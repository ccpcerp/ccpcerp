# Copyright (c) 2026, CCPC and contributors
"""Offline wiring tests for the Phase 1 release. No Frappe import or SQL.

These checks prove that the release restrictions remain present at their
expected entry points. They are not functional ledger or HTTP acceptance.
"""
import ast
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent
ISSUE = BASE / "withdrawal_slip_stock.py"
BILLING = BASE / "doctype/intercompany_material_billing_batch/intercompany_material_billing_batch.py"


def _functions_in(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _named_function(path, name):
    matches = [fn for fn in _functions_in(path) if fn.name == name]
    if len(matches) != 1:
        raise AssertionError(f"Expected exactly one {name}, found {len(matches)}")
    return matches[0]


def _calls(fn, name):
    return [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == name
    ]


class TestPhase1ReleaseWiring(unittest.TestCase):
    def test_stockroom_issuance_has_server_scope_check(self):
        fn = _named_function(ISSUE, "_get_issuable_slip")
        self.assertEqual(len(_calls(fn, "_assert_phase1_same_company")), 1)

    def test_direct_linked_stock_entry_has_server_scope_check(self):
        fn = _named_function(ISSUE, "validate_withdrawal_slip_stock_entry")
        self.assertEqual(len(_calls(fn, "_assert_phase1_same_company")), 1)

    def test_locked_issuer_snapshot_includes_requesting_company(self):
        lock = _named_function(ISSUE, "_lock_withdrawal_slip")
        self.assertTrue(any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "SELECT status, received_by, company, requesting_company," in node.value
            for node in ast.walk(lock)
        ))
        issue_source = ISSUE.read_text(encoding="utf-8")
        self.assertEqual(
            issue_source.count('"company", "requesting_company", "source_warehouse"'), 2
        )

    def test_billing_mutators_fail_closed_before_any_other_work(self):
        for name in (
            "on_trash", "before_validate", "validate",
            "refresh_preview", "finalize_month", "approve_and_create_invoices",
        ):
            with self.subTest(method=name):
                fn = _named_function(BILLING, name)
                # Decorators/DocPerm do not substitute for a server-side gate.
                self.assertTrue(fn.body, name)
                first = fn.body[0]
                self.assertIsInstance(first, ast.Expr, name)
                self.assertIsInstance(first.value, ast.Call, name)
                self.assertIsInstance(first.value.func, ast.Name, name)
                self.assertEqual(first.value.func.id, "_assert_billing_not_released", name)


    def test_all_whitelisted_billing_entrypoints_are_guarded(self):
        """Catch new PR33-style endpoints, not only the six known mutators."""
        tree = ast.parse(BILLING.read_text(encoding="utf-8"), filename=str(BILLING))
        classes = [
            n for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "IntercompanyMaterialBillingBatch"
        ]
        self.assertEqual(len(classes), 1)
        whitelisted = []
        for fn in classes[0].body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "frappe"
                and dec.func.attr == "whitelist"
                for dec in fn.decorator_list
            ):
                continue
            whitelisted.append(fn.name)
            self.assertTrue(fn.body, fn.name)
            first = fn.body[0]
            self.assertIsInstance(first, ast.Expr, fn.name)
            self.assertIsInstance(first.value, ast.Call, fn.name)
            self.assertIsInstance(first.value.func, ast.Name, fn.name)
            self.assertEqual(first.value.func.id, "_assert_billing_not_released", fn.name)
        self.assertTrue(
            {"refresh_preview", "finalize_month", "approve_and_create_invoices"}
            <= set(whitelisted),
            "Expected all three billing endpoints to remain protected",
        )

    def test_finance_role_bootstrap_survives_pr33_reconciliation(self):
        """Do not replace the Phase 1 hook with PR33's billing bootstrap."""
        hooks = BASE.parent / "hooks.py"
        tree = ast.parse(hooks.read_text(encoding="utf-8"), filename=str(hooks))
        assignments = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments[target.id] = node.value
        before_install = assignments.get("before_install")
        self.assertIsInstance(before_install, ast.List)
        role_hooks = [
            element.value for element in before_install.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        self.assertEqual(len(role_hooks), len(before_install.elts))
        self.assertEqual(
            set(role_hooks),
            {
                "ccpcerp.setup.ensure_ccpc_group_executive_reporting_role",
                "ccpcerp.setup.ensure_bc_finance_mapping_roles",
            },
        )
        before_migrate = assignments.get("before_migrate")
        self.assertIsInstance(before_migrate, ast.Call)
        self.assertIsInstance(before_migrate.func, ast.Attribute)
        self.assertEqual(before_migrate.func.attr, "copy")
        self.assertIsInstance(before_migrate.func.value, ast.Name)
        self.assertEqual(before_migrate.func.value.id, "before_install")
        self.assertFalse(before_migrate.args or before_migrate.keywords)
        self.assertIsInstance(assignments.get("after_migrate"), ast.Constant)
        self.assertEqual(
            assignments["after_migrate"].value,
            "ccpcerp.setup.after_migrate",
        )


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

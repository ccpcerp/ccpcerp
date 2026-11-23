# Copyright (c) 2026, CCPC and contributors
"""Offline wiring checks; these do not replace ERPNext/MariaDB ledger tests."""
import ast
import json
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent
APP = BASE.parent


def _named(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = [
        fn for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and fn.name == name
    ]
    if len(found) != 1:
        raise AssertionError("Expected one " + name)
    return found[0]


def _calls(fn, function):
    return [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == function
    ]


class TestPhase1MaintenanceWiring(unittest.TestCase):
    def test_structured_classification_field_is_explicit_and_not_defaulted(self):
        path = BASE / "doctype/withdrawal_slip/withdrawal_slip.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        fields = [f for f in doc["fields"] if f.get("fieldname") == "ccpc_material_use"]
        self.assertEqual(len(fields), 1)
        field = fields[0]
        self.assertEqual(field["fieldtype"], "Select")
        self.assertNotIn("default", field)
        self.assertIn("Routine Maintenance", field["options"].split("\n"))
        self.assertIn("Capital Project / Improvement", field["options"].split("\n"))
        self.assertIn("Other / Unclassified", field["options"].split("\n"))
        self.assertIn("ccpc_material_use", doc["field_order"])

    def test_classification_is_read_only_after_approval_in_browser(self):
        source = (BASE / "doctype/withdrawal_slip/withdrawal_slip.js").read_text(encoding="utf-8")
        request_fields = source.split("const request_intent_fields = [", 1)[1].split("];", 1)[0]
        self.assertIn('"ccpc_material_use"', request_fields)
        self.assertIn(
            'frm.set_df_property(fieldname, "read_only", request_editable ? 0 : 1)',
            source,
        )
        self.assertIn('state !== "Returned for Correction"', source)

    def test_request_intent_cannot_be_changed_after_approval(self):
        module = ast.parse(
            (BASE / "doctype/withdrawal_slip/withdrawal_slip.py").read_text(encoding="utf-8")
        )
        declared = [
            node for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "REQUEST_INTENT_HEADER_FIELDS"
                    for t in node.targets)
        ]
        self.assertEqual(len(declared), 1)
        self.assertIn("ccpc_material_use", ast.literal_eval(declared[0].value))

    def test_locked_slip_header_and_stale_snapshot_cover_material_use(self):
        path = BASE / "withdrawal_slip_stock.py"
        fn = _named(path, "_lock_withdrawal_slip")
        self.assertTrue(any(
            isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "ccpc_material_use" in node.value
            and "FOR UPDATE" in node.value
            for node in ast.walk(fn)
        ))
        for name in ("_get_issuable_slip", "validate_withdrawal_slip_stock_entry"):
            fn = _named(path, name)
            self.assertTrue(any(
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value == "ccpc_material_use"
                for node in ast.walk(fn)
            ), name)

    def test_locked_group_gate_runs_for_creation_and_before_submit(self):
        path = BASE / "bc_issue_runtime.py"
        resolver = _named(path, "resolve_locked_original_issue")
        self.assertEqual(len(_calls(resolver, "require_maintenance_pilot_use")), 1)
        for name in ("assign_original_issue_b_c", "validate_original_issue_b_c"):
            fn = _named(path, name)
            self.assertEqual(len(_calls(fn, "resolve_locked_original_issue")), 1, name)

    def test_unlinked_ccpc_bearings_direct_issue_is_rejected(self):
        path = BASE / "withdrawal_slip_stock.py"
        validator = _named(path, "validate_withdrawal_slip_stock_entry")
        self.assertEqual(len(_calls(validator, "_reject_unlinked_pilot_bearings_issue")), 1)
        check = _named(path, "_reject_unlinked_pilot_bearings_issue")
        source = ast.get_source_segment(path.read_text(encoding="utf-8"),
                                        check) or ""
        for evidence in ("Material Issue", "PILOT_COMPANY", "PILOT_ITEM_GROUP",
                         "tabItem", "FOR UPDATE", "frappe.throw"):
            self.assertIn(evidence, source)

    def test_stock_entry_validation_hooks_run_at_submit(self):
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('"before_submit": [', hooks)
        self.assertIn(
            '"ccpcerp.ccpc_erp.withdrawal_slip_stock.validate_withdrawal_slip_stock_entry"',
            hooks,
        )


if __name__ == "__main__":
    unittest.main()

"""Static non-database security regression checks for the Finance lifecycle branch.

These tests DO NOT create users, mappings, ledger records, or site migrations.
Real Frappe/MariaDB lock and transaction tests are a separate acceptance gate.
"""
import ast
import json
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent
NEW = (
    "ccpc_accounting_mapping_decision",
    "ccpc_mapping_decision_use",
    "ccpc_mapping_rejection_event",
    "ccpc_mapping_retirement_request",
    "ccpc_mapping_retirement_event",
)


class FinanceLifecycleStaticTests(unittest.TestCase):
    def test_new_schema_audit_and_write_guards(self):
        for name in NEW:
            with self.subTest(name=name):
                folder = HERE / "doctype" / name
                data = json.loads((folder / (name + ".json")).read_text())
                fields = {f["fieldname"]: f for f in data["fields"]}
                self.assertEqual(data["module"], "CCPC ERP")
                self.assertEqual(data["permissions"], [])
                self.assertEqual(data["field_order"], list(fields))
                self.assertEqual(data["track_changes"], 1)
                self.assertEqual(data["autoname"], "hash")
                self.assertEqual(data["engine"], "InnoDB")
                for f in fields.values():
                    if f.get("unique"):
                        self.assertEqual(f.get("read_only"), 1)
                src = (folder / (name + ".py")).read_text()
                tree = ast.parse(src)
                self.assertIn("ccpc_finance_event_writer", src)
                self.assertIn("on_trash", src)
                self.assertIn("before_rename", src)
                self.assertIn("immutable", src)
                self.assertEqual(len([n for n in tree.body if isinstance(n, ast.ClassDef)]), 1)

    def test_bound_and_single_use_decisions(self):
        evidence = (HERE / "bc_finance_evidence.py").read_text()
        tree = ast.parse(evidence)
        names = {x.name for x in tree.body if isinstance(x, ast.FunctionDef)}
        self.assertTrue({
            "sign_mapping_decision", "verify_decision", "consume_decision",
            "snapshot", "approved_hash", "event_insert",
        } <= names)
        self.assertIn('methods=["POST"]', evidence)
        self.assertIn("assert_independent_review", evidence)
        self.assertIn('action in ("Approve", "Retire")', evidence)
        self.assertIn('frappe.db.exists(USE, {"decision": doc.name})', evidence)
        use_schema = json.loads((
            HERE / "doctype/ccpc_mapping_decision_use/ccpc_mapping_decision_use.json"
        ).read_text())
        field = {x["fieldname"]: x for x in use_schema["fields"]}
        self.assertEqual(field["decision"]["unique"], 1)
        decision_schema = json.loads((
            HERE / "doctype/ccpc_accounting_mapping_decision/ccpc_accounting_mapping_decision.json"
        ).read_text())
        self.assertIn("status", decision_schema["field_order"])
        self.assertIn('doc.status == "Signed"', evidence)

    def test_terminal_rejection_and_event_cutoff(self):
        events = (HERE / "bc_finance_lifecycle_events.py").read_text()
        runtime = (HERE / "bc_issue_runtime.py").read_text()
        ast.parse(events)
        ast.parse(runtime)
        self.assertIn('approval_state=\'Rejected\'', events)
        self.assertIn('request_state=\'Accepted\'', events)
        self.assertIn("verify_decision", events)
        self.assertIn("ccpc_retirement_accept_or_decline", events)
        self.assertIn("def get_retirement_request_review(", events)
        self.assertIn("Retirement request does not match its original Approved mapping.", events)
        self.assertIn('"modified": str(req.modified)', events)
        self.assertIn("_effective_mapping_end(", runtime)
        self.assertIn("computed_end", runtime)
        self.assertIn("cutoff >= _utc(e.approved_at)", runtime)
        self.assertIn("is_effective_after_approval(", runtime)
        self.assertNotIn('_real_authority(maker, "CCPC Mapping Preparer", company)\n    _real_authority(checker', runtime)

    def test_existing_approval_requires_internal_decision(self):
        workflow = (HERE / "bc_mapping_workflow.py").read_text()
        ast.parse(workflow)
        self.assertIn("verify_decision(approval_reference", workflow)
        self.assertIn("consume_decision(signed", workflow)
        self.assertIn("One unresolved Draft", workflow)
        for slug in ("ccpc_expense_mapping_revision", "ccpc_center_mapping_revision"):
            schema = json.loads((
                HERE / "doctype" / slug / (slug + ".json")
            ).read_text())
            status = next(x for x in schema["fields"]
                          if x["fieldname"] == "approval_state")
            self.assertIn("Rejected", status["options"].split("\n"))
            for required in ("rejected_by", "rejected_at",
                             "rejection_reason", "rejection_event"):
                self.assertIn(required, schema["field_order"])
        legacy = (HERE / "bc_mapping_lifecycle.py").read_text()
        self.assertIn("Legacy mutable cutoff approval disabled", legacy)
        self.assertIn("Legacy retirement endpoint disabled", legacy)

    def test_request_replay_and_atomic_audit_guards(self):
        evidence = (HERE / "bc_finance_evidence.py").read_text()
        lifecycle = (HERE / "bc_finance_lifecycle_events.py").read_text()
        self.assertIn("ccpc_signed_finance_decision", evidence)
        self.assertIn("frappe.db.rollback(save_point=marker)", evidence)
        self.assertIn("ccpc_finance_retirement_request", lifecycle)
        self.assertIn('"reused": True', lifecycle)
        self.assertIn("previous.request_key == request_key", lifecycle)
        self.assertIn("Different retirement request already pending", lifecycle)
        self.assertGreaterEqual(lifecycle.count("frappe.db.rollback(save_point=marker)"), 3)

    def test_no_automatic_admin_or_transaction_write(self):
        for name in ("bc_finance_evidence.py", "bc_finance_lifecycle_events.py"):
            with self.subTest(name=name):
                src = (HERE / name).read_text()
                self.assertNotIn("frappe.db.commit()", src)
                self.assertNotIn("frappe.set_user(", src)
                self.assertNotIn("Administrator", src)
                self.assertNotIn("Stock Entry", src)
                self.assertNotIn("GL Entry", src)


if __name__ == "__main__":
    unittest.main()

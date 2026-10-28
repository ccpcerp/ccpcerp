# Copyright (c) 2026, CCPC and contributors
"""Disposable-only Frappe v16 / MariaDB Finance lifecycle acceptance probes.

This module is NOT an authenticated HTTP/CSRF or real ledger posting test.
It executes real Frappe workflow methods, real InnoDB transactions/DocTypes,
and independent Maker/Checker role and Company permission checks against an
entirely synthetic test site. Never run against persistent DEVELOPMENT/PROD.
"""
from unittest.mock import patch

import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import now_datetime

from ccpcerp.ccpc_erp.ci_finance_lifecycle_site_guard import (
    assert_finance_disposable_site,
)
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
from ccpcerp.ccpc_erp.bc_mapping_workflow import (
    approve_mapping, get_mapping_review, propose_mapping,
)
from ccpcerp.ccpc_erp.bc_finance_evidence import sign_mapping_decision
from ccpcerp.ccpc_erp.bc_finance_lifecycle_events import (
    decide_mapping_retirement,
    get_retirement_request_review,
    reject_mapping,
    request_mapping_retirement,
)

COMPANY = "_Test Company"
MAKER = "ccpc-finance-ci-maker@invalid.test"
CHECKER = "ccpc-finance-ci-checker@invalid.test"
REV = "CCPC Expense Mapping Revision"
SCOPE = "CCPC Expense Mapping Scope"
DECISION = "CCPC Accounting Mapping Decision"
USE = "CCPC Mapping Decision Use"
REJECT = "CCPC Mapping Rejection Event"
RETIRE_REQ = "CCPC Mapping Retirement Request"
RETIRE_EVENT = "CCPC Mapping Retirement Event"


class TestFinanceLifecycleDisposableIntegration(ERPNextTestSuite):
    def setUp(self):
        # This is FIRST: no fixtures, database writes or role changes before guard.
        assert_finance_disposable_site(getattr(frappe.local, "site", None))
        super().setUp()
        frappe.set_user("Administrator")
        self.load_test_records("Stock Entry")
        self.assertTrue(frappe.db.exists("Company", COMPANY))
        self.assertTrue(frappe.db.exists("Item Group", "All Item Groups"))
        self.account = "Stock Adjustment - _TC"
        self.assertTrue(frappe.db.exists("Account", self.account))
        self.assertEqual(frappe.db.get_value("Account", self.account, "company"), COMPANY)
        self.assertEqual(frappe.db.get_value("Account", self.account, "root_type"), "Expense")
        self.group = "CCPC Finance CI " + self._testMethodName.replace("test_", "")
        self.assertFalse(frappe.db.exists("Item Group", self.group),
                         "Expected pristine, disposable source group.")
        frappe.get_doc({
            "doctype": "Item Group", "item_group_name": self.group,
            "parent_item_group": "All Item Groups", "is_group": 0,
        }).insert(ignore_permissions=True)
        self._actor(MAKER, "CCPC Mapping Preparer")
        self._actor(CHECKER, "CCPC Accounting Mapping Approver")
        for name in (DECISION, USE, REJECT, RETIRE_REQ, RETIRE_EVENT):
            self.assertTrue(frappe.db.table_exists("tab" + name), name)
            self.assertEqual(frappe.get_meta(name).permissions, [])

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def _actor(self, email, role):
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role}).insert(
                ignore_permissions=True)
        if not frappe.db.exists("User", email):
            frappe.get_doc({
                "doctype": "User", "email": email,
                "first_name": "Finance Disposable CI",
                "enabled": 1, "user_type": "System User",
                "send_welcome_email": 0,
            }).insert(ignore_permissions=True)
        user = frappe.get_doc("User", email)
        self.assertEqual(int(user.enabled), 1)
        self.assertFalse(any(r.role == "Stockroom Personnel" for r in user.roles))
        if not any(r.role == role for r in user.roles):
            user.append("roles", {"role": role})
            user.save(ignore_permissions=True)
        if not frappe.db.exists("User Permission", {
            "user": email, "allow": "Company", "for_value": COMPANY,
            "apply_to_all_doctypes": 1,
        }):
            frappe.get_doc({
                "doctype": "User Permission", "user": email,
                "allow": "Company", "for_value": COMPANY,
                "apply_to_all_doctypes": 1,
            }).insert(ignore_permissions=True)

    def _draft(self):
        frappe.set_user(MAKER)
        draft = propose_mapping("Expense", COMPANY, self.group, self.account)
        self.assertEqual(draft["state"], "Draft")
        self.assertEqual(frappe.db.get_value(REV, draft["name"], "approval_state"), "Draft")
        return draft["name"]

    def _approve(self):
        name = self._draft()
        frappe.set_user(CHECKER)
        reviewed = get_mapping_review("Expense", name)
        self.assertEqual(reviewed["owner"], MAKER)
        with self.assertRaises(MappingPolicyError):
            approve_mapping("Expense", name, reviewed["modified"], "INVENTED REFERENCE")
        signed = sign_mapping_decision(
            "Expense", name, reviewed["modified"], "Approve"
        )
        self.assertEqual(signed["state"], "Signed")
        self.assertEqual(frappe.db.count(USE, {"decision": signed["name"]}), 0,
                         "Merely signing cannot activate the mapping.")
        approved = approve_mapping(
            "Expense", name, reviewed["modified"], signed["name"]
        )
        self.assertEqual(approved["state"], "Approved")
        self.assertEqual(approved["approved_by"], CHECKER)
        self.assertEqual(frappe.db.count(USE, {"decision": signed["name"]}), 1)
        self.assertTrue(frappe.db.exists("Version", approved["version"]))
        self.assertEqual(frappe.db.get_value(REV, name, "approval_reference"), signed["name"])
        return name, signed["name"]

    def test_a_schema_has_required_lifecycle_fields_and_no_general_permissions(self):
        for doctype in ("CCPC Expense Mapping Revision", "CCPC Center Mapping Revision"):
            for field in ("approval_state", "rejected_by", "rejected_at",
                          "rejection_reason", "rejection_event"):
                self.assertTrue(frappe.get_meta(doctype).has_field(field),
                                doctype + "." + field)
        self.assertTrue(frappe.get_meta(DECISION).has_field("status"))
        self.assertTrue(frappe.get_meta(USE).has_field("decision"))
        self.assertEqual(frappe.db.count(RETIRE_EVENT), 0)
        self.assertEqual(frappe.db.count("GL Entry", {"account": self.account,
                         "voucher_type": "CCPC Mapping Retirement Event"}), 0)

    def test_b_draft_rejection_is_terminal_and_audited(self):
        revision = self._draft()
        frappe.set_user(CHECKER)
        reviewed = get_mapping_review("Expense", revision)
        rejected = reject_mapping("Expense", revision, reviewed["modified"],
                                  "Incorrect test-only mapping")
        self.assertEqual(rejected["state"], "Rejected")
        self.assertEqual(frappe.db.get_value(REV, revision, "approval_state"), "Rejected")
        self.assertEqual(frappe.db.get_value(REV, revision, "rejected_by"), CHECKER)
        self.assertEqual(frappe.db.count(REJECT, {"revision": revision}), 1)
        self.assertTrue(frappe.db.exists("Version", rejected["version"]))
        with self.assertRaises(MappingPolicyError):
            reject_mapping("Expense", revision, reviewed["modified"], "REPLAY")
        with self.assertRaises(MappingPolicyError):
            sign_mapping_decision("Expense", revision, reviewed["modified"], "Approve")
        self.assertEqual(frappe.db.count(USE, {"revision": revision}), 0)

    def test_c_signed_approval_is_once_only_and_denies_forged_reference(self):
        revision = self._draft()
        frappe.set_user(MAKER)
        with self.assertRaises(MappingPolicyError):
            sign_mapping_decision("Expense", revision, "forged", "Approve")
        frappe.set_user(CHECKER)
        reviewed = get_mapping_review("Expense", revision)
        with self.assertRaises(MappingPolicyError):
            sign_mapping_decision("Expense", revision, "stale token", "Approve")
        signed = sign_mapping_decision("Expense", revision, reviewed["modified"], "Approve")
        reused = sign_mapping_decision("Expense", revision, reviewed["modified"], "Approve")
        self.assertEqual(reused["name"], signed["name"])
        self.assertTrue(reused["reused"])
        approved = approve_mapping("Expense", revision, reviewed["modified"], signed["name"])
        self.assertEqual(approved["state"], "Approved")
        self.assertEqual(frappe.db.count(USE, {"decision": signed["name"]}), 1)
        with self.assertRaises(MappingPolicyError):
            approve_mapping("Expense", revision, reviewed["modified"], signed["name"])
        with self.assertRaises(MappingPolicyError):
            sign_mapping_decision("Expense", revision, reviewed["modified"], "Approve")

    def test_d_signing_version_failure_rolls_back_decision(self):
        revision = self._draft()
        frappe.set_user(CHECKER)
        reviewed = get_mapping_review("Expense", revision)
        before = frappe.db.count(DECISION, {"revision": revision})
        with patch(
            "ccpcerp.ccpc_erp.bc_finance_evidence.audit",
            side_effect=RuntimeError("synthetic disposable Version failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic disposable Version failure"):
                sign_mapping_decision("Expense", revision, reviewed["modified"], "Approve")
        self.assertEqual(frappe.db.count(DECISION, {"revision": revision}), before)
        self.assertEqual(frappe.db.count(USE, {"revision": revision}), 0)
        self.assertEqual(frappe.db.get_value(REV, revision, "approval_state"), "Draft")

    def test_e_retirement_request_replay_decline_and_accept_are_audited(self):
        revision, approval_decision = self._approve()
        original = frappe.db.get_value(REV, revision,
                                      ["approval_state", "effective_from",
                                       "effective_until", "approval_reference"],
                                      as_dict=True)
        frappe.set_user(MAKER)
        first = request_mapping_retirement("Expense", revision,
                                           "End synthetic Finance mapping")
        self.assertEqual(first["state"], "Pending")
        replay = request_mapping_retirement("Expense", revision,
                                            "End synthetic Finance mapping")
        self.assertTrue(replay["reused"])
        self.assertEqual(replay["name"], first["name"])
        with self.assertRaises(MappingPolicyError):
            request_mapping_retirement("Expense", revision, "Changed reason")
        frappe.set_user(CHECKER)
        reviewed = get_retirement_request_review("Expense", first["name"])
        self.assertEqual(reviewed["state"], "Pending")
        declined = decide_mapping_retirement(
            "Expense", first["name"], reviewed["modified"],
            "Decline", decline_reason="Not warranted yet",
        )
        self.assertEqual(declined["state"], "Declined")
        self.assertEqual(frappe.db.count(RETIRE_EVENT, {"revision": revision}), 0)
        frappe.set_user(MAKER)
        second = request_mapping_retirement("Expense", revision,
                                            "New synthetic retirement rationale")
        frappe.set_user(CHECKER)
        reviewed2 = get_retirement_request_review("Expense", second["name"])
        signed = sign_mapping_decision(
            "Expense", revision,
            str(frappe.db.get_value(REV, revision, "modified")),
            "Retire", request_name=second["name"],
        )
        accepted = decide_mapping_retirement(
            "Expense", second["name"], reviewed2["modified"],
            "Accept", decision_id=signed["name"],
        )
        self.assertEqual(accepted["state"], "Approved")
        self.assertEqual(frappe.db.count(RETIRE_EVENT, {"revision": revision}), 1)
        self.assertEqual(frappe.db.count(USE, {"decision": signed["name"]}), 1)
        self.assertEqual(frappe.db.count(USE, {"decision": approval_decision}), 1)
        self.assertTrue(frappe.db.exists("Version", accepted["version"]))
        after = frappe.db.get_value(REV, revision,
                                    ["approval_state", "effective_from",
                                     "effective_until", "approval_reference"],
                                    as_dict=True)
        self.assertEqual(dict(after), dict(original),
                         "Retirement must not rewrite the historically Approved revision.")
        with self.assertRaises(MappingPolicyError):
            decide_mapping_retirement(
                "Expense", second["name"], reviewed2["modified"],
                "Accept", decision_id=signed["name"],
            )
        self.assertIsNone(original.effective_until)

    def test_f_unauthorized_guest_and_administrator_fail_closed(self):
        revision = self._draft()
        reviewed = None
        for actor in ("Guest", "Administrator"):
            frappe.set_user(actor)
            with self.subTest(actor=actor):
                with self.assertRaises(MappingPolicyError):
                    reviewed = get_mapping_review("Expense", revision)
                with self.assertRaises(MappingPolicyError):
                    sign_mapping_decision("Expense", revision, "forged", "Approve")
        frappe.set_user(MAKER)
        with self.assertRaises(MappingPolicyError):
            reject_mapping("Expense", revision, "forged", "self approval")
        self.assertEqual(frappe.db.get_value(REV, revision, "approval_state"), "Draft")

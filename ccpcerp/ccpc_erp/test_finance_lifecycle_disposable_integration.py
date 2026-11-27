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
from ccpcerp.ccpc_erp.ci_stockroom_center_pr45_site_guard import (
    assert_stockroom_pr45_disposable_site,
    is_reviewed_stockroom_pr45_disposable_site,
)
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
from ccpcerp.ccpc_erp.bc_issue_runtime import _effective_mapping_end, _utc
from ccpcerp.ccpc_erp.bc_mapping_workflow import (
    approve_mapping, get_mapping_review, propose_mapping,
)
from ccpcerp.ccpc_erp.bc_finance_evidence import sign_mapping_decision
from ccpcerp.ccpc_erp.phase1_finance_onboarding import (
    get_pilot_context, get_pilot_coverage, propose_pilot_mapping,
    search_pilot_master, sign_and_approve_initial_mapping,
)
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
        # FIRST, before fixtures or record writes: preserve the original
        # CircleCI Finance gate. The sole Stockroom Center test additionally
        # accepts its separate exact GitHub PR45 disposable event/site guard.
        site = getattr(frappe.local, "site", None)
        if (self._testMethodName
                == "test_i_stockroom_center_draft_only_and_independent_finance_approval"
                and is_reviewed_stockroom_pr45_disposable_site(site)):
            assert_stockroom_pr45_disposable_site(site)
        else:
            assert_finance_disposable_site(site)
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
            self.assertTrue(frappe.db.table_exists(name), name)
            permissions = frappe.get_meta(name).permissions
            # Retain the strict zero-permission gate. If Frappe has added a
            # DocPerm during install/test setup, show only the role and
            # permission bits, never user identities or document records.
            details = [
                {key: row.get(key) for key in (
                    "role", "read", "write", "create", "delete",
                    "submit", "cancel", "amend", "export", "report", "share",
                )}
                for row in permissions
            ]
            self.assertEqual(
                permissions, [],
                f"Unexpected effective DocPerm on protected {name}: {details}",
            )

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
        scope_id = frappe.db.get_value(REV, revision, "mapping_scope")
        locked_row = frappe.db.get_value(REV, revision,
            ["name", "effective_from", "effective_until"], as_dict=True)
        effective_end = _effective_mapping_end(
            "Expense", COMPANY, scope_id, locked_row
        )
        self.assertEqual(effective_end, _utc(accepted["cutoff_at"]))


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

    def test_g_center_approval_uses_exact_company_department(self):
        name = "CCPC CI Finance Center " + self._testMethodName.split("test_g_")[1]
        self.assertFalse(frappe.db.exists("Department", {
            "department_name": name, "company": COMPANY,
        }))
        dept = frappe.get_doc({
            "doctype": "Department", "department_name": name,
            "company": COMPANY, "is_group": 0,
        }).insert(ignore_permissions=True).name
        center = frappe.db.get_value("Cost Center", {
            "company": COMPANY, "is_group": 0, "disabled": 0,
        }, "name")
        self.assertTrue(center, "Expected disposable fixture's leaf Cost Center")
        frappe.set_user(MAKER)
        draft = propose_mapping("Center", COMPANY, dept, center)
        self.assertEqual(draft["state"], "Draft")
        frappe.set_user(CHECKER)
        reviewed = get_mapping_review("Center", draft["name"])
        self.assertEqual(reviewed["source"], dept)
        self.assertEqual(reviewed["value"], center)
        signed = sign_mapping_decision(
            "Center", draft["name"], reviewed["modified"], "Approve"
        )
        approved = approve_mapping(
            "Center", draft["name"], reviewed["modified"], signed["name"]
        )
        self.assertEqual(approved["state"], "Approved")
        self.assertEqual(frappe.db.count(USE, {"decision": signed["name"]}), 1)


    def test_i_stockroom_center_draft_only_and_independent_finance_approval(self):
        """Real disposable SQL/API test. Scoped constants are patched only in CI."""
        from ccpcerp.ccpc_erp.finance_doctype_security import PROTECTED_FINANCE_DOCTYPES

        # Validate actual post-install/post-migrate Frappe metadata, not only
        # the committed JSON. No System Manager/Custom DocPerm may survive.
        self.assertEqual(len(PROTECTED_FINANCE_DOCTYPES), 9)
        for protected in PROTECTED_FINANCE_DOCTYPES:
            with self.subTest(protected=protected):
                self.assertEqual(frappe.get_meta(protected).permissions, [])
                self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": protected}))

        stockroom = "ccpc-finance-ci-stockroom-maker@invalid.test"
        self._actor(stockroom, "CCPC Mapping Preparer")
        if not frappe.db.exists("Role", "Stockroom Personnel"):
            frappe.get_doc({
                "doctype": "Role", "role_name": "Stockroom Personnel",
            }).insert(ignore_permissions=True)
        user = frappe.get_doc("User", stockroom)
        user.append("roles", {"role": "Stockroom Personnel"})
        user.save(ignore_permissions=True)

        dept = frappe.get_doc({
            "doctype": "Department",
            "department_name": "CCPC Finance CI Stockroom " + self._testMethodName,
            "company": COMPANY, "is_group": 0,
        }).insert(ignore_permissions=True).name
        center = frappe.db.get_value("Cost Center", {
            "company": COMPANY, "is_group": 0, "disabled": 0,
        }, "name")
        self.assertTrue(center)
        before_sle = frappe.db.count("Stock Ledger Entry", {"company": COMPANY})
        before_gl = frappe.db.count("GL Entry", {"company": COMPANY})
        from ccpcerp.ccpc_erp import bc_issue_runtime, phase1_finance_onboarding

        # The permanent policy remains restricted to CCPC / Main - CCPC.
        # Synthetic fixture _Test Company and its leaf are substituted ONLY
        # inside this disposable test's context, not the actual application.
        # The onboarding module imports these constants by value.
        with (
            patch.object(bc_issue_runtime, "STOCKROOM_CENTER_PROPOSAL_COMPANY", COMPANY),
            patch.object(bc_issue_runtime, "STOCKROOM_CENTER_PROPOSAL_VALUE", center),
            patch.object(phase1_finance_onboarding, "STOCKROOM_CENTER_PROPOSAL_COMPANY", COMPANY),
            patch.object(phase1_finance_onboarding, "STOCKROOM_CENTER_PROPOSAL_VALUE", center),
        ):
            frappe.set_user(stockroom)
            access = get_pilot_context()
            self.assertIn(COMPANY, access["companies"])
            self.assertEqual(access["allowed_kinds"], ["Center"])
            self.assertTrue(access["can_propose"])
            self.assertFalse(access["can_review"])
            self.assertIn(center, search_pilot_master(
                "Center", COMPANY, "value", "",
            ))
            with self.assertRaises(MappingPolicyError):
                propose_mapping("Expense", COMPANY, self.group, self.account)
            with self.assertRaises(frappe.ValidationError):
                propose_pilot_mapping("Expense", COMPANY, self.group, self.account)
            with self.assertRaises(MappingPolicyError):
                propose_mapping("Center", COMPANY, dept, "NOT THE SHARED CENTER")
            with self.assertRaises(frappe.ValidationError):
                get_pilot_coverage("Expense", COMPANY, self.group)

            coverage = get_pilot_coverage("Center", COMPANY, dept)
            self.assertFalse(coverage["active"])
            self.assertFalse(coverage["draft"])
            draft = propose_pilot_mapping("Center", COMPANY, dept, center)
            self.assertEqual(draft["state"], "Draft")
            revision = draft["name"]
            self.assertEqual(frappe.db.get_value(
                "CCPC Center Mapping Revision", revision, "approval_state"), "Draft")
            scope = frappe.db.get_value("CCPC Center Mapping Revision", revision,
                                        "mapping_scope")
            selector = frappe.db.get_value("CCPC Center Mapping Scope", scope,
                                           ["selector_kind", "selector_key"],
                                           as_dict=True)
            self.assertEqual(selector.selector_kind, "Department Only")
            self.assertEqual(selector.selector_key, "DEPARTMENT_ONLY")
            with self.assertRaises(frappe.ValidationError):
                sign_and_approve_initial_mapping("Center", revision, "forged")

            frappe.set_user(CHECKER)
            reviewed = get_mapping_review("Center", revision)
            self.assertEqual(reviewed["owner"], stockroom)
            self.assertEqual(reviewed["value"], center)
            approval = sign_and_approve_initial_mapping(
                "Center", revision, reviewed["modified"],
            )
            self.assertEqual(approval["state"], "Approved")
            self.assertEqual(frappe.db.count(USE, {
                "decision": frappe.db.get_value(
                    "CCPC Center Mapping Revision", revision, "approval_reference"),
            }), 1)
            self.assertTrue(frappe.db.exists("Version", approval["version"]))
            self.assertEqual(frappe.db.count("Stock Ledger Entry", {
                "company": COMPANY,
            }), before_sle)
            self.assertEqual(frappe.db.count("GL Entry", {
                "company": COMPANY,
            }), before_gl)

            frappe.set_user(stockroom)
            active = get_pilot_coverage("Center", COMPANY, dept)
            self.assertEqual(active["active"]["value"], center)
            self.assertEqual(active["active"]["revision"], revision)
            self.assertFalse(active["draft"])

            # A real System Manager must have no ordinary DocType/document
            # access to signed decisions, scopes, revisions or audit events.
            # The Controller's authorized API is the ONLY review path.
            system_manager = "ccpc-finance-ci-system-manager@invalid.test"
            frappe.set_user("Administrator")
            self._actor(system_manager, "System Manager")
            frappe.set_user(system_manager)
            for protected in PROTECTED_FINANCE_DOCTYPES:
                with self.subTest(system_manager_denied=protected):
                    for ptype in ("read", "write", "create", "delete",
                                  "export", "report", "share"):
                        self.assertFalse(frappe.has_permission(
                            protected, ptype=ptype, user=system_manager,
                        ), protected + ":" + ptype)
                    try:
                        listed = frappe.get_list(protected, fields=["name"], limit=3)
                    except frappe.PermissionError:
                        pass
                    else:
                        self.assertEqual(listed, [])
            decision = frappe.db.get_value(
                "CCPC Center Mapping Revision", revision, "approval_reference",
            )
            event_doc = frappe.get_doc(DECISION, decision)
            self.assertFalse(frappe.has_permission(
                DECISION, ptype="read", doc=event_doc, user=system_manager,
            ))
            self.assertFalse(frappe.has_permission(
                "CCPC Center Mapping Revision", ptype="read",
                doc=frappe.get_doc("CCPC Center Mapping Revision", revision),
                user=system_manager,
            ))

            # Having both Stockroom and Checker roles does NOT create a shortcut.
            frappe.set_user("Administrator")
            user = frappe.get_doc("User", stockroom)
            user.append("roles", {"role": "CCPC Accounting Mapping Approver"})
            user.save(ignore_permissions=True)
            frappe.set_user(stockroom)
            with self.assertRaises(frappe.ValidationError):
                get_pilot_context()
            with self.assertRaises(MappingPolicyError):
                propose_mapping("Center", COMPANY, dept, center)

    def test_h_rejection_audit_failure_rolls_back_event_and_state(self):
        revision = self._draft()
        frappe.set_user(CHECKER)
        reviewed = get_mapping_review("Expense", revision)
        with patch(
            "ccpcerp.ccpc_erp.bc_finance_lifecycle_events.audit",
            side_effect=RuntimeError("synthetic rejection audit failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic rejection audit failure"):
                reject_mapping("Expense", revision, reviewed["modified"],
                               "Test-only invalid mapping")
        self.assertEqual(frappe.db.get_value(REV, revision, "approval_state"), "Draft")
        self.assertFalse(frappe.db.get_value(REV, revision, "rejected_by"))
        self.assertEqual(frappe.db.count(REJECT, {"revision": revision}), 0)

# Copyright (c) 2026, CCPC and contributors
"""DISPOSABLE real Frappe/MariaDB B/C original stock issue acceptance probe.

Exercises the authenticated proposal, independent reviewer and real
Stock Entry + GL posting against a disposable site. The Company, users, Accounts
and Departments are synthetic ERPNext fixtures, NOT real CCPC finance approvals.
Never run on persistent DEVELOPMENT or PRODUCTION sites.
"""
import os

import frappe
from frappe.utils import flt, now_datetime

from erpnext.tests.utils import ERPNextTestSuite
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
from ccpcerp.ccpc_erp.bc_mapping_workflow import (
    approve_mapping, get_mapping_review, propose_mapping,
)
from ccpcerp.ccpc_erp.doctype.withdrawal_slip import (
    test_withdrawal_real_ledger_integration as _base_real,
)

COMPANY = _base_real.COMPANY
PREPARER = _base_real.PREPARER
ISSUER = _base_real.ISSUER
QTY_ISSUED = _base_real.QTY_ISSUED
UNIT_RATE = _base_real.UNIT_RATE
QTY_RECEIVED = _base_real.QTY_RECEIVED
from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip


APPROVER = "ccpc-bc-ci-approver@invalid.test"
CI_REF = "review/bc-original-issue-allocation-2026-10-20"


class TestBCOriginalIssueRealIntegration(ERPNextTestSuite):
    _bin_qty = _base_real.TestWithdrawalRealLedgerIntegration._bin_qty

    def setUp(self):
        if not (
            os.getenv("GITHUB_ACTIONS") == "true"
            and os.getenv("GITHUB_REPOSITORY") == "ccpcerp/ccpcerp"
            and os.getenv("GITHUB_HEAD_REF") == CI_REF
            and os.getenv("CCPC_ALLOW_BC_POSTING_PROBE") == "isolated-only"
            and getattr(frappe.local, "site", None) == "ci-withdrawal.localhost"
        ):
            raise RuntimeError("B/C posting probe restricted to single disposable GitHub Actions site")
        _base_real.TestWithdrawalRealLedgerIntegration.setUp(self)
        self.assertTrue(frappe.get_meta("Stock Entry Detail").has_field("ccpc_expense_mapping_revision"))
        self.assertTrue(frappe.get_meta("Stock Entry Detail").has_field("ccpc_center_mapping_revision"))
        self.assertTrue(frappe.get_meta("Stock Entry Detail").has_field("ccpc_item_group_at_issue"))

    def tearDown(self):
        frappe.set_user("Administrator")
        ERPNextTestSuite.tearDown(self)

    def test_a_missing_approved_mapping_rejects_without_stock_or_gl(self):
        frappe.set_user(ISSUER)
        before = self._bin_qty()
        with self.assertRaises(MappingPolicyError):
            issue_withdrawal_slip(self.slip_name)
        self.assertAlmostEqual(self._bin_qty(), before)
        self.assertFalse(frappe.db.exists("Stock Entry", {
            "withdrawal_slip": self.slip_name, "docstatus": 1,
        }))
        self.assertEqual(frappe.db.count("GL Entry", {"voucher_type": "Stock Entry",
                         "voucher_no": self.slip_name}), 0)

    def _ensure_user_role(self, username, role):
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)
        if not frappe.db.exists("User", username):
            frappe.get_doc({
                "doctype": "User", "email": username, "first_name": "CI Finance Reviewer",
                "enabled": 1, "user_type": "System User", "send_welcome_email": 0,
            }).insert(ignore_permissions=True)
        user = frappe.get_doc("User", username)
        if not any(row.role == role for row in user.roles):
            user.append("roles", {"role": role})
            user.save(ignore_permissions=True)
        if not frappe.db.exists("User Permission", {
            "user": username, "allow": "Company", "for_value": COMPANY,
        }):
            frappe.get_doc({
                "doctype": "User Permission", "user": username,
                "allow": "Company", "for_value": COMPANY,
                "apply_to_all_doctypes": 1,
            }).insert(ignore_permissions=True)

    def _authorized_revision(self, *, kind, source, value):
        """Approve a genuine synthetic draft with two authenticated Finance users."""
        frappe.set_user(PREPARER)
        draft = propose_mapping(kind, COMPANY, source, value)
        self.assertEqual(draft["state"], "Draft")
        self.assertEqual(frappe.db.get_value(
            "CCPC " + kind + " Mapping Revision", draft["name"], "approval_state"
        ), "Draft")
        # The preparer is NEVER a valid independent Finance approver.
        with self.assertRaises(MappingPolicyError):
            approve_mapping(kind, draft["name"], "forged-version", "SYNTHETIC CI")
        frappe.set_user(APPROVER)
        reviewed = get_mapping_review(kind, draft["name"])
        self.assertEqual(reviewed["owner"], PREPARER)
        self.assertEqual(reviewed["value"], value)
        with self.assertRaises(MappingPolicyError):
            approve_mapping(kind, draft["name"], "stale-review-token", "SYNTHETIC CI")
        result = approve_mapping(
            kind, draft["name"], reviewed["modified"], "CI INDEPENDENT REVIEW ONLY"
        )
        self.assertEqual(result["state"], "Approved")
        self.assertEqual(result["approved_by"], APPROVER)
        self.assertTrue(result["version"], "Approval must atomically create audit history")
        with self.assertRaises(MappingPolicyError):
            approve_mapping(kind, draft["name"], reviewed["modified"], "REPLAY")
        return draft["name"]

    def test_b_genuine_finance_approval_issue_posts_correct_stock_expense_gl(self):
        """Prove original issue builder and before_submit against genuine ledger.

        Full Finance G1 maker/checker + ledger path with synthetic test-only
        Company, Accounts and identities, NOT actual CCPC mapping authorization.
        """
        frappe.set_user("Administrator")
        self._ensure_user_role(PREPARER, "CCPC Mapping Preparer")
        self._ensure_user_role(APPROVER, "CCPC Accounting Mapping Approver")

        account = frappe.db.get_value("Account", {
            "name": "Stock Adjustment - _TC", "company": COMPANY, "is_group": 0,
            "disabled": 0, "root_type": "Expense",
        }, "name")
        self.assertTrue(account, "Synthetic fixture lacks usable expense Account")
        centers = frappe.get_all("Cost Center", filters={
            "company": COMPANY, "is_group": 0, "disabled": 0,
        }, pluck="name", limit_page_length=2)
        self.assertTrue(centers, "Synthetic fixture lacks a leaf Cost Center")
        center = centers[0]

        ex_revision = self._authorized_revision(
            kind="Expense", source="_Test Item Group", value=account,
        )
        cn_revision = self._authorized_revision(
            kind="Center", source=self.department, value=center,
        )
        frappe.set_user(ISSUER)
        result = issue_withdrawal_slip(self.slip_name)
        entry = frappe.get_doc("Stock Entry", result["name"])
        self.assertEqual(entry.docstatus, 1)
        self.assertEqual(entry.items[0].expense_account, account)
        self.assertEqual(entry.items[0].cost_center, center)
        self.assertEqual(entry.items[0].ccpc_expense_mapping_revision, ex_revision)
        self.assertEqual(entry.items[0].ccpc_center_mapping_revision, cn_revision)
        self.assertEqual(entry.items[0].ccpc_item_group_at_issue, "_Test Item Group")
        self.assertEqual(entry.items[0].department, self.department)
        self.assertAlmostEqual(self._bin_qty(), QTY_RECEIVED - QTY_ISSUED, places=5)

        gl = frappe.get_all(
            "GL Entry", filters={
                "voucher_type": "Stock Entry", "voucher_no": entry.name,
                "is_cancelled": 0,
            },
            fields=["account", "cost_center", "department", "debit", "credit"],
        )
        expense = [r for r in gl if r.account == account and flt(r.debit) > 0]
        self.assertTrue(expense, "Missing B/C-approved expense-side GL")
        self.assertAlmostEqual(sum(flt(r.debit) - flt(r.credit) for r in expense),
                               QTY_ISSUED * UNIT_RATE, places=2)
        self.assertTrue(all(r.cost_center == center and r.department == self.department for r in expense))
        with self.assertRaises(Exception):
            issue_withdrawal_slip(self.slip_name)
        self.assertAlmostEqual(self._bin_qty(), QTY_RECEIVED - QTY_ISSUED, places=5)

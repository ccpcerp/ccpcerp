# Copyright (c) 2026, CCPC and contributors
"""DISPOSABLE real Frappe/MariaDB B/C original stock issue acceptance probe.

Not a production-approval workflow test: for the positive case only, synthetic
test mapping rows are set to Approved through SQL after Draft insertion.
That emulates a future server approval service WITHOUT enabling one. This test
must NEVER be run on persistent DEVELOPMENT or PRODUCTION sites.
"""
import os

import frappe
from frappe.utils import flt, now_datetime

from erpnext.tests.utils import ERPNextTestSuite
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
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

    def _synthetic_approved_revision(self, *, doctype, scope, field, value):
        """CI-ONLY direct row update; never an operational approval API."""
        frappe.set_user(PREPARER)
        revision = frappe.get_doc({
            "doctype": doctype, "mapping_scope": scope,
            "revision_no": 1, field: value, "approval_state": "Draft",
            "effective_from": frappe.utils.add_days(now_datetime(), -1),
        }).insert(ignore_permissions=True)
        self.assertEqual(revision.owner, PREPARER)
        frappe.set_user("Administrator")
        frappe.db.sql(
            "UPDATE `tab" + doctype + "` "
            "SET approval_state='Approved', approved_by=%s, approved_at=%s, approval_reference=%s "
            "WHERE name=%s",
            (APPROVER, now_datetime(), "SYNTHETIC CI ONLY", revision.name),
        )
        return revision.name

    def test_b_synthetic_signed_issue_posts_correct_stock_expense_gl(self):
        """Prove original issue builder and before_submit against genuine ledger.

        NOT a proof of Finance approval workflow (fixture deliberately bypasses
        current Draft-only activation). No Company or human identities from CCPC.
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

        frappe.set_user(PREPARER)
        ex_scope = frappe.get_doc({
            "doctype": "CCPC Expense Mapping Scope",
            "company": COMPANY, "item_group": "_Test Item Group",
        }).insert(ignore_permissions=True)
        cn_scope = frappe.get_doc({
            "doctype": "CCPC Center Mapping Scope", "company": COMPANY,
            "beneficiary_department": self.department,
            "selector_kind": "Department Only", "selector_key": "DEPARTMENT_ONLY",
        }).insert(ignore_permissions=True)
        ex_revision = self._synthetic_approved_revision(
            doctype="CCPC Expense Mapping Revision", scope=ex_scope.name,
            field="expense_account", value=account,
        )
        cn_revision = self._synthetic_approved_revision(
            doctype="CCPC Center Mapping Revision", scope=cn_scope.name,
            field="cost_center", value=center,
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

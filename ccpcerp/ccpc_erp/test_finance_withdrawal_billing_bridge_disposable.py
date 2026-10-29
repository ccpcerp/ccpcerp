# Copyright (c) 2026, CCPC and contributors
"""Exact-source Finance -> Withdrawal -> SLE/GL -> billing-source SQL bridge.

DISPOSABLE-CI-ONLY candidate. Real Frappe/ERPNext tables, auth-backed signed
mapping decisions and submitted stock/ledger records; no mocked accounting,
no invoice approval, no generated invoice, and no production data.

Not wired to an automatic or manually triggered CircleCI workflow. This test
requires a future separately approved Finance-feature SHA, with the
finance-lifecycle-isolated-integration job/site and project-level SHA gate.
"""
import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import flt, nowdate

from ccpcerp.ccpc_erp.bc_finance_evidence import sign_mapping_decision
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
from ccpcerp.ccpc_erp.bc_mapping_workflow import (
    approve_mapping,
    get_mapping_review,
    propose_mapping,
)
from ccpcerp.ccpc_erp.ci_finance_lifecycle_site_guard import (
    assert_finance_disposable_site,
)
from ccpcerp.ccpc_erp.doctype.withdrawal_slip import (
    test_withdrawal_real_ledger_integration as base_ledger,
)
from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip

COMPANY = base_ledger.COMPANY
ISSUER = base_ledger.ISSUER
QTY_ISSUED = base_ledger.QTY_ISSUED
QTY_RECEIVED = base_ledger.QTY_RECEIVED
UNIT_RATE = base_ledger.UNIT_RATE
MAKER = "ci-bridge-finance-maker@invalid.test"
CHECKER = "ci-bridge-finance-checker@invalid.test"
BUYER = "_Test Company 5"


class TestFinanceWithdrawalBillingBridgeDisposable(ERPNextTestSuite):
    # Borrow the existing reviewed real-stock fixture, but do NOT let it seed
    # synthetic Approved mappings: approval below uses the actual signed API.
    _verify_genuine_finance_workflow = True
    _bin_qty = base_ledger.TestWithdrawalRealLedgerIntegration._bin_qty

    def setUp(self):
        # This MUST precede all fixture loading, SQL queries and user writes.
        assert_finance_disposable_site(getattr(frappe.local, "site", None))
        super().setUp()
        base_ledger.TestWithdrawalRealLedgerIntegration.setUp(self)
        frappe.set_user("Administrator")
        self.load_test_records("Sales Invoice")
        self.assertTrue(frappe.db.exists("Company", BUYER))
        self.assertNotEqual(COMPANY, BUYER)
        self.assertEqual(
            frappe.db.get_value("Company", COMPANY, "default_currency"),
            frappe.db.get_value("Company", BUYER, "default_currency"),
            "Disposable intercompany sample requires equal currencies.",
        )
        # The requestor Department must belong to the BUYER, not the issuing
        # Company. The real beneficiary work_for_department remains in the
        # issuing Company so the signed Expense/Center GL allocation is source-
        # scoped. All modifications are synthetic CI test fixtures only.
        buyer_department_name = "CI Bridge Request " + frappe.generate_hash(length=10)
        buyer_department = frappe.get_doc({
            "doctype": "Department",
            "department_name": buyer_department_name,
            "company": BUYER,
            "is_group": 0,
        }).insert(ignore_permissions=True)
        self.assertEqual(
            frappe.db.get_value("Department", buyer_department.name, "company"), BUYER
        )
        frappe.db.set_value(
            "Withdrawal Slip", self.slip_name,
            {"requesting_company": BUYER, "department": buyer_department.name},
            update_modified=False,
        )
        self.assertEqual(
            frappe.db.get_value(
                "Withdrawal Slip", self.slip_name,
                ["requesting_company", "department"], as_dict=True,
            ).get("requesting_company"), BUYER,
        )
        self.assertEqual(
            frappe.db.get_value("Withdrawal Slip", self.slip_name, "department"),
            buyer_department.name,
        )
        self.assertEqual(
            frappe.db.get_value(
                "Withdrawal Slip", self.slip_name, "work_for_department"
            ), self.department,
        )
        self.account = "Stock Adjustment - _TC"
        self.center = frappe.db.get_value(
            "Cost Center", {"company": COMPANY, "is_group": 0, "disabled": 0},
            "name",
        )
        self.assertTrue(self.center)

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def _actor(self, email, role):
        frappe.set_user("Administrator")
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role}).insert(
                ignore_permissions=True
            )
        if not frappe.db.exists("User", email):
            frappe.get_doc({
                "doctype": "User",
                "email": email,
                "first_name": "Disposable Bridge Finance",
                "enabled": 1,
                "user_type": "System User",
                "send_welcome_email": 0,
            }).insert(ignore_permissions=True)
        user = frappe.get_doc("User", email)
        self.assertFalse(any(r.role == "Stockroom Personnel" for r in user.roles))
        if not any(r.role == role for r in user.roles):
            user.append("roles", {"role": role})
            user.save(ignore_permissions=True)
        if not frappe.db.exists("User Permission", {
            "user": email, "allow": "Company", "for_value": COMPANY,
            "apply_to_all_doctypes": 1,
        }):
            frappe.get_doc({
                "doctype": "User Permission",
                "user": email,
                "allow": "Company",
                "for_value": COMPANY,
                "apply_to_all_doctypes": 1,
            }).insert(ignore_permissions=True)

    def _approve_signed(self, kind, source, value):
        frappe.set_user(MAKER)
        draft = propose_mapping(kind, COMPANY, source, value)
        self.assertEqual(draft["state"], "Draft")
        frappe.set_user(CHECKER)
        reviewed = get_mapping_review(kind, draft["name"])
        self.assertEqual(reviewed["owner"], MAKER)
        with self.assertRaises(MappingPolicyError):
            approve_mapping(kind, draft["name"], reviewed["modified"], "FORGED CI APPROVAL")
        signed = sign_mapping_decision(
            kind, draft["name"], reviewed["modified"], "Approve"
        )
        self.assertEqual(signed["state"], "Signed")
        approved = approve_mapping(
            kind, draft["name"], reviewed["modified"], signed["name"]
        )
        self.assertEqual(approved["state"], "Approved")
        self.assertEqual(approved["approved_by"], CHECKER)
        self.assertEqual(
            frappe.db.count("CCPC Mapping Decision Use", {"decision": signed["name"]}),
            1,
        )
        return draft["name"]

    def _billing_candidate(self):
        frappe.set_user("Administrator")
        batch = frappe.get_doc({
            "doctype": "Intercompany Material Billing Batch",
            "issuing_company": COMPANY,
            "requesting_company": BUYER,
            "billing_month": nowdate(),
            "status": "Draft",
        }).insert(ignore_permissions=True)
        return batch

    def test_a_signed_issue_reaches_real_ledger_and_billing_source(self):
        """One approved original issue appears once in a real billing preview."""
        self._actor(MAKER, "CCPC Mapping Preparer")
        self._actor(CHECKER, "CCPC Accounting Mapping Approver")
        expense_revision = self._approve_signed(
            "Expense", "_Test Item Group", self.account
        )
        center_revision = self._approve_signed(
            "Center", self.department, self.center
        )
        frappe.set_user(ISSUER)
        result = issue_withdrawal_slip(self.slip_name)
        stock = frappe.get_doc("Stock Entry", result["name"])
        self.assertEqual(stock.docstatus, 1)
        self.assertEqual(stock.company, COMPANY)
        self.assertEqual(stock.withdrawal_slip, self.slip_name)
        self.assertEqual(len(stock.items), 1)
        detail = stock.items[0]
        self.assertEqual(detail.ccpc_expense_mapping_revision, expense_revision)
        self.assertEqual(detail.ccpc_center_mapping_revision, center_revision)
        self.assertEqual(detail.expense_account, self.account)
        self.assertEqual(detail.cost_center, self.center)
        self.assertEqual(detail.department, self.department)
        self.assertAlmostEqual(self._bin_qty(), QTY_RECEIVED - QTY_ISSUED)

        ledger = frappe.get_all(
            "Stock Ledger Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": stock.name,
                     "is_cancelled": 0},
            fields=["item_code", "actual_qty", "stock_value_difference"],
        )
        self.assertTrue(ledger, "Submitted source has no real Stock Ledger Entry")
        self.assertAlmostEqual(sum(flt(r.actual_qty) for r in ledger), -QTY_ISSUED)
        gl = frappe.get_all(
            "GL Entry", filters={"voucher_type": "Stock Entry",
                                 "voucher_no": stock.name, "is_cancelled": 0},
            fields=["account", "cost_center", "department", "debit", "credit"],
        )
        expense = [g for g in gl if g.account == self.account and flt(g.debit) > 0]
        self.assertTrue(expense, "Signed mapping did not reach real expense GL")
        self.assertAlmostEqual(
            sum(flt(r.debit) - flt(r.credit) for r in expense),
            QTY_ISSUED * UNIT_RATE,
            places=2,
        )
        self.assertTrue(all(
            g.cost_center == self.center and g.department == self.department
            for g in expense
        ))

        batch = self._billing_candidate()
        first = batch.refresh_preview()
        self.assertEqual(first["line_count"], 1)
        current = frappe.get_doc("Intercompany Material Billing Batch", batch.name)
        self.assertEqual(current.status, "Draft")
        self.assertEqual(len(current.items), 1)
        row = current.items[0]
        self.assertEqual(row.stock_entry, stock.name)
        self.assertEqual(row.stock_entry_detail, detail.name)
        self.assertEqual(row.withdrawal_slip, self.slip_name)
        self.assertEqual(row.withdrawal_slip_item, detail.withdrawal_slip_item)
        self.assertEqual(row.item_code, detail.item_code)
        self.assertAlmostEqual(flt(row.quantity), QTY_ISSUED)
        self.assertAlmostEqual(flt(row.frozen_basic_amount), flt(detail.basic_amount))
        self.assertFalse(current.sales_invoice or current.purchase_invoice)
        second = current.refresh_preview()
        self.assertEqual(second["line_count"], 1)
        self.assertEqual(frappe.db.count("Stock Entry", {
            "withdrawal_slip": self.slip_name, "docstatus": 1,
        }), 1, "Preview replay must not post another Stock Entry")
        self.assertEqual(frappe.db.count("GL Entry", {
            "voucher_type": "Stock Entry", "voucher_no": stock.name,
            "is_cancelled": 0,
        }), len(gl), "Preview replay must not repost GL")
        with self.assertRaises(Exception):
            frappe.set_user(ISSUER)
            issue_withdrawal_slip(self.slip_name)

    def test_b_unsigned_issue_cannot_enter_billing_preview(self):
        """Missing signed maps must leave zero stock, GL and billable source."""
        frappe.set_user(ISSUER)
        before_qty = self._bin_qty()
        before_sle = frappe.db.count("Stock Ledger Entry", {
            "voucher_type": "Stock Entry", "item_code": self.item.name,
        })
        with self.assertRaises(MappingPolicyError):
            issue_withdrawal_slip(self.slip_name)
        self.assertAlmostEqual(self._bin_qty(), before_qty)
        self.assertEqual(frappe.db.count("Stock Entry", {
            "withdrawal_slip": self.slip_name, "docstatus": 1,
        }), 0)
        self.assertEqual(frappe.db.count("Stock Ledger Entry", {
            "voucher_type": "Stock Entry", "item_code": self.item.name,
        }), before_sle, "Rejected issue must not add SLE beyond the fixture receipt")
        batch = self._billing_candidate()
        preview = batch.refresh_preview()
        self.assertEqual(preview["line_count"], 0)
        self.assertFalse(frappe.get_doc(
            "Intercompany Material Billing Batch", batch.name
        ).items)

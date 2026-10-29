# Copyright (c) 2026, CCPC and contributors
"""PR31: isolated real SLE vs posted P&L GL reconciliation in MariaDB only."""
import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import flt, nowdate

from ccpcerp.ccpc_erp import test_finance_withdrawal_billing_bridge_disposable as bridge
from ccpcerp.ccpc_erp.ci_four_reports_site_guard import assert_approved_site
from ccpcerp.ccpc_erp.doctype.withdrawal_slip import (
    test_withdrawal_real_ledger_integration as ledger_fixture,
)
from ccpcerp.ccpc_erp.report.ccpc_company_stock_pnl_gl_reconciliation import (
    ccpc_company_stock_pnl_gl_reconciliation as report,
)
from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip


class TestCompanyGLReport31Disposable(ERPNextTestSuite):
    _verify_genuine_finance_workflow = True
    _actor = bridge.TestFinanceWithdrawalBillingBridgeDisposable._actor
    _approve_signed = bridge.TestFinanceWithdrawalBillingBridgeDisposable._approve_signed
    _bin_qty = ledger_fixture.TestWithdrawalRealLedgerIntegration._bin_qty

    def setUp(self):
        assert_approved_site(getattr(frappe.local, "site", None))
        ERPNextTestSuite.setUp(self)
        ledger_fixture.TestWithdrawalRealLedgerIntegration.setUp(self)
        frappe.set_user("Administrator")
        self.account = "Stock Adjustment - _TC"
        self.center = frappe.db.get_value(
            "Cost Center",
            {"company": bridge.COMPANY, "is_group": 0, "disabled": 0},
            "name",
        )
        self.assertTrue(self.center)

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def _filters(self, **changes):
        f = {"company": bridge.COMPANY, "from_date": nowdate(), "to_date": nowdate()}
        f.update(changes)
        return f

    def _rows(self, **changes):
        cols, data = report.execute(self._filters(**changes))
        self.assertTrue(cols)
        return data

    def _comparison(self, name):
        found = [
            row for row in self._rows()
            if row["perspective"] == "STOCK_ISSUE_VOUCHER_COMPARISON"
            and row["voucher_no"] == name
        ]
        self.assertEqual(len(found), 1)
        return found[0]

    def _issue(self):
        self._actor(bridge.MAKER, "CCPC Mapping Preparer")
        self._actor(bridge.CHECKER, "CCPC Accounting Mapping Approver")
        self._approve_signed("Expense", "_Test Item Group", self.account)
        self._approve_signed("Center", self.department, self.center)
        frappe.set_user(bridge.ISSUER)
        created = issue_withdrawal_slip(self.slip_name)
        frappe.set_user("Administrator")
        stock = frappe.get_doc("Stock Entry", created["name"])
        self.assertEqual(stock.docstatus, 1)
        return stock, stock.items[0]

    def _ledger(self, stock, detail):
        sle = frappe.get_all("Stock Ledger Entry", filters={
            "voucher_type": "Stock Entry", "voucher_no": stock.name,
            "voucher_detail_no": detail.name, "is_cancelled": 0,
        }, fields=["name", "actual_qty", "stock_value_difference"])
        self.assertTrue(sle)
        outgoing = [r for r in sle if flt(r.actual_qty) < 0]
        self.assertTrue(outgoing)
        return outgoing

    def _expense_gl(self, stock):
        gl = frappe.get_all("GL Entry", filters={
            "voucher_type": "Stock Entry", "voucher_no": stock.name,
            "account": self.account, "is_cancelled": 0, "docstatus": 1,
        }, fields=["name", "debit", "credit"])
        self.assertTrue(gl)
        return gl

    def test_a_real_submitted_stock_sle_and_posted_expense_gl_match(self):
        stock, detail = self._issue()
        outgoing = self._ledger(stock, detail)
        gl = self._expense_gl(stock)
        source_value = -sum(flt(r.stock_value_difference) for r in outgoing)
        gl_value = sum(flt(r.debit) - flt(r.credit) for r in gl)
        self.assertAlmostEqual(source_value, gl_value, places=2)
        result = self._comparison(stock.name)
        self.assertEqual(result["company"], bridge.COMPANY)
        self.assertEqual(result["withdrawal_slip"], self.slip_name)
        self.assertEqual(result["source_item_rows"], 1)
        self.assertEqual(result["matched_sle_item_rows"], 1)
        self.assertAlmostEqual(result["gross_original_sle_value"], source_value, places=2)
        self.assertAlmostEqual(result["voucher_pnl_expense_gl_value"], gl_value, places=2)
        self.assertAlmostEqual(result["stock_vs_expense_variance"], 0, places=2)
        self.assertEqual(result["source_quality"], "VOUCHER_VALUES_MATCH_WITHIN_TOLERANCE")
        self.assertEqual(
            result["company_currency"],
            frappe.db.get_value("Company", bridge.COMPANY, "default_currency"),
        )
        posted_pnl = [r for r in self._rows()
                      if r["perspective"] == "ALL_PNL_GL_VOUCHER_ACCOUNT"
                      and r["voucher_no"] == stock.name and r["account"] == self.account]
        self.assertEqual(len(posted_pnl), 1)
        self.assertAlmostEqual(posted_pnl[0]["pnl_account_gl_net_debit"], gl_value, places=2)
        self.assertIsNone(posted_pnl[0]["gross_original_sle_value"])

    def test_b_changed_expense_gl_is_flagged_not_silently_net_matched(self):
        stock, detail = self._issue()
        gl = self._expense_gl(stock)
        before = self._comparison(stock.name)
        # Disposable test-only GL manipulation, rolled back by ERPNext test runner.
        frappe.db.set_value(
            "GL Entry", gl[0].name,
            "debit", flt(gl[0].debit) + 9, update_modified=False,
        )
        after = self._comparison(stock.name)
        self.assertIn("STOCK_VOUCHER_VALUE_GL_VARIANCE", after["source_quality"])
        self.assertAlmostEqual(after["gross_original_sle_value"], before["gross_original_sle_value"])
        self.assertAlmostEqual(after["stock_vs_expense_variance"], -9, places=2)

    def test_c_missing_expense_gl_is_explicit_missing_not_zero(self):
        stock, detail = self._issue()
        gl = self._expense_gl(stock)
        for entry in gl:
            frappe.db.set_value(
                "GL Entry", entry.name, "is_cancelled", 1, update_modified=False,
            )
        result = self._comparison(stock.name)
        self.assertIsNone(result["voucher_pnl_expense_gl_value"])
        self.assertIsNone(result["stock_vs_expense_variance"])
        self.assertIn("NO_PNL_EXPENSE_GL_FOR_STOCK_VOUCHER", result["source_quality"])

    def test_d_missing_or_split_outbound_sle_retains_original_voucher(self):
        stock, detail = self._issue()
        outgoing = self._ledger(stock, detail)
        for entry in outgoing:
            frappe.db.set_value(
                "Stock Ledger Entry", entry.name, "is_cancelled", 1,
                update_modified=False,
            )
        missing = self._comparison(stock.name)
        self.assertIn("MISSING_OUTBOUND_SLE", missing["source_quality"])
        self.assertIsNone(missing["gross_original_sle_value"])
        self.assertIsNone(missing["stock_vs_expense_variance"])
        for entry in outgoing:
            frappe.db.set_value(
                "Stock Ledger Entry", entry.name, "is_cancelled", 0,
                update_modified=False,
            )
        restored = self._comparison(stock.name)
        self.assertEqual(restored["source_item_rows"], 1)
        self.assertEqual(restored["matched_sle_item_rows"], 1)

    def test_e_real_access_and_company_date_scopes_fail_closed(self):
        stock, detail = self._issue()
        frappe.set_user(bridge.ISSUER)
        try:
            with self.assertRaises(frappe.PermissionError):
                report.execute(self._filters())
        finally:
            frappe.set_user("Administrator")
        for bad in (
            {"company": "CI-INVALID-COMPANY"},
            {"from_date": None},
            {"to_date": None},
            {"from_date": "2026-11-01", "to_date": "2026-10-01"},
            {"from_date": "2025-01-01", "to_date": "2026-10-01"},
        ):
            with self.assertRaises(frappe.ValidationError):
                report.execute(self._filters(**bad))
        self.assertEqual(self._comparison(stock.name)["source_item_rows"], 1)

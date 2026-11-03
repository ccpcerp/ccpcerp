# Copyright (c) 2026, CCPC and contributors
"""PR29 real Department report acceptance: isolated disposable CI ONLY.

Reuses established ERPNext _Test Company real-stock fixture and *genuine*
signed maker/checker Finance mapping helpers, without inheriting any previous
Finance/bridge test methods. All deliberate source corruption occurs only
inside a guarded disposable MariaDB test transaction. No manual commits,
invoice submissions, DEV/PROD access or CCPC real masters.
"""
import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import flt, nowdate

from ccpcerp.ccpc_erp import (
    test_finance_withdrawal_billing_bridge_disposable as bridge,
)
from ccpcerp.ccpc_erp.ci_four_reports_site_guard import assert_approved_site
from ccpcerp.ccpc_erp.doctype.withdrawal_slip import (
    test_withdrawal_real_ledger_integration as base_ledger,
)
from ccpcerp.ccpc_erp.report.ccpc_department_material_consumption import (
    ccpc_department_material_consumption as report,
)
from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip


class TestDepartmentReport29Disposable(ERPNextTestSuite):
    # Explicit helper borrowing ONLY; neither other test class is inherited.
    _verify_genuine_finance_workflow = True
    _actor = bridge.TestFinanceWithdrawalBillingBridgeDisposable._actor
    _approve_signed = bridge.TestFinanceWithdrawalBillingBridgeDisposable._approve_signed
    _bin_qty = base_ledger.TestWithdrawalRealLedgerIntegration._bin_qty

    def setUp(self):
        # First action before any fixture or database write.
        assert_approved_site(getattr(frappe.local, "site", None))
        ERPNextTestSuite.setUp(self)
        base_ledger.TestWithdrawalRealLedgerIntegration.setUp(self)
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

    def _filters(self, **change):
        values = {
            "company": bridge.COMPANY,
            "from_date": nowdate(),
            "to_date": nowdate(),
        }
        values.update(change)
        return values

    def _rows(self, **change):
        columns, rows = report.execute(self._filters(**change))
        self.assertTrue(columns)
        return rows

    def _row(self, detail, **change):
        rows = self._rows(**change)
        exact = [r for r in rows if r["stock_entry_detail"] == detail.name]
        self.assertEqual(len(exact), 1, "One original source row is required")
        return exact[0]

    def _issue_signed_original(self):
        self._actor(bridge.MAKER, "CCPC Mapping Preparer")
        self._actor(bridge.CHECKER, "CCPC Accounting Mapping Approver")
        self._approve_signed("Expense", "_Test Item Group", self.account)
        self._approve_signed("Center", self.department, self.center)
        frappe.set_user(bridge.ISSUER)
        result = issue_withdrawal_slip(self.slip_name)
        frappe.set_user("Administrator")
        stock = frappe.get_doc("Stock Entry", result["name"])
        self.assertEqual(stock.docstatus, 1)
        self.assertEqual(stock.company, bridge.COMPANY)
        self.assertEqual(stock.department, self.department)
        self.assertEqual(len(stock.items), 1)
        self.assertEqual(stock.items[0].department, self.department)
        return stock, stock.items[0]

    def _new_other_department(self):
        frappe.set_user("Administrator")
        return frappe.get_doc({
            "doctype": "Department",
            "department_name": "CI Dept29 Other " + frappe.generate_hash(length=10),
            "company": bridge.COMPANY,
            "is_group": 0,
        }).insert(ignore_permissions=True).name

    def test_a_actual_sle_and_expense_gl_reconcile_with_original_report_row(self):
        """One signed original issue and authentic SLE/GL, not billing money."""
        stock, detail = self._issue_signed_original()
        sle = frappe.get_all(
            "Stock Ledger Entry",
            filters={
                "voucher_type": "Stock Entry", "voucher_no": stock.name,
                "voucher_detail_no": detail.name, "is_cancelled": 0,
            },
            fields=["actual_qty", "stock_value_difference"],
        )
        self.assertTrue(sle)
        outgoing = [r for r in sle if flt(r.actual_qty) < 0]
        self.assertTrue(outgoing)
        expected_qty = -sum(flt(r.actual_qty) for r in outgoing)
        expected_value = -sum(flt(r.stock_value_difference) for r in outgoing)
        row = self._row(detail)
        self.assertEqual(row["stock_entry"], stock.name)
        self.assertEqual(row["withdrawal_slip"], self.slip_name)
        self.assertEqual(row["withdrawal_slip_item"], self.slip_item_name)
        self.assertEqual(row["beneficiary_department_at_issue"], self.department)
        self.assertEqual(row["source_quality"], "OK")
        self.assertEqual(row["section_provenance"], "NO_ORIGINAL_SECTION_SNAPSHOT")
        self.assertEqual(row["issued_qty"], bridge.QTY_ISSUED)
        self.assertAlmostEqual(row["sle_issued_qty"], expected_qty)
        self.assertAlmostEqual(row["gross_sle_issue_value"], expected_value, places=2)
        self.assertEqual(
            row["issuing_currency"],
            frappe.db.get_value("Company", bridge.COMPANY, "default_currency"),
        )
        gl = frappe.get_all(
            "GL Entry",
            filters={
                "voucher_type": "Stock Entry", "voucher_no": stock.name,
                "account": self.account, "is_cancelled": 0,
            },
            fields=["debit", "credit", "department"],
        )
        self.assertTrue(gl)
        self.assertTrue(all(g.department == self.department for g in gl))
        self.assertAlmostEqual(
            sum(flt(g.debit) - flt(g.credit) for g in gl),
            expected_value, places=2,
        )
        self.assertEqual(len(self._rows(department=self.department)), 1)

    def test_b_mutable_slip_requesting_department_is_not_historical_beneficiary(self):
        """Even direct synthetic Slip edit cannot reattribute the posted source."""
        stock, detail = self._issue_signed_original()
        baseline = self._row(detail)
        other = self._new_other_department()
        self.assertNotEqual(other, self.department)
        # Test-only deliberate SQL corruption: never edit live or DEV slips.
        frappe.db.set_value(
            "Withdrawal Slip", self.slip_name,
            "department", other, update_modified=False,
        )
        row = self._row(detail)
        self.assertEqual(row["beneficiary_department_at_issue"], self.department)
        self.assertEqual(row["source_quality"], "OK")
        self.assertEqual(row["gross_sle_issue_value"], baseline["gross_sle_issue_value"])
        self.assertNotIn("requesting_section", row)
        self.assertNotIn("work_for_section", row)
        self.assertEqual(len(self._rows(department=other)), 0)

    def test_c_corrupt_row_header_department_and_source_membership_are_flagged(self):
        """No valid beneficiary from forged row Department; preserve raw issue."""
        stock, detail = self._issue_signed_original()
        other = self._new_other_department()
        frappe.db.set_value(
            "Stock Entry Detail", detail.name,
            "department", other, update_modified=False,
        )
        corrupt = self._row(detail, department=other)
        self.assertIsNone(corrupt["beneficiary_department_at_issue"])
        self.assertIn("DEPARTMENT_ROW_HEADER_CONFLICT", corrupt["source_quality"])
        self.assertEqual(corrupt["header_department"], self.department)
        self.assertEqual(corrupt["detail_department"], other)
        # The opposite header filter must also surface the conflicting row.
        self.assertEqual(self._row(detail, department=self.department)["stock_entry"], stock.name)
        frappe.db.set_value(
            "Stock Entry Detail", detail.name,
            "withdrawal_slip_item", "CI-MISSING-SLIP-ITEM", update_modified=False,
        )
        missing = self._row(detail)
        self.assertIn("SLIP_ITEM_LINK_INVALID", missing["source_quality"])
        self.assertEqual(missing["stock_entry"], stock.name)

    def test_d_missing_ledger_and_mismatched_outbound_qty_are_explicit(self):
        """Do not fabricate zero source value or silently ignore a changed SLE."""
        stock, detail = self._issue_signed_original()
        original = self._row(detail)
        sle = frappe.get_all(
            "Stock Ledger Entry",
            filters={
                "voucher_type": "Stock Entry", "voucher_no": stock.name,
                "voucher_detail_no": detail.name, "is_cancelled": 0,
            },
            fields=["name", "actual_qty"],
        )
        self.assertTrue(sle)
        first = sle[0]
        frappe.db.set_value(
            "Stock Ledger Entry", first.name,
            "actual_qty", flt(first.actual_qty) * 0.5, update_modified=False,
        )
        changed = self._row(detail)
        self.assertIn("SLE_QUANTITY_MISMATCH", changed["source_quality"])
        self.assertEqual(
            changed["beneficiary_department_at_issue"], self.department,
        )
        for entry in sle:
            frappe.db.set_value(
                "Stock Ledger Entry", entry.name,
                "is_cancelled", 1, update_modified=False,
            )
        gone = self._row(detail)
        self.assertIn("NO_ORIGINAL_OUTBOUND_SLE", gone["source_quality"])
        self.assertIsNone(gone["sle_issued_qty"])
        self.assertIsNone(gone["gross_sle_issue_value"])
        self.assertGreater(original["gross_sle_issue_value"], 0)

    def test_e_real_permission_and_invalid_company_or_date_scope_denied(self):
        """Real role check precedes executing SQL, even on disposable Company."""
        stock, detail = self._issue_signed_original()
        frappe.set_user(bridge.ISSUER)
        try:
            with self.assertRaises(frappe.PermissionError):
                report.execute(self._filters())
        finally:
            frappe.set_user("Administrator")
        for invalid in (
            {"company": None},
            {"company": "CI-FOREIGN-COMPANY-NOT-FOUND"},
            {"from_date": None},
            {"to_date": None},
            {"from_date": "2026-10-30", "to_date": "2026-10-01"},
            {"from_date": "2025-01-01", "to_date": "2026-10-01"},
        ):
            with self.assertRaises(frappe.ValidationError):
                report.execute(self._filters(**invalid))
        self.assertEqual(self._row(detail)["source_quality"], "OK")

    def test_f_multiple_real_sle_fragments_count_once_at_original_issue_grain(self):
        """Two outbound ledger fragments must not double-count issue or valuation."""
        stock, detail = self._issue_signed_original()
        baseline = self._row(detail)
        entries = frappe.get_all(
            "Stock Ledger Entry",
            filters={
                "voucher_type": "Stock Entry", "voucher_no": stock.name,
                "voucher_detail_no": detail.name, "is_cancelled": 0,
            },
            fields=["name", "actual_qty", "stock_value_difference"],
        )
        self.assertTrue(entries)
        first = entries[0]
        self.assertLess(flt(first.actual_qty), 0)
        # Deliberate synthetic raw SQL split only inside disposable CI rollback.
        # Together, the original and new fragment retain exactly the old total.
        frappe.db.set_value(
            "Stock Ledger Entry", first.name,
            {
                "actual_qty": flt(first.actual_qty) / 2,
                "stock_value_difference": flt(first.stock_value_difference) / 2,
            }, update_modified=False,
        )
        fragment = frappe.copy_doc(frappe.get_doc("Stock Ledger Entry", first.name))
        fragment.name = None
        fragment.voucher_type = "Stock Entry"
        fragment.voucher_no = stock.name
        fragment.voucher_detail_no = detail.name
        fragment.company = bridge.COMPANY
        fragment.db_insert()
        self.assertNotEqual(fragment.name, first.name)
        after = self._row(detail)
        self.assertEqual(after["source_quality"], "OK")
        self.assertEqual(after["beneficiary_department_at_issue"], self.department)
        self.assertEqual(after["issued_qty"], baseline["issued_qty"])
        self.assertAlmostEqual(after["sle_issued_qty"], baseline["sle_issued_qty"])
        self.assertAlmostEqual(
            after["gross_sle_issue_value"], baseline["gross_sle_issue_value"], places=2,
        )
        self.assertEqual(
            len([r for r in self._rows() if r["stock_entry_detail"] == detail.name]), 1,
        )

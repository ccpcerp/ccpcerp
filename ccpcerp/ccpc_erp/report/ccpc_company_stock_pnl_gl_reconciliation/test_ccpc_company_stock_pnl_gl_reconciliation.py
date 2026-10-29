# Copyright (c) 2026, CCPC and contributors
"""Pure source/presentation and permission tests; NOT native MariaDB testing."""
from unittest import TestCase
from unittest.mock import patch

import frappe

from ccpcerp.ccpc_erp.report.ccpc_company_stock_pnl_gl_reconciliation import (
    ccpc_company_stock_pnl_gl_reconciliation as report,
)


class TestCCPCCompanyStockPnlGLReconciliation(TestCase):
    @staticmethod
    def source(**kwargs):
        values = {
            "stock_entry": "MAT-ISSUE-1",
            "posting_date": "2026-10-15",
            "issuing_company": "_Test Company",
            "issuing_currency": "PHP",
            "withdrawal_slip": "WS-1",
            "issue_item_rows": 2,
            "rows_with_outbound_sle": 2,
            "gross_outbound_qty_untyped": 2,
            "gross_sle_material_value": 200,
            "negative_value_source_rows": 0,
        }
        values.update(kwargs)
        return frappe._dict(values)

    @staticmethod
    def gl(**kwargs):
        values = {
            "issuing_company": "_Test Company",
            "issuing_currency": "PHP",
            "posting_date": "2026-10-15",
            "voucher_type": "Stock Entry",
            "voucher_no": "MAT-ISSUE-1",
            "account": "Stock Adjustment - _TC",
            "pnl_root_type": "Expense",
            "pnl_net_debit": 200,
            "gl_entry_rows": 2,
        }
        values.update(kwargs)
        return frappe._dict(values)

    def test_matching_issue_voucher_and_expense_gl_are_comparison_only(self):
        rows = report._present([self.source()], [self.gl()])
        self.assertEqual(len(rows), 2)
        comparison, gl = rows
        self.assertEqual(comparison["perspective"], "STOCK_ISSUE_VOUCHER_COMPARISON")
        self.assertEqual(comparison["gross_original_sle_value"], 200)
        self.assertEqual(comparison["voucher_pnl_expense_gl_value"], 200)
        self.assertEqual(comparison["stock_vs_expense_variance"], 0)
        self.assertEqual(comparison["source_quality"], "VOUCHER_VALUES_MATCH_WITHIN_TOLERANCE")
        self.assertIsNone(comparison["pnl_account_gl_net_debit"])
        self.assertEqual(gl["perspective"], "ALL_PNL_GL_VOUCHER_ACCOUNT")
        self.assertIsNone(gl["gross_original_sle_value"])
        self.assertIsNone(gl["stock_vs_expense_variance"])
        self.assertEqual(gl["pnl_account_gl_net_debit"], 200)
        self.assertIn("Do Not Add", report.get_columns()[0]["label"])

    def test_same_stock_voucher_multiple_expense_accounts_reconcile_once(self):
        rows = report._present([self.source()], [
            self.gl(account="Expense-A", pnl_net_debit=110),
            self.gl(account="Expense-B", pnl_net_debit=90),
        ])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["voucher_pnl_expense_gl_value"], 200)
        self.assertEqual(rows[0]["stock_vs_expense_variance"], 0)
        self.assertEqual(
            len([r for r in rows if r["perspective"] == "STOCK_ISSUE_VOUCHER_COMPARISON"]), 1,
        )

    def test_stock_gl_variance_flagged_not_forced_to_match(self):
        row = report._present(
            [self.source(gross_sle_material_value=250)],
            [self.gl(pnl_net_debit=200)],
        )[0]
        self.assertEqual(row["stock_vs_expense_variance"], 50)
        self.assertIn("STOCK_VOUCHER_VALUE_GL_VARIANCE", row["source_quality"])

    def test_missing_gl_is_null_not_zero_and_missing_sle_is_exception(self):
        row = report._present([self.source()], [])[0]
        self.assertIsNone(row["voucher_pnl_expense_gl_value"])
        self.assertIsNone(row["stock_vs_expense_variance"])
        self.assertIn("NO_PNL_EXPENSE_GL_FOR_STOCK_VOUCHER", row["source_quality"])
        absent = report._present(
            [self.source(rows_with_outbound_sle=0, gross_sle_material_value=None)], [self.gl()],
        )[0]
        self.assertIsNone(absent["gross_original_sle_value"])
        self.assertIsNone(absent["stock_vs_expense_variance"])
        self.assertIn("MISSING_OUTBOUND_SLE", absent["source_quality"])

    def test_issue_without_withdrawal_is_still_in_company_source(self):
        row = report._present([self.source(withdrawal_slip=None)], [])[0]
        self.assertIn("NON_WITHDRAWAL_MATERIAL_ISSUE", row["source_quality"])
        self.assertEqual(row["gross_original_sle_value"], 200)

    def test_partial_sle_coverage_and_negative_sle_value_flagged(self):
        row = report._present([self.source(
            rows_with_outbound_sle=1, negative_value_source_rows=1,
        )], [])[0]
        self.assertIn("ISSUE_ITEM_WITHOUT_OUTBOUND_SLE", row["source_quality"])
        self.assertIn("NEGATIVE_SLE_SOURCE_VALUATION", row["source_quality"])

    def test_other_company_pnl_income_and_other_voucher_separate(self):
        rows = report._present([], [
            self.gl(voucher_type="Sales Invoice", voucher_no="SI-1",
                    account="Sales - _TC", pnl_root_type="Income", pnl_net_debit=-300),
            self.gl(voucher_type="Journal Entry", voucher_no="JE-1",
                    account="Admin Expenses - _TC", pnl_root_type="Expense", pnl_net_debit=50),
        ])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["perspective"] == "ALL_PNL_GL_VOUCHER_ACCOUNT" for r in rows))
        self.assertEqual([r["pnl_account_gl_net_debit"] for r in rows], [-300, 50])
        self.assertTrue(all(r["gross_original_sle_value"] is None for r in rows))

    def test_pnl_income_on_stock_voucher_is_not_expense_for_comparison(self):
        row = report._present([self.source()], [self.gl(
            pnl_root_type="Income", account="Sales - _TC", pnl_net_debit=-200,
        )])[0]
        self.assertIsNone(row["voucher_pnl_expense_gl_value"])
        self.assertIn("NO_PNL_EXPENSE_GL_FOR_STOCK_VOUCHER", row["source_quality"])

    def test_duplicate_stock_or_gl_group_fails_closed(self):
        with self.assertRaises(frappe.ValidationError):
            report._present([self.source(), self.source()], [])
        with self.assertRaises(frappe.ValidationError):
            report._present([], [self.gl(), self.gl()])

    def test_sql_only_original_submitted_material_issue_with_grouped_sle(self):
        sql = report._stock_sql()
        for required in (
            "se.docstatus = 1", "se.purpose = 'Material Issue'",
            "se.company = %(company)s",
            "se.posting_date BETWEEN %(from_date)s AND %(to_date)s",
            "sle.voucher_type = 'Stock Entry'",
            "sle.is_cancelled = 0", "sle.actual_qty < 0",
            "GROUP BY sle.company, sle.voucher_no, sle.voucher_detail_no",
            "ledger.voucher_detail_no = sed.name",
            "AND ledger.company = se.company",
            "COUNT(DISTINCT sed.name)",
            "LIMIT %(stock_limit)s",
        ):
            self.assertIn(required, sql)
        self.assertNotIn("INNER JOIN `tabWithdrawal Slip`", sql)
        self.assertNotIn("tabGL Entry", sql)
        self.assertNotIn("tabIntercompany Material Billing Item", sql)

    def test_pnl_sql_filters_company_account_classification_and_cancellation(self):
        sql = report._pnl_sql()
        for required in (
            "FROM `tabGL Entry` gl", "INNER JOIN `tabAccount` acc",
            "acc.company = gl.company", "gl.company = %(company)s",
            "gl.docstatus = 1", "gl.is_cancelled = 0",
            "gl.posting_date BETWEEN %(from_date)s AND %(to_date)s",
            "acc.report_type = 'Profit and Loss'",
            "acc.root_type IN ('Expense', 'Income')",
            "GROUP BY gl.company",
            "LIMIT %(gl_limit)s",
        ):
            self.assertIn(required, sql)
        self.assertNotIn("tabStock Ledger Entry", sql)
        self.assertNotIn("tabIntercompany Material Billing Item", sql)

    def test_system_manager_and_explicit_doctype_permission_gate(self):
        with patch.object(report.frappe, "get_roles", return_value=["Finance Operator"]):
            with self.assertRaises(frappe.PermissionError):
                report._require_access()
        with (
            patch.object(report.frappe, "get_roles", return_value=["System Manager"]),
            patch.object(report.frappe, "has_permission", return_value=False),
        ):
            with self.assertRaises(frappe.PermissionError):
                report._require_access()

    def test_company_record_permission_and_bounded_dates(self):
        class Company:
            def check_permission(self, *_):
                raise frappe.PermissionError("Company denied")
        with (
            patch.object(report.frappe.db, "exists", return_value=True),
            patch.object(report.frappe, "get_doc", return_value=Company()),
        ):
            with self.assertRaises(frappe.PermissionError):
                report._validate_filters(frappe._dict({
                    "company": "_Test Company", "from_date": "2026-10-01",
                    "to_date": "2026-10-31",
                }))
        class AllowedCompany:
            def check_permission(self, *_):
                pass
        with (
            patch.object(report.frappe.db, "exists", return_value=True),
            patch.object(report.frappe, "get_doc", return_value=AllowedCompany()),
        ):
            for vals in (
                dict(from_date=None, to_date="2026-10-31"),
                dict(from_date="2026-12-01", to_date="2026-10-31"),
                dict(from_date="2025-01-01", to_date="2026-10-31"),
            ):
                with self.assertRaises(frappe.ValidationError):
                    report._validate_filters(frappe._dict(company="_Test Company", **vals))

    def test_per_result_stock_and_account_records_permission_checked(self):
        checked = []
        class Doc:
            def __init__(self, kind, name):
                self.kind, self.name = kind, name
            def check_permission(self, operation):
                checked.append((self.kind, self.name, operation))
        with patch.object(report.frappe, "get_doc", side_effect=lambda k, n: Doc(k, n)):
            report._check_row_visibility([self.source()], [self.gl()])
        self.assertIn(("Stock Entry", "MAT-ISSUE-1", "read"), checked)
        self.assertIn(("Account", "Stock Adjustment - _TC", "read"), checked)

    def test_row_cap_and_accounting_currency_columns(self):
        cols = {c["fieldname"]: c for c in report.get_columns()}
        for field in ("gross_original_sle_value", "voucher_pnl_expense_gl_value",
                      "stock_vs_expense_variance", "pnl_account_gl_net_debit"):
            self.assertEqual(cols[field]["fieldtype"], "Currency")
            self.assertEqual(cols[field]["options"], "company_currency")
        with (
            patch.object(report, "_require_access"),
            patch.object(report, "_validate_filters"),
            patch.object(report.frappe.db, "sql", side_effect=[
                [self.source()] * (report.MAX_STOCK_VOUCHERS + 1), [],
            ]),
        ):
            with self.assertRaises(frappe.ValidationError):
                report.execute({})

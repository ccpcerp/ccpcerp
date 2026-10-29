# Copyright (c) 2026, CCPC and contributors
"""Source-only report policy unit checks, not a real MariaDB integration run."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from ccpcerp.ccpc_erp.report.ccpc_intercompany_withdrawal_billing_status import (
    ccpc_intercompany_withdrawal_billing_status as report,
)


class TestCCPCIntercompanyWithdrawalBillingStatus(TestCase):
    @staticmethod
    def source(**overrides):
        data = {
            "posting_date": "2026-10-15",
            "issuing_company": "_Test Company",
            "requesting_company": "_Test Company 5",
            "withdrawal_slip": "WS-TEST",
            "stock_entry": "MAT-STE-TEST",
            "stock_entry_detail": "SED-ORIGINAL-1",
            "item_code": "_Test Item",
            "item_name": "Sample",
            "issued_qty": 2,
            "sle_issued_qty": 2,
            "sle_row_count": 2,
            "stock_uom": "_Test UOM",
            "gross_sle_issue_value": 100,
            "issuing_currency": "INR",
            "batch_name": "ICMB-TEST-1",
            "batch_status": "Approved",
            "billing_month": "2026-10-01",
            "billed_issuer": "_Test Company",
            "billed_requester": "_Test Company 5",
            "bill_item_code": "_Test Item",
            "bill_quantity": 2,
            "bill_uom": "_Test UOM",
            "frozen_basic_amount": 105,
            "sales_invoice": "SI-TEST",
            "sales_docstatus": 0,
            "sales_company": "_Test Company",
            "purchase_invoice": "PI-TEST",
            "purchase_docstatus": 0,
            "purchase_company": "_Test Company 5",
        }
        data.update(overrides)
        return frappe._dict(data)

    def test_exact_original_sle_is_grouped_before_billing_join(self):
        sql = report._source_sql()
        self.assertIn("GROUP BY sle.voucher_no, sle.voucher_detail_no, sle.company", sql)
        self.assertIn("sle.voucher_detail_no = sed.name", sql.replace("source_sle.voucher_detail_no", "sle.voucher_detail_no"))
        self.assertIn("source_sle.company = se.company", sql)
        self.assertIn("AND se.docstatus = 1", sql)
        self.assertIn("AND sle.is_cancelled = 0", sql)
        self.assertIn("AND sle.actual_qty < 0", sql)
        self.assertIn("AND batch.status != 'Cancelled'", sql)
        self.assertIn("AND ws.requesting_company != se.company", sql)
        self.assertIn("se.posting_date BETWEEN %(from_date)s AND %(to_date)s", sql)
        self.assertIn("bi.parentfield = 'items'", sql)
        self.assertNotIn("GROUP BY bi.", sql)

    def test_independent_sle_and_billing_amounts_not_added_together(self):
        item = report._present([self.source()])[0]
        self.assertEqual(item["gross_sle_issue_value"], 100)
        self.assertEqual(item["frozen_billing_amount"], 105)
        self.assertEqual((item["sales_status"], item["purchase_status"]), ("Draft", "Draft"))
        self.assertEqual(item["source_quality"], "OK")
        self.assertEqual(item["issuing_currency"], "INR")

    def test_unbilled_source_remains_visible_and_preserves_ledger_currency(self):
        item = report._present([self.source(
            batch_name=None, batch_status=None, frozen_basic_amount=None,
            sales_invoice=None, purchase_invoice=None, sales_docstatus=None,
            purchase_docstatus=None, sle_row_count=1,
        )])[0]
        self.assertEqual(item["billing_status"], "Unbilled")
        self.assertEqual(item["gross_sle_issue_value"], 100)
        self.assertIsNone(item["frozen_billing_amount"])
        self.assertEqual((item["sales_status"], item["purchase_status"]),
                         ("Not Created", "Not Created"))

    def test_duplicate_claim_fails_closed_instead_of_doubling_value(self):
        with patch.object(report.frappe, "throw", side_effect=RuntimeError("duplicate")):
            with self.assertRaisesRegex(RuntimeError, "duplicate"):
                report._present([self.source(), self.source(batch_name="ICMB-TEST-2")])

    def test_missing_sle_and_cross_company_invoice_are_explicit_exceptions(self):
        item = report._present([self.source(
            sle_row_count=None, sle_issued_qty=None,
            gross_sle_issue_value=None,
            sales_company="_Test Company 5",
            purchase_docstatus=2,
        )])[0]
        self.assertIsNone(item["gross_sle_issue_value"])
        self.assertIn("NO_ORIGINAL_OUTBOUND_SLE", item["source_quality"])
        self.assertIn("SALES_COMPANY_OR_LINK_INVALID", item["source_quality"])
        self.assertIn("APPROVED_BATCH_INVOICE_EXCEPTION", item["source_quality"])

    def test_claim_company_or_source_mismatch_is_not_silently_accepted(self):
        item = report._present([self.source(
            billed_requester="_Test Company",
            bill_quantity=3,
        )])[0]
        self.assertIn("BILLING_COMPANY_MISMATCH", item["source_quality"])
        self.assertIn("BILLING_SOURCE_MISMATCH", item["source_quality"])

    def test_missing_accounting_role_rejected(self):
        with (
            patch.object(report.frappe, "get_roles", return_value=["CCPC Stock Inquiry User"]),
            patch.object(report.frappe, "throw", side_effect=PermissionError("blocked")),
            self.assertRaisesRegex(PermissionError, "blocked"),
        ):
            report._require_access()

    def test_invoice_status_values_never_assume_posted(self):
        self.assertEqual(report._invoice_status(None, None), "Not Created")
        self.assertEqual(report._invoice_status("SI-1", None), "Missing Reference")
        self.assertEqual(report._invoice_status("SI-1", 0), "Draft")
        self.assertEqual(report._invoice_status("SI-1", 1), "Submitted")
        self.assertEqual(report._invoice_status("SI-1", 2), "Cancelled")

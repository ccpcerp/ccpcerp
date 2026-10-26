# Copyright (c) 2026, CCPC and contributors
"""Real MariaDB savepoint tests for paired intercompany approval failure.

This disposable-site probe uses actual SQL inserts into ToDo as unmistakable
test-only persistence markers. Invoice objects, master defaults and batch save
are mocked: this proves *rollback of SQL writes when the caller catches an
approval exception*, NOT validity of genuine Sales/Purchase Invoice drafts.
A later end-to-end invoice test must check real ERPNext document validation.
Never execute on persistent DEVELOPMENT or PRODUCTION.
"""

import os
from unittest.mock import MagicMock

import frappe
from ccpcerp.ccpc_erp.ci_disposable_site_guard import is_reviewed_intercompany_disposable_site
from erpnext.tests.utils import ERPNextTestSuite

from ccpcerp.ccpc_erp.doctype.intercompany_material_billing_batch.intercompany_material_billing_batch import (
    IntercompanyMaterialBillingBatch,
)


BRANCH = "review/intercompany-invoice-transaction-2026-10-20"


class TestIntercompanyAtomicPairDatabase(ERPNextTestSuite):
    def setUp(self):
        if not (
            is_reviewed_intercompany_disposable_site(getattr(frappe.local, "site", None))
            and os.environ.get("CCPC_ALLOW_IC_SQL_PROBE") == "isolated-only"
        ):
            raise RuntimeError("Intercompany SQL tests require approved disposable CI site")
        super().setUp()
        frappe.set_user("Administrator")
        self.marker_names = []

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def _real_sql_marker(self, invoice):
        # No Sales/Purchase Invoices are inserted in this fixture. The marker
        # distinguishes actual database rollback from mock call assertions.
        doc = frappe.get_doc({
            "doctype": "ToDo",
            "description": "Disposable paired invoice SQL transaction probe",
            "status": "Open",
        }).insert(ignore_permissions=True)
        invoice.name = doc.name
        self.marker_names.append(doc.name)
        return doc

    def _fake_invoice(self):
        doc = MagicMock()
        doc.name = "not-inserted"
        doc.insert.side_effect = lambda ignore_permissions: self._real_sql_marker(doc)
        return doc

    def _batch(self):
        doc = MagicMock(spec=IntercompanyMaterialBillingBatch)
        doc.name = "CI-SYNTHETIC-NOT-SAVED"
        doc.issuing_company = "_Test Company"
        doc.requesting_company = "_Test Company 2"
        doc.period_end = "2026-09-30"
        doc.total_amount = 100
        doc.status = "Under Review"
        doc.sales_invoice = None
        doc.purchase_invoice = None
        doc.approved_by = None
        doc.approved_on = None
        # This SQL-rollback fixture mocks invoice defaults, so supply explicit
        # identical synthetic currencies. Cross-currency denial is verified by
        # the separate approval-failure tests; never bypass the runtime guard.
        doc._get_company_invoice_defaults.side_effect = [
            {"currency": "INR"}, {"currency": "INR"}
        ]
        doc._get_internal_party.side_effect = ["CI Buyer", "CI Seller"]
        sales = self._fake_invoice()
        purchase = self._fake_invoice()
        doc._build_sales_invoice.return_value = sales
        doc._build_purchase_invoice.return_value = purchase
        return doc, sales, purchase

    def _approve(self, doc):
        return IntercompanyMaterialBillingBatch.approve_and_create_invoices(doc)

    def _assert_no_orphans(self, doc):
        for name in self.marker_names:
            self.assertFalse(frappe.db.exists("ToDo", name),
                             "A failed invoice pair left a real MariaDB row.")
        self.assertEqual(doc.status, "Under Review")
        self.assertIsNone(doc.sales_invoice)
        self.assertIsNone(doc.purchase_invoice)
        self.assertIsNone(doc.approved_by)
        self.assertIsNone(doc.approved_on)

    def test_a_second_document_insert_failure_rolls_back_first_sql_row(self):
        doc, sales, purchase = self._batch()
        purchase.insert.side_effect = RuntimeError("Injected second document failure")
        with self.assertRaisesRegex(RuntimeError, "Injected second document"):
            self._approve(doc)
        self.assertEqual(len(self.marker_names), 1)
        self._assert_no_orphans(doc)

    def test_b_cross_link_failure_rolls_back_both_sql_rows(self):
        doc, sales, purchase = self._batch()
        sales.save.side_effect = RuntimeError("Injected intercompany cross-link failure")
        with self.assertRaisesRegex(RuntimeError, "Injected intercompany cross-link"):
            self._approve(doc)
        self.assertEqual(len(self.marker_names), 2)
        self._assert_no_orphans(doc)

    def test_c_final_batch_write_failure_rolls_back_both_sql_rows(self):
        doc, sales, purchase = self._batch()
        doc._save_authorized_workflow_transition.side_effect = RuntimeError(
            "Injected batch finalization failure",
        )
        with self.assertRaisesRegex(RuntimeError, "Injected batch finalization"):
            self._approve(doc)
        self.assertEqual(len(self.marker_names), 2)
        self._assert_no_orphans(doc)

    def test_d_success_keeps_both_sql_rows_until_outer_transaction_ends(self):
        doc, sales, purchase = self._batch()
        outcome = self._approve(doc)
        self.assertEqual(len(self.marker_names), 2)
        for name in self.marker_names:
            self.assertTrue(frappe.db.exists("ToDo", name))
        self.assertEqual(doc.status, "Approved")
        self.assertEqual(outcome["sales_invoice"], sales.name)
        self.assertEqual(outcome["purchase_invoice"], purchase.name)
        self.assertEqual(sales.inter_company_invoice_reference, purchase.name)
        self.assertEqual(doc.sales_invoice, sales.name)
        self.assertEqual(doc.purchase_invoice, purchase.name)
        # The disposable test suite rolls back both markers after this case.

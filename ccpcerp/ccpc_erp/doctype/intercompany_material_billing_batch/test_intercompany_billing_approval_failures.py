"""Unit-level failure propagation checks for paired draft-invoice approval.

These deliberately mock document insertion and saving. They prove which later
steps are skipped after an exception; they DO NOT prove MariaDB transaction
rollback, deadlock behavior, or absence of persisted orphan invoices. Those
require a separately approved DEVELOPMENT database integration test.
"""

from unittest import TestCase
from unittest.mock import MagicMock

from ccpcerp.ccpc_erp.doctype.intercompany_material_billing_batch.intercompany_material_billing_batch import (
    IntercompanyMaterialBillingBatch,
)


class TestIntercompanyApprovalFailurePaths(TestCase):
    def setUp(self):
        self.batch = MagicMock(spec=IntercompanyMaterialBillingBatch)
        self.batch.issuing_company = "CCPC"
        self.batch.requesting_company = "RMC"
        self.batch.period_end = "2026-09-30"
        self.batch.total_amount = 10000
        self.batch.status = "Under Review"
        self.batch.sales_invoice = None
        self.batch.purchase_invoice = None

        self.batch._get_company_invoice_defaults.side_effect = [
            {"company": "CCPC"},
            {"company": "RMC"},
        ]
        self.batch._get_internal_party.side_effect = [
            "RMC Customer",
            "CCPC Supplier",
        ]
        self.sales_invoice = MagicMock()
        self.sales_invoice.name = "SINV-TEST-001"
        self.purchase_invoice = MagicMock()
        self.purchase_invoice.name = "PINV-TEST-001"
        self.batch._build_sales_invoice.return_value = self.sales_invoice
        self.batch._build_purchase_invoice.return_value = self.purchase_invoice

    def approve(self):
        return IntercompanyMaterialBillingBatch.approve_and_create_invoices(
            self.batch
        )

    def assert_not_marked_approved(self):
        self.assertEqual(self.batch.status, "Under Review")
        self.assertIsNone(self.batch.sales_invoice)
        self.assertIsNone(self.batch.purchase_invoice)
        self.batch._save_authorized_workflow_transition.assert_not_called()

    def test_sales_invoice_insert_failure_skips_purchase_and_batch_approval(self):
        self.sales_invoice.insert.side_effect = RuntimeError("injected sales insert failure")

        with self.assertRaisesRegex(RuntimeError, "injected sales insert failure"):
            self.approve()

        self.sales_invoice.insert.assert_called_once_with(ignore_permissions=True)
        self.batch._build_purchase_invoice.assert_not_called()
        self.purchase_invoice.insert.assert_not_called()
        self.sales_invoice.save.assert_not_called()
        self.assert_not_marked_approved()

    def test_purchase_invoice_insert_failure_skips_linking_and_batch_approval(self):
        self.purchase_invoice.insert.side_effect = RuntimeError(
            "injected purchase insert failure"
        )

        with self.assertRaisesRegex(RuntimeError, "injected purchase insert failure"):
            self.approve()

        self.sales_invoice.insert.assert_called_once_with(ignore_permissions=True)
        self.purchase_invoice.insert.assert_called_once_with(ignore_permissions=True)
        self.sales_invoice.save.assert_not_called()
        self.assert_not_marked_approved()

    def test_cross_link_save_failure_skips_batch_approval(self):
        self.sales_invoice.save.side_effect = RuntimeError("injected cross-link failure")

        with self.assertRaisesRegex(RuntimeError, "injected cross-link failure"):
            self.approve()

        self.sales_invoice.insert.assert_called_once_with(ignore_permissions=True)
        self.purchase_invoice.insert.assert_called_once_with(ignore_permissions=True)
        self.assertEqual(
            self.sales_invoice.inter_company_invoice_reference,
            self.purchase_invoice.name,
        )
        self.sales_invoice.save.assert_called_once_with(ignore_permissions=True)
        self.assert_not_marked_approved()

    def test_final_batch_save_failure_propagates(self):
        self.batch._save_authorized_workflow_transition.side_effect = RuntimeError(
            "injected batch save failure"
        )

        with self.assertRaisesRegex(RuntimeError, "injected batch save failure"):
            self.approve()

        self.sales_invoice.insert.assert_called_once_with(ignore_permissions=True)
        self.purchase_invoice.insert.assert_called_once_with(ignore_permissions=True)
        self.sales_invoice.save.assert_called_once_with(ignore_permissions=True)
        self.batch._save_authorized_workflow_transition.assert_called_once_with(
            "Under Review", "Approved"
        )
        # In-memory fields were assigned before the save raised. This does
        # NOT establish the database state or rollback: test that separately
        # using a real DEVELOPMENT MariaDB transaction.

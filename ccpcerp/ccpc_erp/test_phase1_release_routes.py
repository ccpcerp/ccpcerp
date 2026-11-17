# Copyright (c) 2026, CCPC and contributors
"""Targeted installed-Frappe route tests; no DB inserts or financial postings.

Run in a disposable Frappe/ERPNext test environment, not PRODUCTION.
"""
import unittest
from unittest.mock import patch

import frappe

from ccpcerp.ccpc_erp.withdrawal_slip_stock import (
    _get_issuable_slip,
    validate_withdrawal_slip_stock_entry,
)
from ccpcerp.ccpc_erp.doctype.intercompany_material_billing_batch.intercompany_material_billing_batch import (
    IntercompanyMaterialBillingBatch,
)


def _foreign_company_slip():
    return frappe._dict({
        "name": "SYNTHETIC-PHASE1-SLIP",
        "company": "Synthetic Issuer",
        "requesting_company": "Synthetic Requester",
        "status": "Stockroom Review",
        "received_by": "Synthetic Receiver",
        "source_warehouse": "Synthetic Warehouse",
        "equipment": None,
        "work_for_department": None,
        "owner": "synthetic-preparer@testing.invalid",
        "approving_supervisor": "synthetic-preparer@testing.invalid",
        "check_permission": lambda _: None,
    })


class TestPhase1ReleasedRoutes(unittest.TestCase):
    def test_stockroom_route_denies_foreign_company_before_building_issue(self):
        slip = _foreign_company_slip()
        locked = dict(slip)
        with patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_doc", return_value=slip), \
             patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._assert_independent_issuer") as issuer:
            with self.assertRaisesRegex(frappe.PermissionError, "Phase 1"):
                _get_issuable_slip(slip.name, locked)
            issuer.assert_not_called()

    def test_direct_linked_stock_entry_denies_before_native_posting(self):
        slip = _foreign_company_slip()
        stock = frappe._dict({
            "docstatus": 0,
            "withdrawal_slip": slip.name,
        })
        with patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._require_stockroom_user"), \
             patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._lock_withdrawal_slip", return_value=dict(slip)), \
             patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_doc", return_value=slip), \
             patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._assert_independent_issuer"), \
             patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._assert_no_submitted_material_issue"):
            with self.assertRaisesRegex(frappe.PermissionError, "Phase 1"):
                validate_withdrawal_slip_stock_entry(stock, method="before_submit")

    def test_intercompany_create_update_delete_and_invoice_actions_denied(self):
        billing = object.__new__(IntercompanyMaterialBillingBatch)
        blocked = (
            "before_validate", "validate", "on_trash",
            "refresh_preview", "finalize_month", "approve_and_create_invoices",
        )
        for name in blocked:
            with self.subTest(method=name):
                with self.assertRaisesRegex(frappe.PermissionError, "disabled"):
                    getattr(billing, name)()


if __name__ == "__main__":
    unittest.main()

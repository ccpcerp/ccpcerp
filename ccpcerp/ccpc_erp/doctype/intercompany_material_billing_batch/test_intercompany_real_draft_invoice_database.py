# Copyright (c) 2026, CCPC and contributors
"""Real ERPNext draft Sales/Purchase Invoice pairing in disposable MariaDB.

Genuine ERPNext Invoice.insert/save and GL absence, using two synthetic test
companies. A mocked, synthetic finalized billing batch supplies frozen rows.
NOT proof of real source Slip/Stock Entry eligibility, true Finance workflow,
invoice submission, returns, or persistent DEVELOPMENT readiness.
"""
import os
from unittest.mock import MagicMock

import frappe
from ccpcerp.ccpc_erp.ci_disposable_site_guard import is_reviewed_intercompany_disposable_site
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import flt, nowdate
from erpnext.stock.doctype.item.test_item import make_item
from ccpcerp.ccpc_erp.doctype.intercompany_material_billing_batch.intercompany_material_billing_batch import (
    IntercompanyMaterialBillingBatch as Billing,
)

BRANCH = "review/intercompany-invoice-transaction-2026-10-20"


class TestIntercompanyRealDraftInvoicePair(ERPNextTestSuite):
    def setUp(self):
        if not (
            is_reviewed_intercompany_disposable_site(getattr(frappe.local, "site", None))
            and os.environ.get("CCPC_ALLOW_IC_REAL_INVOICES") == "isolated-only"
        ):
            raise RuntimeError("Intercompany SQL tests require approved disposable CI site")
        super().setUp()
        frappe.set_user("Administrator")
        self.load_test_records("Company")
        self.load_test_records("Sales Invoice")
        # Both synthetic Companies use INR; currencies with no approved FX
        # crosswalk are deliberately denied by production billing logic.
        self.seller, self.buyer = "_Test Company", "_Test Company 5"
        for company in (self.seller, self.buyer):
            self.assertTrue(frappe.db.exists("Company", company),
                            "ERPNext synthetic Company test fixtures are required.")
        self.item = make_item(
            item_code="CI-PAIR-" + frappe.generate_hash(length=12),
            properties={"is_stock_item": 0, "stock_uom": "_Test UOM",
                        "item_group": "_Test Item Group"},
        )
        self.assertEqual(
            frappe.db.get_value("Company", self.seller, "default_currency"),
            frappe.db.get_value("Company", self.buyer, "default_currency"),
        )
        self.customer = self._party("Customer", self.buyer, self.seller)
        self.supplier = self._party("Supplier", self.seller, self.buyer)

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def _party(self, kind, represents, allowed):
        group_kind = "Customer Group" if kind == "Customer" else "Supplier Group"
        group = frappe.db.get_value(group_kind, {"is_group": 0}, "name")
        self.assertTrue(group, "Missing test " + group_kind)
        name = "CI-" + kind + "-" + frappe.generate_hash(length=12)
        values = {
            "doctype": kind,
            "customer_name" if kind == "Customer" else "supplier_name": name,
            "customer_group" if kind == "Customer" else "supplier_group": group,
            "is_internal_customer" if kind == "Customer" else "is_internal_supplier": 1,
            "represents_company": represents,
            "companies": [{"company": allowed}],
        }
        if kind == "Customer":
            values.update(customer_type="Company",
                          territory=frappe.db.get_value("Territory", {"is_group": 0}, "name"))
        else:
            values.update(supplier_type="Company")
        return frappe.get_doc(values).insert(ignore_permissions=True).name

    def _account(self, company, root_type, account_type=None, currency=None):
        filters = {"company": company, "is_group": 0, "disabled": 0,
                   "root_type": root_type}
        if account_type:
            filters["account_type"] = account_type
        if currency:
            # ERPNext's test company has both INR and USD party accounts.
            # Do not choose an arbitrary Receivable/Payable account that
            # conflicts with the real invoice currency validator.
            filters["account_currency"] = currency
        account = frappe.db.get_value("Account", filters, "name")
        self.assertTrue(account, "Missing eligible disposable account " + str(filters))
        if currency:
            self.assertEqual(
                frappe.db.get_value("Account", account, "account_currency"), currency,
                "Test party account must use the synthetic invoice currency",
            )
        return account

    def _defaults(self, company):
        cost_center = frappe.db.get_value("Cost Center", {
            "company": company, "is_group": 0, "disabled": 0,
        }, "name")
        currency = frappe.db.get_value("Company", company, "default_currency")
        self.assertTrue(cost_center and currency)
        return frappe._dict(
            currency=currency, cost_center=cost_center,
            receivable_account=self._account(company, "Asset", "Receivable", currency=currency),
            payable_account=self._account(company, "Liability", "Payable", currency=currency),
            income_account=self._account(company, "Income"),
            expense_account=self._account(company, "Expense"),
        )

    def _batch(self):
        # Deliberately simulate source state, but bind both REAL invoice builders
        # and REAL generated-invoice validation to the synthetic batch.
        obj = MagicMock(spec=Billing)
        obj.name = "CI-BATCH-" + frappe.generate_hash(length=12)
        obj.status = "Under Review"
        obj.issuing_company, obj.requesting_company = self.seller, self.buyer
        obj.period_end, obj.total_amount = nowdate(), 100
        obj.sales_invoice = obj.purchase_invoice = None
        obj.approved_by = obj.approved_on = None
        obj.items = [frappe._dict(item_code=self.item.name, quantity=2,
                                  stock_uom=self.item.stock_uom,
                                  frozen_basic_rate=50,
                                  requesting_department=None, equipment=None)]
        obj._get_company_invoice_defaults.side_effect = [
            self._defaults(self.seller), self._defaults(self.buyer),
        ]
        obj._get_internal_party.side_effect = [self.customer, self.supplier]
        obj._build_sales_invoice.side_effect = (
            lambda customer, defaults: Billing._build_sales_invoice(obj, customer, defaults)
        )
        obj._build_purchase_invoice.side_effect = (
            lambda supplier, defaults, sales: Billing._build_purchase_invoice(
                obj, supplier, defaults, sales,
            )
        )
        obj._validate_generated_invoice.side_effect = (
            lambda invoice: Billing._validate_generated_invoice(obj, invoice)
        )
        return obj

    def test_a_real_draft_pair_cross_links_and_has_no_gl(self):
        batch = self._batch()
        result = Billing.approve_and_create_invoices(batch)
        si = frappe.get_doc("Sales Invoice", result["sales_invoice"])
        pi = frappe.get_doc("Purchase Invoice", result["purchase_invoice"])
        self.assertEqual((si.company, pi.company), (self.seller, self.buyer))
        self.assertEqual((si.customer, pi.supplier), (self.customer, self.supplier))
        self.assertEqual(si.inter_company_invoice_reference, pi.name)
        self.assertEqual(pi.inter_company_invoice_reference, si.name)
        self.assertEqual((si.docstatus, pi.docstatus), (0, 0))
        self.assertFalse(si.update_stock or pi.update_stock)
        self.assertFalse(si.taxes or pi.taxes)
        self.assertAlmostEqual(flt(si.grand_total), 100, places=2)
        self.assertAlmostEqual(flt(pi.grand_total), 100, places=2)
        for doctype, name in (("Sales Invoice", si.name), ("Purchase Invoice", pi.name)):
            self.assertEqual(frappe.db.count("GL Entry", {
                "voucher_type": doctype, "voucher_no": name,
            }), 0, "An unsubmitted draft invoice must not post General Ledger entries.")
        self.assertEqual(batch.status, "Approved")

    def test_b_real_sales_draft_rolls_back_on_purchase_builder_failure(self):
        batch = self._batch()
        created = []
        original = batch._build_sales_invoice.side_effect
        def record_sales(customer, defaults):
            doc = original(customer, defaults)
            created.append(doc)
            return doc
        batch._build_sales_invoice.side_effect = record_sales
        batch._build_purchase_invoice.side_effect = RuntimeError("CI injected purchase builder failure")
        with self.assertRaisesRegex(RuntimeError, "injected purchase builder failure"):
            Billing.approve_and_create_invoices(batch)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].name)
        self.assertFalse(frappe.db.exists("Sales Invoice", created[0].name),
                         "Real SQL rollback must remove the first invoice.")
        self.assertEqual(batch.status, "Under Review")
        self.assertIsNone(batch.sales_invoice)
        self.assertIsNone(batch.purchase_invoice)

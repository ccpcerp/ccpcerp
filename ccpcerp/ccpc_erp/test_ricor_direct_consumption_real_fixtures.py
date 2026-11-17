# Copyright (c) 2026, CCPC and contributors
"""Opt-in real MariaDB fixture tests; never run on a persistent ERPNext site.

Fixtures are intentionally generated inside the disposable CI test transaction.
They are NOT accounting signoffs or persistent Ricor User/Item master changes.
The dedicated CI job/site must be separately reviewed and explicitly run.
"""

import os

import frappe
from erpnext.tests.utils import ERPNextTestSuite
from erpnext.stock.doctype.item.test_item import make_item
from frappe.utils import flt, nowdate

from ccpcerp.ccpc_erp.bc_issue_runtime import _real_authority
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
from ccpcerp.ccpc_erp.ricor_direct_consumption_policy import (
    DirectConsumptionBillingItemError,
    validate_direct_consumption_billing_item,
)

BRANCH = "review/ricor-direct-consumption-billing-item-2026-11-17"
SITE = "ci-ricor-direct.localhost"
COMPANY = "_Test Company"
OTHER_COMPANY = "_Test Company 5"
GROUP = "_Test Item Group"
UOM = "_Test UOM"


def _disposable_only():
    env = os.environ
    return (
        getattr(frappe.local, "site", None) == SITE
        and env.get("CIRCLECI") == "true"
        and env.get("CIRCLE_PROJECT_USERNAME") == "CCPCerp_2026"
        and env.get("CIRCLE_PROJECT_REPONAME") == "ccpcerp"
        and env.get("CIRCLE_BRANCH") == BRANCH
        and env.get("CIRCLE_JOB") == "ricor-direct-consumption-integration"
        and bool(env.get("CIRCLE_PIPELINE_ID"))
        and bool(env.get("CIRCLE_WORKFLOW_ID"))
        and bool(env.get("CIRCLE_BUILD_NUM"))
        and len(env.get("CIRCLE_SHA1", "")) == 40
        and env.get("CIRCLE_SHA1") == env.get("CCPC_RICOR_APPROVED_SHA")
        and env.get("CCPC_ALLOW_RICOR_DIRECT_FIXTURES") == "isolated-only"
    )


class TestRicorDirectConsumptionRealFixtures(ERPNextTestSuite):
    def setUp(self):
        if not _disposable_only():
            raise RuntimeError("Ricor fixture tests are confined to dedicated disposable CircleCI site")
        super().setUp()
        frappe.set_user("Administrator")
        self.load_test_records("Stock Entry")
        self.assertTrue(frappe.db.exists("Company", COMPANY))
        self.assertTrue(frappe.db.exists("Company", OTHER_COMPANY))
        self.assertEqual(frappe.db.get_value("Item Group", GROUP, "is_group"), 0)
        self.assertTrue(frappe.db.exists("UOM", UOM))

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def test_actual_nonstock_item_created_and_fixed_asset_rejected(self):
        code = "CI-DIRECT-CHARGE-" + frappe.generate_hash(length=12)
        item = make_item(
            item_code=code,
            properties={
                "item_group": GROUP,
                "stock_uom": UOM,
                "is_stock_item": 0,
                "is_purchase_item": 1,
                "is_fixed_asset": 0,
                "enable_deferred_expense": 0,
                "has_variants": 0,
                "disabled": 0,
            },
        )
        fields = [
            "name", "item_group", "stock_uom", "disabled", "is_stock_item",
            "is_purchase_item", "is_fixed_asset", "enable_deferred_expense",
            "has_variants", "variant_of",
        ]
        actual = frappe.db.get_value("Item", item.name, fields, as_dict=True)
        self.assertEqual(
            validate_direct_consumption_billing_item(
                source_item_code="CI-ORIGINAL-STOCK", frozen_source_uom=UOM,
                candidate=actual, item_group_is_leaf=True,
            ),
            item.name,
        )
        actual.is_fixed_asset = 1
        with self.assertRaises(DirectConsumptionBillingItemError):
            validate_direct_consumption_billing_item(
                source_item_code="CI-ORIGINAL-STOCK", frozen_source_uom=UOM,
                candidate=actual, item_group_is_leaf=True,
            )

    def test_native_nonstock_purchase_invoice_retains_expense_and_no_postings(self):
        """Real ERPNext PI.insert/validate, not a fake accounting field assignment."""
        code = "CI-DIRECT-PI-" + frappe.generate_hash(length=12)
        material = make_item(
            item_code=code,
            properties={
                "item_group": GROUP, "stock_uom": UOM, "is_stock_item": 0,
                "is_purchase_item": 1, "is_fixed_asset": 0,
                "enable_deferred_expense": 0, "has_variants": 0, "disabled": 0,
            },
        )
        item_fields = [
            "name", "item_group", "stock_uom", "disabled", "is_stock_item",
            "is_purchase_item", "is_fixed_asset", "enable_deferred_expense",
            "has_variants", "variant_of",
        ]
        item_row = frappe.db.get_value("Item", material.name, item_fields, as_dict=True)
        self.assertEqual(
            validate_direct_consumption_billing_item(
                source_item_code="CI-ORIGINAL-STOCK",
                frozen_source_uom=UOM,
                candidate=item_row, item_group_is_leaf=True,
            ),
            material.name,
        )
        supplier_group = frappe.db.get_value(
            "Supplier Group", {"is_group": 0}, "name",
        )
        self.assertTrue(supplier_group)
        supplier = frappe.get_doc({
            "doctype": "Supplier",
            "supplier_name": "CI Direct Supplier " + frappe.generate_hash(length=10),
            "supplier_type": "Company",
            "supplier_group": supplier_group,
        }).insert(ignore_permissions=True)
        currency = frappe.db.get_value("Company", COMPANY, "default_currency")
        payable = frappe.db.get_value("Account", {
            "company": COMPANY, "is_group": 0, "disabled": 0,
            "root_type": "Liability", "account_type": "Payable",
            "account_currency": currency,
        }, "name")
        expense = frappe.db.get_value("Account", {
            "company": COMPANY, "is_group": 0, "disabled": 0,
            "root_type": "Expense",
        }, "name")
        center = frappe.db.get_value("Cost Center", {
            "company": COMPANY, "is_group": 0, "disabled": 0,
        }, "name")
        self.assertTrue(currency and payable and expense and center)
        invoice = frappe.get_doc({
            "doctype": "Purchase Invoice",
            "company": COMPANY, "supplier": supplier.name,
            "posting_date": nowdate(), "currency": currency,
            "credit_to": payable, "update_stock": 0, "is_opening": "No",
            "items": [{
                "item_code": material.name, "qty": 2, "uom": UOM,
                "conversion_factor": 1, "rate": 50, "expense_account": expense,
                "cost_center": center,
            }],
        })
        invoice.insert(ignore_permissions=True)
        saved = frappe.get_doc("Purchase Invoice", invoice.name)
        self.assertEqual(saved.docstatus, 0)
        self.assertEqual(saved.company, COMPANY)
        self.assertFalse(saved.update_stock)
        self.assertEqual(len(saved.items), 1)
        self.assertEqual(saved.items[0].item_code, material.name)
        self.assertEqual(saved.items[0].expense_account, expense)
        self.assertEqual(saved.items[0].cost_center, center)
        self.assertEqual(saved.items[0].uom, UOM)
        self.assertAlmostEqual(flt(saved.items[0].qty), 2)
        self.assertAlmostEqual(flt(saved.items[0].rate), 50)
        self.assertAlmostEqual(flt(saved.grand_total), 100, places=2)
        for ledger in ("GL Entry", "Stock Ledger Entry"):
            self.assertEqual(frappe.db.count(ledger, {
                "voucher_type": "Purchase Invoice", "voucher_no": saved.name,
            }), 0)

    def _role(self, name):
        if not frappe.db.exists("Role", name):
            frappe.get_doc({
                "doctype": "Role", "role_name": name, "desk_access": 1,
            }).insert(ignore_permissions=True)

    def _actor(self, label, role, *, with_company_grant):
        self._role(role)
        email = "ci-ricor-" + label + "-" + frappe.generate_hash(length=10) + "@testing.invalid"
        frappe.get_doc({
            "doctype": "User", "email": email,
            "first_name": "CI TEST " + label,
            "enabled": 1, "user_type": "System User",
            "send_welcome_email": 0,
            "roles": [{"role": role}],
        }).insert(ignore_permissions=True)
        if with_company_grant:
            frappe.get_doc({
                "doctype": "User Permission",
                "user": email, "allow": "Company",
                "for_value": COMPANY,
                "apply_to_all_doctypes": 1,
            }).insert(ignore_permissions=True)
        return email

    def test_distinct_mapping_fixture_actors_and_wrong_company_denial(self):
        preparer_role = "CCPC Mapping Preparer"
        approver_role = "CCPC Accounting Mapping Approver"
        preparer = self._actor("preparer", preparer_role, with_company_grant=True)
        checker = self._actor("checker", approver_role, with_company_grant=True)
        self.assertNotEqual(preparer, checker)
        _real_authority(preparer, preparer_role, COMPANY)
        _real_authority(checker, approver_role, COMPANY)
        with self.assertRaises(MappingPolicyError):
            _real_authority(preparer, preparer_role, OTHER_COMPANY)
        with self.assertRaises(MappingPolicyError):
            _real_authority(preparer, approver_role, COMPANY)
        lacking_grant = self._actor("ungranted", preparer_role, with_company_grant=False)
        with self.assertRaises(MappingPolicyError):
            _real_authority(lacking_grant, preparer_role, COMPANY)

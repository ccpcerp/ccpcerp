# Copyright (c) 2026, CCPC and contributors
"""Genuine disposable Frappe/MariaDB test of INERT B/C schema only.

NO live CCPC company or mapping values, NO stock or GL posting. Guard is
intentionally before fixture setup and must not be relaxed for a local bench.
"""

import os

import frappe
from erpnext.tests.utils import ERPNextTestSuite


SITE = "ci-bc-mapping.localhost"
BRANCH = "review/bc-mapping-scopes-revisions-2026-10-18"


class TestBCMappingRealIntegration(ERPNextTestSuite):
    def setUp(self):
        if not (
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "ccpcerp/ccpcerp"
            and os.environ.get("GITHUB_REF") == "refs/heads/" + BRANCH
            and os.environ.get("CCPC_ALLOW_BC_SCHEMA_PROBE") == "isolated-only"
            and getattr(frappe.local, "site", None) == SITE
        ):
            raise RuntimeError("B/C schema test restricted to disposable GitHub Actions site")
        frappe.set_user("Administrator")
        self.load_test_records("Stock Entry")
        self.company = "_Test Company"
        self.assertTrue(frappe.db.exists("Company", self.company))

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def test_real_all_four_schema_tables_and_absent_role_grants(self):
        for doctype in (
            "CCPC Expense Mapping Scope", "CCPC Expense Mapping Revision",
            "CCPC Center Mapping Scope", "CCPC Center Mapping Revision",
        ):
            with self.subTest(doctype=doctype):
                self.assertTrue(frappe.db.table_exists(doctype))
                meta = frappe.get_meta(doctype)
                self.assertEqual(list(meta.permissions), [])
                self.assertTrue(meta.has_field("scope_key" if doctype.endswith("Scope") else "revision_key"))
                self.assertEqual(frappe.db.count(doctype), 0)

    def test_real_expense_scope_leaf_and_unique_database_constraint(self):
        item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
        self.assertTrue(item_group, "Synthetic test fixture should have a leaf Item Group")
        data = {"doctype": "CCPC Expense Mapping Scope", "company": self.company, "item_group": item_group}
        first = frappe.get_doc(data).insert(ignore_permissions=True)
        self.assertEqual(len(first.scope_key), 64)
        same = frappe.get_doc(data)
        with self.assertRaises(Exception):
            same.insert(ignore_permissions=True)
        self.assertEqual(frappe.db.count("CCPC Expense Mapping Scope", {"scope_key": first.scope_key}), 1)

    def test_real_center_scope_beneficiary_company_and_unique_identity(self):
        name = "CI B/C Department"
        department = frappe.db.get_value("Department", {"company": self.company, "department_name": name}, "name")
        if not department:
            department = frappe.get_doc({
                "doctype": "Department", "department_name": name,
                "company": self.company, "is_group": 0, "disabled": 0,
            }).insert(ignore_permissions=True).name
        scope = frappe.get_doc({
            "doctype": "CCPC Center Mapping Scope",
            "company": self.company, "beneficiary_department": department,
            "selector_kind": "Department Only", "selector_key": "DEPARTMENT_ONLY",
        }).insert(ignore_permissions=True)
        self.assertTrue(scope.scope_key)
        self.assertEqual(frappe.db.get_value("CCPC Center Mapping Scope", scope.name, "scope_key"), scope.scope_key)
        rejected = frappe.get_doc({
            "doctype": "CCPC Center Mapping Scope",
            "company": self.company, "beneficiary_department": department,
            "selector_kind": "Explicit Selection", "selector_key": "DEPARTMENT_ONLY",
        })
        with self.assertRaises(frappe.ValidationError):
            rejected.before_validate()

    def test_real_no_finance_approval_activation_even_for_administrator(self):
        cases = [
            ("CCPC Expense Mapping Revision", "Stock Adjustment - _TC", "expense_account"),
            ("CCPC Center Mapping Revision", "_Test Cost Center 2 - _TC", "cost_center"),
        ]
        for doctype, value, field in cases:
            with self.subTest(doctype=doctype):
                doc = frappe.get_doc({
                    "doctype": doctype,
                    "mapping_scope": "nonexistent-disposable-review",
                    "revision_no": 1,
                    field: value,
                    "approval_state": "Approved",
                })
                doc.before_validate()
                with self.assertRaises(frappe.ValidationError):
                    doc.validate()

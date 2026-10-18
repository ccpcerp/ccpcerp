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

    def test_real_draft_expense_revision_eligible_company_and_no_group_account(self):
        item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
        scope = frappe.get_doc({
            "doctype": "CCPC Expense Mapping Scope",
            "company": self.company, "item_group": item_group,
        }).insert(ignore_permissions=True)
        account = "Stock Adjustment - _TC"  # Disposable ERPNext fixture only; NOT CCPC-approved.
        details = frappe.db.get_value(
            "Account", account, ["company", "is_group", "disabled", "root_type"], as_dict=True,
        )
        self.assertEqual(details.company, self.company)
        self.assertEqual(details.root_type, "Expense")
        self.assertFalse(details.is_group or details.disabled)
        draft = frappe.get_doc({
            "doctype": "CCPC Expense Mapping Revision",
            "mapping_scope": scope.name, "revision_no": 1,
            "expense_account": account, "approval_state": "Draft",
        }).insert(ignore_permissions=True)
        self.assertEqual(len(draft.revision_key), 64)
        self.assertEqual(draft.approval_state, "Draft")
        self.assertIsNone(draft.approved_by)
        self.assertIsNone(draft.approved_at)

        group_account = frappe.db.get_value(
            "Account", {"company": self.company, "is_group": 1}, "name",
        )
        self.assertTrue(group_account)
        invalid = frappe.get_doc({
            "doctype": "CCPC Expense Mapping Revision",
            "mapping_scope": scope.name, "revision_no": 2,
            "expense_account": group_account, "approval_state": "Draft",
        })
        with self.assertRaises(frappe.ValidationError):
            invalid.insert(ignore_permissions=True)

        # A second revision may not silently reuse the first immutable key.
        duplicate = frappe.get_doc({
            "doctype": "CCPC Expense Mapping Revision",
            "mapping_scope": scope.name, "revision_no": 1,
            "expense_account": account, "approval_state": "Draft",
        })
        with self.assertRaises(Exception):
            duplicate.insert(ignore_permissions=True)
        self.assertEqual(frappe.db.count(
            "CCPC Expense Mapping Revision", {"revision_key": draft.revision_key}
        ), 1)

    def test_real_draft_center_revision_valid_leaf_and_reject_group_center(self):
        name = "CI B/C Department"
        department = frappe.db.get_value(
            "Department", {"company": self.company, "department_name": name}, "name",
        )
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
        center = "_Test Cost Center 2 - _TC"  # Disposable ERPNext fixture only.
        details = frappe.db.get_value(
            "Cost Center", center, ["company", "is_group", "disabled"], as_dict=True,
        )
        self.assertEqual(details.company, self.company)
        self.assertFalse(details.is_group or details.disabled)
        draft = frappe.get_doc({
            "doctype": "CCPC Center Mapping Revision",
            "mapping_scope": scope.name, "revision_no": 1,
            "cost_center": center, "approval_state": "Draft",
        }).insert(ignore_permissions=True)
        self.assertEqual(draft.approval_state, "Draft")
        self.assertEqual(len(draft.revision_key), 64)

        group_center = frappe.db.get_value(
            "Cost Center", {"company": self.company, "is_group": 1}, "name",
        )
        self.assertTrue(group_center)
        invalid = frappe.get_doc({
            "doctype": "CCPC Center Mapping Revision",
            "mapping_scope": scope.name, "revision_no": 2,
            "cost_center": group_center, "approval_state": "Draft",
        })
        with self.assertRaises(frappe.ValidationError):
            invalid.insert(ignore_permissions=True)

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

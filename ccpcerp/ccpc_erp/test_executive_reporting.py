# Copyright (c) 2026, CCPC and contributors
"""No-live-data tests for the locked four-Company executive reporting gateway."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe

from ccpcerp.ccpc_erp import executive_reporting as gateway
from ccpcerp.ccpc_erp.report.ccpc_department_material_consumption import (
    ccpc_department_material_consumption as department,
)


class TestCCPCGroupExecutiveReporting(TestCase):
    def setUp(self):
        self.role = gateway.EXECUTIVE_ROLE
        self.company = "Cagayan Corn Products Corporation"
        self.other = "Ricor Mills Corporation"
        self.filters = {
            "company": self.company, "from_date": "2026-10-01", "to_date": "2026-10-02",
        }

    def test_only_four_authoritative_company_ids_are_configured(self):
        from ccpcerp.patches.v1_0.create_intercompany_parties import COMPANIES
        self.assertEqual(gateway.COMPANIES, dict(COMPANIES))
        self.assertEqual(len(gateway.COMPANIES), 4)

    def test_unlisted_company_rejected_before_source_execution(self):
        with (
            patch.object(gateway, "_deny", side_effect=PermissionError("denied")),
            patch.object(gateway, "_check_group_company",
                         wraps=gateway._check_group_company),
        ):
            with self.assertRaisesRegex(PermissionError, "denied"):
                gateway._filters("department", {
                    **self.filters, "company": "Integration Test Issuer",
                })
            with self.assertRaisesRegex(PermissionError, "denied"):
                gateway._filters("stock_gl", {
                    **self.filters, "company": "Unrelated Historical Company",
                })

    def test_nonexistent_or_wrong_company_abbreviation_is_denied(self):
        with (
            patch.object(gateway.frappe.db, "get_value", return_value="TISS"),
            patch.object(gateway, "_deny", side_effect=PermissionError("denied")),
            self.assertRaisesRegex(PermissionError, "denied"),
        ):
            gateway._filters("department", self.filters)

    def test_report_name_injection_or_unknown_filters_rejected(self):
        with (
            patch.object(gateway, "_deny", side_effect=PermissionError("denied")),
        ):
            for report in ("other", "__import__", None):
                with self.assertRaisesRegex(PermissionError, "denied"):
                    gateway._filters(report, self.filters)
            for extra in ("company_name", "or_where", "ignore_permissions"):
                with self.assertRaisesRegex(PermissionError, "denied"):
                    gateway._filters("department", {**self.filters, extra: "1"})

    def test_optional_master_filters_not_exposed_by_executive_gateway(self):
        # Native System Manager reports retain Department/Equipment filters,
        # but this executive-only endpoint grants no master-record DocPerm.
        with patch.object(gateway, "_deny", side_effect=PermissionError("denied")):
            for report_key, optional in (
                ("department", {"department": "Sensitive Department"}),
                ("equipment", {"equipment": "Sensitive Equipment"}),
            ):
                with self.subTest(report=report_key):
                    with self.assertRaisesRegex(PermissionError, "denied"):
                        gateway._filters(report_key, {**self.filters, **optional})

    def test_intercompany_needs_two_distinct_approved_companies(self):
        with (
            patch.object(gateway.frappe.db, "get_value",
                         side_effect=lambda dt, name, key: gateway.COMPANIES.get(name)),
            patch.object(gateway, "_deny", side_effect=PermissionError("denied")),
        ):
            valid = {
                "issuing_company": self.company,
                "requesting_company": self.other,
                "from_date": "2026-10-01", "to_date": "2026-10-02",
            }
            self.assertEqual(
                gateway._filters("intercompany", valid)[1:],
                (self.company, self.other),
            )
            for other in (self.company, "Integration Test Requester"):
                with self.assertRaisesRegex(PermissionError, "denied"):
                    gateway._filters("intercompany",
                                     {**valid, "requesting_company": other})

    def test_guest_admin_disabled_and_unassigned_role_rejected(self):
        with patch.object(gateway, "_deny", side_effect=PermissionError("denied")):
            for user in ("Guest", "Administrator"):
                with (
                    patch.object(gateway.frappe, "session", SimpleNamespace(user=user)),
                    self.assertRaisesRegex(PermissionError, "denied"),
                ):
                    gateway._actor_is_authorized()
            with (
                patch.object(gateway.frappe, "session", SimpleNamespace(user="viewer@example.test")),
                patch.object(gateway.frappe, "get_roles", return_value=["Employee"]),
                self.assertRaisesRegex(PermissionError, "denied"),
            ):
                gateway._actor_is_authorized()
            with (
                patch.object(gateway.frappe, "session", SimpleNamespace(user="viewer@example.test")),
                patch.object(gateway.frappe, "get_roles", return_value=[self.role]),
                patch.object(gateway.frappe.db, "get_value",
                             return_value=frappe._dict(enabled=0, user_type="System User")),
                self.assertRaisesRegex(PermissionError, "denied"),
            ):
                gateway._actor_is_authorized()

    def test_authorized_context_encloses_only_one_call_and_resets(self):
        self.assertFalse(gateway.in_executive_report())
        def execute(filters):
            self.assertTrue(gateway.in_executive_report())
            self.assertEqual(filters, self.filters)
            return [{"fieldname": "company", "label": "Company"}], [
                {"company": self.company, "perspective": "STOCK_ISSUE_VOUCHER_COMPARISON"},
            ]
        with (
            patch.object(gateway, "_actor_is_authorized"),
            patch.object(gateway.frappe.db, "get_value",
                         side_effect=lambda dt, name, key: gateway.COMPANIES.get(name)),
            patch.object(gateway, "import_module",
                         return_value=SimpleNamespace(execute=execute)),
        ):
            result = gateway.run_group_report("stock_gl", self.filters)
        self.assertEqual(len(result["rows"]), 1)
        self.assertFalse(gateway.in_executive_report())

    def test_context_resets_on_bad_company_row_and_does_not_leak(self):
        def execute(_filters):
            return [], [{"issuing_company": "Integration Test Issuer"}]
        with (
            patch.object(gateway, "_actor_is_authorized"),
            patch.object(gateway.frappe.db, "get_value",
                         side_effect=lambda dt, name, key: gateway.COMPANIES.get(name)),
            patch.object(gateway, "import_module",
                         return_value=SimpleNamespace(execute=execute)),
            patch.object(gateway, "_deny", side_effect=PermissionError("denied")),
        ):
            with self.assertRaisesRegex(PermissionError, "denied"):
                gateway.run_group_report("department", self.filters)
        self.assertFalse(gateway.in_executive_report())

    def test_original_native_report_still_denies_viewer_without_context(self):
        self.assertFalse(gateway.in_executive_report())
        with (
            patch.object(department.frappe, "get_roles", return_value=[self.role]),
            patch.object(department.frappe, "throw",
                         side_effect=PermissionError("native report denied")),
            self.assertRaisesRegex(PermissionError, "native report denied"),
        ):
            department._require_access()

    def test_reject_foreign_linked_masters_and_billing_claims(self):
        with (
            patch.object(gateway, "_deny", side_effect=PermissionError("denied")),
            patch.object(gateway.frappe.db, "get_value", return_value=self.other),
        ):
            with self.assertRaisesRegex(PermissionError, "denied"):
                gateway._assert_returned_companies(
                    "department",
                    [{"issuing_company": self.company,
                      "detail_department": "Foreign master"}],
                    self.company, None,
                )
            with self.assertRaisesRegex(PermissionError, "denied"):
                gateway._assert_returned_companies(
                    "equipment",
                    [{"issuing_company": self.company,
                      "source_equipment": "Foreign equipment"}],
                    self.company, None,
                )
            with self.assertRaisesRegex(PermissionError, "denied"):
                gateway._assert_returned_companies(
                    "intercompany",
                    [{"issuing_company": self.company,
                      "requesting_company": self.other,
                      "source_quality": "BILLING_COMPANY_MISMATCH"}],
                    self.company, self.other,
                )

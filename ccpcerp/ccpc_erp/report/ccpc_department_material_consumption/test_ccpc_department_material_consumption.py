# Copyright (c) 2026, CCPC and contributors
"""Focused source/accounting/permission tests (Frappe unit, no real SQL site)."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from ccpcerp.ccpc_erp.report.ccpc_department_material_consumption import (
    ccpc_department_material_consumption as report,
)


class TestCCPCDepartmentMaterialConsumption(TestCase):
    @staticmethod
    def source(**change):
        values = {
            "posting_date": "2026-10-15",
            "issuing_company": "_Test Company",
            "issuing_currency": "INR",
            "stock_entry": "MAT-ISSUE-1",
            "stock_entry_detail": "DETAIL-1",
            "withdrawal_slip": "WS-1",
            "withdrawal_slip_item": "WSI-1",
            "item_code": "_Test Item",
            "item_name": "Sample",
            "stock_uom": "_Test UOM",
            "issued_qty": 2,
            "detail_department": "CI Department",
            "header_department": "CI Department",
            "company_department": "CI Department",
            "matched_slip_item": "WSI-1",
            "slip_item_code": "_Test Item",
            "sle_issued_qty": 2,
            "gross_sle_issue_value": 100,
            "sle_row_count": 2,
        }
        values.update(change)
        return frappe._dict(values)

    def presented(self, *values):
        with patch.object(report, "_assert_department_visibility"):
            return report._present(list(values))

    def test_sql_uses_exact_submitted_original_voucher_and_issue_detail(self):
        sql = report._source_sql()
        self.assertIn("WHERE se.docstatus = 1", sql)
        self.assertIn("AND se.purpose = 'Material Issue'", sql)
        self.assertIn("ws.name = se.withdrawal_slip", sql)
        self.assertIn("AND ws.company = se.company", sql)
        self.assertIn("AND se.company = %(company)s", sql)
        self.assertIn("sed.parent = se.name", sql)
        self.assertIn("sed.parenttype = 'Stock Entry'", sql)
        self.assertIn("sle.voucher_type = 'Stock Entry'", sql)
        self.assertIn("AND sle.actual_qty < 0", sql)
        self.assertIn("AND sle.is_cancelled = 0", sql)
        self.assertIn("GROUP BY sle.voucher_no, sle.voucher_detail_no, sle.company", sql)
        self.assertIn("source_sle.voucher_detail_no = sed.name", sql)
        self.assertIn("source_sle.company = se.company", sql)
        self.assertNotIn("frappe.db.commit(", report.__dict__.get("__doc__", ""))

    def test_static_bound_filters_and_bounded_row_limit(self):
        sql = report._source_sql()
        self.assertIn("se.posting_date BETWEEN %(from_date)s AND %(to_date)s", sql)
        self.assertIn("sle.posting_date BETWEEN %(from_date)s AND %(to_date)s", sql)
        self.assertIn("LIMIT %(row_limit)s", sql)
        self.assertNotIn("%(department)s", sql)
        filtered = report._source_sql(True)
        self.assertIn(
            "sed.department = %(department)s OR se.department = %(department)s",
            filtered,
        )
        # The filter includes discrepancies against one matching source
        # rather than hiding them under a prefiltered verified-only join.
        self.assertNotIn("dept.name = %(department)s", filtered)

    def test_valid_department_provenance_and_no_netting_claim(self):
        rows = self.presented(self.source())
        self.assertEqual(len(rows), 1)
        line = rows[0]
        self.assertEqual(line["beneficiary_department_at_issue"], "CI Department")
        self.assertEqual(line["source_quality"], "OK")
        self.assertEqual(line["sle_issued_qty"], 2)
        self.assertEqual(line["gross_sle_issue_value"], 100)
        self.assertEqual(line["issuing_currency"], "INR")
        self.assertEqual(line["section_provenance"], "NO_ORIGINAL_SECTION_SNAPSHOT")
        self.assertIn("not GL, returns", line["scope_note"])

    def test_protected_department_conflict_not_relabelled_to_requester(self):
        line = self.presented(self.source(
            detail_department="Different Department",
        ))[0]
        self.assertIsNone(line["beneficiary_department_at_issue"])
        self.assertIn("DEPARTMENT_ROW_HEADER_CONFLICT", line["source_quality"])
        self.assertEqual(line["gross_sle_issue_value"], 100)
        self.assertNotIn("requesting_department", line)
        self.assertNotIn("requesting_section", line)

    def test_missing_foreign_or_legacy_department_is_unattributed(self):
        for change, code in (
            ({"detail_department": None, "header_department": None,
              "company_department": None}, "MISSING_ISSUE_DEPARTMENT"),
            ({"company_department": None}, "DEPARTMENT_MASTER_MISSING_OR_OTHER_COMPANY"),
        ):
            line = self.presented(self.source(**change))[0]
            self.assertIsNone(line["beneficiary_department_at_issue"])
            self.assertIn(code, line["source_quality"])

    def test_absent_sle_is_null_not_zero_and_quantity_mismatch_is_flagged(self):
        absent = self.presented(self.source(
            sle_issued_qty=None, gross_sle_issue_value=None, sle_row_count=None,
        ))[0]
        self.assertIsNone(absent["gross_sle_issue_value"])
        self.assertIsNone(absent["sle_issued_qty"])
        self.assertIn("NO_ORIGINAL_OUTBOUND_SLE", absent["source_quality"])

        mismatch = self.presented(self.source(sle_issued_qty=1.75))[0]
        self.assertIn("SLE_QUANTITY_MISMATCH", mismatch["source_quality"])
        negative = self.presented(self.source(gross_sle_issue_value=-100))[0]
        self.assertIn("UNEXPECTED_SLE_VALUE_SIGN", negative["source_quality"])

    def test_forged_slip_detail_membership_and_item_are_flagged(self):
        orphan = self.presented(self.source(matched_slip_item=None))[0]
        self.assertIn("SLIP_ITEM_LINK_INVALID", orphan["source_quality"])
        changed = self.presented(self.source(slip_item_code="Other Item"))[0]
        self.assertIn("SLIP_ITEM_CODE_MISMATCH", changed["source_quality"])

    def test_duplicate_original_detail_fails_closed(self):
        with (
            patch.object(report, "_assert_department_visibility"),
            patch.object(report.frappe, "throw",
                         side_effect=RuntimeError("duplicate source")),
            self.assertRaisesRegex(RuntimeError, "duplicate source"),
        ):
            report._present([self.source(), self.source(stock_entry="OTHER")])

    def test_report_currency_columns_and_no_sum_or_accidental_totals(self):
        columns = {c["fieldname"]: c for c in report.get_columns()}
        self.assertEqual(
            columns["gross_sle_issue_value"]["options"], "issuing_currency"
        )
        self.assertEqual(columns["gross_sle_issue_value"]["fieldtype"], "Currency")
        self.assertFalse(any(
            key in columns for key in ("grand_total", "net_consumption", "total_expense")
        ))
        self.assertNotIn("SUM(CASE WHEN", report._source_sql())
        self.assertNotIn("tabGL Entry", report._source_sql())
        self.assertNotIn("tabIntercompany Material Billing Item", report._source_sql())

    def test_role_and_doctype_permission_fail_before_query(self):
        with (
            patch.object(report.frappe, "get_roles", return_value=["Stockroom Personnel"]),
            patch.object(report.frappe, "throw",
                         side_effect=PermissionError("role denied")),
            self.assertRaisesRegex(PermissionError, "role denied"),
        ):
            report._require_access()
        with (
            patch.object(report.frappe, "get_roles", return_value=["System Manager"]),
            patch.object(report.frappe, "has_permission", return_value=False),
            patch.object(report.frappe, "throw",
                         side_effect=PermissionError("doctype denied")),
            self.assertRaisesRegex(PermissionError, "doctype denied"),
        ):
            report._require_access()

    def test_filter_requires_company_permission_and_bounded_dates(self):
        class BlockCompany:
            def check_permission(self, *_):
                raise PermissionError("blocked company")

        with (
            patch.object(report.frappe.db, "exists", return_value=True),
            patch.object(report.frappe, "get_doc", return_value=BlockCompany()),
            self.assertRaisesRegex(PermissionError, "blocked company"),
        ):
            report._validate_filters(frappe._dict({
                "company": "_Test Company",
                "from_date": "2026-10-01", "to_date": "2026-10-31",
            }))

        class Allowed:
            company = "_Test Company"

            def check_permission(self, *_):
                pass

        with (
            patch.object(report.frappe.db, "exists", return_value=True),
            patch.object(report.frappe, "get_doc", return_value=Allowed()),
            patch.object(report.frappe, "throw",
                         side_effect=ValueError("invalid filter")),
        ):
            for vals in (
                {"from_date": None, "to_date": "2026-10-31"},
                {"from_date": "2026-11-02", "to_date": "2026-10-31"},
                {"from_date": "2025-01-01", "to_date": "2026-10-31"},
            ):
                with self.assertRaisesRegex(ValueError, "invalid filter"):
                    report._validate_filters(frappe._dict({
                        "company": "_Test Company", **vals,
                    }))

    def test_wrong_company_department_filter_denied(self):
        class Record:
            company = "_Other Company"

            def check_permission(self, *_):
                pass

        with (
            patch.object(report.frappe.db, "exists", return_value=True),
            patch.object(report.frappe, "get_doc", return_value=Record()),
            patch.object(report.frappe, "throw",
                         side_effect=ValueError("company mismatch")),
            self.assertRaisesRegex(ValueError, "company mismatch"),
        ):
            report._validate_filters(frappe._dict({
                "company": "_Test Company", "department": "Other Department",
                "from_date": "2026-10-01", "to_date": "2026-10-31",
            }))

    def test_departments_in_result_are_individually_permission_checked(self):
        class Hidden:
            def check_permission(self, *_):
                raise PermissionError("hidden department")

        with (
            patch.object(report.frappe.db, "exists", return_value=True),
            patch.object(report.frappe, "get_doc", return_value=Hidden()),
            self.assertRaisesRegex(PermissionError, "hidden department"),
        ):
            report._assert_department_visibility([
                self.source(detail_department="Not Permitted"),
            ])

    def test_report_does_not_silently_truncate_large_source(self):
        with (
            patch.object(report, "_require_access"),
            patch.object(report, "_validate_filters"),
            patch.object(report.frappe.db, "sql",
                         return_value=[object()] * (report.MAX_ROWS + 1)),
            patch.object(report.frappe, "throw",
                         side_effect=ValueError("narrow range")),
            self.assertRaisesRegex(ValueError, "narrow range"),
        ):
            report.execute({})


    def test_integrated_department_checks_stock_slip_item_and_both_departments(self):
        visited = []
        class Allowed:
            def check_permission(self, action):
                if action != "read":
                    raise AssertionError("expected read")
        def get_doc(doctype, name):
            visited.append((doctype, name))
            return Allowed()
        with (
            patch.object(report.frappe.db, "exists", return_value=True),
            patch.object(report.frappe, "get_doc", side_effect=get_doc),
        ):
            report._assert_department_visibility([
                self.source(detail_department="Division A",
                            header_department="Division B"),
                self.source(stock_entry_detail="SECOND", detail_department="Division A",
                            header_department="Division B"),
            ])
        self.assertEqual(visited, [
            ("Stock Entry", "MAT-ISSUE-1"),
            ("Withdrawal Slip", "WS-1"),
            ("Item", "_Test Item"),
            ("Department", "Division A"),
            ("Department", "Division B"),
        ])

    def test_integrated_department_hidden_stock_or_slip_fails_closed(self):
        class Controlled:
            def __init__(self, doctype, hidden):
                self.doctype = doctype
                self.hidden = hidden
            def check_permission(self, action):
                if action == "read" and self.doctype == self.hidden:
                    raise PermissionError("source record hidden")
        for hidden in ("Stock Entry", "Withdrawal Slip", "Item"):
            with (
                patch.object(report.frappe.db, "exists", return_value=True),
                patch.object(report.frappe, "get_doc",
                             side_effect=lambda dt, name: Controlled(dt, hidden)),
                self.assertRaisesRegex(PermissionError, "source record hidden"),
            ):
                report._assert_department_visibility([self.source()])

    def test_integrated_department_missing_legacy_master_does_not_forge_document(self):
        visited = []
        class Allowed:
            def check_permission(self, action):
                if action != "read":
                    raise AssertionError("expected read")
        def get_doc(doctype, name):
            visited.append((doctype, name))
            return Allowed()
        with (
            patch.object(report.frappe.db, "exists",
                         side_effect=lambda dt, name: dt not in ("Department", "Item")),
            patch.object(report.frappe, "get_doc", side_effect=get_doc),
        ):
            report._assert_department_visibility([self.source()])
        self.assertEqual(visited, [
            ("Stock Entry", "MAT-ISSUE-1"),
            ("Withdrawal Slip", "WS-1"),
        ])

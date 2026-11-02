# Copyright (c) 2026, CCPC and contributors
"""Source/role/quality focused unit cases. NOT real MariaDB integration."""
from unittest import TestCase
from unittest.mock import patch

import frappe

from ccpcerp.ccpc_erp.report.ccpc_equipment_job_material_activity import (
    ccpc_equipment_job_material_activity as report,
)


class TestEquipmentJobMaterialActivity(TestCase):
    @staticmethod
    def issue(**change):
        data = {
            "posting_date": "2026-10-15",
            "issuing_company": "_Test Company",
            "issuing_currency": "PHP",
            "stock_entry": "MAT-ISSUE-A",
            "stock_entry_detail": "MAT-DETAIL-A",
            "withdrawal_slip": "WS-1",
            "withdrawal_slip_item": "WSI-1",
            "item_code": "ITEM-1",
            "item_name": "Filter",
            "stock_uom": "Nos",
            "issued_qty": 2,
            "source_equipment": "EQ-1",
            "source_equipment_code": "EQ-1",
            "valid_equipment": "EQ-1",
            "equipment_description": "Pump",
            "current_slip_equipment": "EQ-1",
            "valid_slip_item": "WSI-1",
            "slip_item_code": "ITEM-1",
            "outbound_qty": 2,
            "gross_sle_issue_value": 100,
            "ledger_rows": 2,
        }
        data.update(change)
        return frappe._dict(data)

    @staticmethod
    def job(**change):
        data = {
            "job_order": "JOB-1",
            "activity_date": "2026-10-15",
            "issuing_company": "_Test Company",
            "job_equipment": "EQ-1",
            "valid_equipment": "EQ-1",
            "equipment_description": "Pump",
            "job_status": "Completed",
            "recorded_hours": 2.5,
        }
        data.update(change)
        return frappe._dict(data)

    def test_original_issue_and_job_are_distinct_not_additive(self):
        rows = report._present([self.issue()], [self.job()])
        self.assertEqual(len(rows), 2)
        material = next(r for r in rows if r["activity_type"] == "ORIGINAL_MATERIAL_ISSUE")
        job = next(r for r in rows if r["activity_type"] == "UNPRICED_JOB_ORDER_ACTIVITY")
        self.assertEqual(material["equipment"], "EQ-1")
        self.assertEqual(material["gross_sle_material_value"], 100)
        self.assertEqual(material["outbound_sle_qty"], 2)
        self.assertEqual(material["issuing_currency"], "PHP")
        self.assertEqual(material["job_attribution_quality"], "NO_FROZEN_JOB_LINK_ON_ORIGINAL_ISSUE")
        self.assertIsNone(material["job_order"])
        self.assertIsNone(material["recorded_labor_hours"])
        self.assertIsNone(job["gross_sle_material_value"])
        self.assertIsNone(job["issuing_currency"])
        self.assertEqual(job["recorded_labor_hours"], 2.5)
        self.assertEqual(job["job_attribution_quality"], "CURRENT_JOB_EQUIPMENT_NOT_ISSUE_LINK")

    def test_mutated_current_slip_does_not_reassign_original_equipment(self):
        line = report._present([self.issue(current_slip_equipment="EQ-OTHER")], [])[0]
        self.assertEqual(line["equipment"], "EQ-1")
        self.assertIn("CURRENT_SLIP_EQUIPMENT_CHANGED", line["source_quality"])
        self.assertEqual(line["gross_sle_material_value"], 100)

    def test_missing_or_mismatched_equipment_source_is_unattributed(self):
        for changed, code in (
            (dict(source_equipment=None, source_equipment_code=None,
                  valid_equipment=None), "LEGACY_NO_EQUIPMENT_SNAPSHOT"),
            (dict(source_equipment="EQ-1", source_equipment_code="EQ-2"),
             "SNAPSHOT_PAIR_CONFLICT"),
            (dict(valid_equipment=None), "EQUIPMENT_MASTER_OR_COMPANY_INVALID"),
        ):
            line = report._present([self.issue(**changed)], [])[0]
            self.assertIsNone(line["equipment"])
            self.assertIn(code, line["source_quality"])
            self.assertEqual(line["gross_sle_material_value"], 100)

    def test_missing_slip_item_and_mismatched_item_are_flagged(self):
        missing = report._present([self.issue(valid_slip_item=None)], [])[0]
        self.assertIn("INVALID_OR_FOREIGN_SLIP_ITEM", missing["source_quality"])
        changed = report._present([self.issue(slip_item_code="FORGED-ITEM")], [])[0]
        self.assertIn("SLIP_ITEM_CODE_CONFLICT", changed["source_quality"])

    def test_sle_missing_mismatch_and_negative_value_fail_open_as_exceptions(self):
        absent = report._present([self.issue(
            ledger_rows=None, outbound_qty=None, gross_sle_issue_value=None,
        )], [])[0]
        self.assertIsNone(absent["gross_sle_material_value"])
        self.assertIsNone(absent["outbound_sle_qty"])
        self.assertIn("MISSING_OUTBOUND_SLE", absent["source_quality"])
        mismatch = report._present([self.issue(outbound_qty=1)], [])[0]
        self.assertIn("OUTBOUND_SLE_QUANTITY_CONFLICT", mismatch["source_quality"])
        unusual = report._present([self.issue(gross_sle_issue_value=-10)], [])[0]
        self.assertIn("UNEXPECTED_NEGATIVE_ISSUE_VALUE", unusual["source_quality"])

    def test_unpriced_unknown_job_hours_and_foreign_equipment_flagged(self):
        row = report._present([], [self.job(recorded_hours=0, valid_equipment=None)])[0]
        self.assertIsNone(row["recorded_labor_hours"])
        self.assertIsNone(row["equipment"])
        self.assertIn("JOB_EQUIPMENT_MISSING_OR_OTHER_COMPANY", row["source_quality"])
        self.assertIsNone(row["gross_sle_material_value"])

    def test_filtered_view_explicitly_warns_about_hidden_unattributed_source(self):
        rows = report._present([self.issue()], [self.job()], filtered=True)
        self.assertTrue(all(
            "EQUIPMENT_FILTER_EXCLUDES_OTHER_UNATTRIBUTED_ROWS" in r["source_quality"]
            for r in rows
        ))

    def test_duplicate_original_stock_entry_detail_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            report._present([
                self.issue(),
                self.issue(stock_entry="OTHER"),
            ], [])

    def test_material_sql_is_one_line_per_original_issue_detail(self):
        s = report._material_sql()
        for x in (
            "se.docstatus = 1", "se.purpose = 'Material Issue'",
            "sed.parenttype = 'Stock Entry'", "sed.parent = se.name",
            "ws.company = se.company", "wsi.parent = ws.name",
            "sle.is_cancelled = 0", "sle.actual_qty < 0",
            "GROUP BY sle.company, sle.voucher_no, sle.voucher_detail_no",
            "ledger.voucher_detail_no = sed.name",
            "AND ledger.company = se.company", "se.company = %(company)s",
            "se.posting_date BETWEEN %(from_date)s AND %(to_date)s",
            "LIMIT %(material_limit)s",
        ):
            self.assertIn(x, s)
        self.assertNotIn("ws.job_order", s)
        self.assertNotIn("ws.department", s)
        self.assertNotIn("tabGL Entry", s)
        self.assertIn("sed.ccpc_equipment_at_issue = %(equipment)s", report._material_sql(True))
        self.assertIn("sed.ccpc_equipment_code_at_issue = %(equipment)s", report._material_sql(True))

    def test_job_order_is_independent_unpriced_operational_query(self):
        s = report._job_sql(True)
        for x in (
            "tabJob Order", "jo.docstatus < 2",
            "COALESCE(jo.status, '') != 'Cancelled'",
            "jo.company = %(company)s", "jo.equipment = %(equipment)s",
            "DATE(jo.date_assigned) BETWEEN %(from_date)s AND %(to_date)s",
            "LIMIT %(job_limit)s",
        ):
            self.assertIn(x, s)
        self.assertNotIn("tabStock Ledger Entry", s)
        self.assertNotIn("tabGL Entry", s)
        self.assertNotIn("SUM(", s)

    def test_permission_gate_denies_nonmanager_and_missing_source_read(self):
        with patch.object(report.frappe, "get_roles", return_value=["Stockroom Personnel"]):
            with self.assertRaises(frappe.PermissionError):
                report._require_access()
        with (
            patch.object(report.frappe, "get_roles", return_value=["System Manager"]),
            patch.object(report.frappe, "has_permission", return_value=False),
        ):
            with self.assertRaises(frappe.PermissionError):
                report._require_access()

    def test_filters_reject_foreign_equipment_company_and_dates(self):
        class Doc:
            def __init__(self, company):
                self.company = company
            def check_permission(self, *_):
                pass
        with (
            patch.object(report.frappe.db, "exists", return_value=True),
            patch.object(report.frappe, "get_doc", side_effect=lambda dt, name: Doc(
                "_Other Company" if dt == "Equipment Registry" else "_Test Company"
            )),
        ):
            with self.assertRaises(frappe.ValidationError):
                report._validate_filters(frappe._dict({
                    "company": "_Test Company", "equipment": "OTHER",
                    "from_date": "2026-10-01", "to_date": "2026-10-31",
                }))
        with (
            patch.object(report.frappe.db, "exists", return_value=True),
            patch.object(report.frappe, "get_doc", return_value=Doc("_Test Company")),
        ):
            for vals in (
                dict(from_date=None, to_date="2026-10-31"),
                dict(from_date="2026-11-01", to_date="2026-10-01"),
                dict(from_date="2025-01-01", to_date="2026-10-31"),
            ):
                with self.assertRaises(frappe.ValidationError):
                    report._validate_filters(frappe._dict(company="_Test Company", **vals))

    def test_document_visibility_checked_per_stock_slip_equipment_job(self):
        seen = []
        class Source:
            def __init__(self, kind, name):
                self.kind, self.name = kind, name
            def check_permission(self, action):
                self_action = action
                seen.append((self.kind, self.name, self_action))
        with (
            patch.object(report.frappe.db, "exists", return_value=True),
            patch.object(report.frappe, "get_doc", side_effect=lambda a, b: Source(a, b)),
        ):
            report._check_row_permissions([self.issue()], [self.job()])
        self.assertIn(("Stock Entry", "MAT-ISSUE-A", "read"), seen)
        self.assertIn(("Withdrawal Slip", "WS-1", "read"), seen)
        self.assertIn(("Equipment Registry", "EQ-1", "read"), seen)
        self.assertIn(("Job Order", "JOB-1", "read"), seen)

    def test_row_limits_fail_closed_not_truncated(self):
        with (
            patch.object(report, "_require_access"),
            patch.object(report, "_validate_filters"),
            patch.object(report.frappe.db, "sql", side_effect=[
                [self.issue()] * (report.MAX_MATERIAL + 1), [],
            ]),
        ):
            with self.assertRaises(frappe.ValidationError):
                report.execute({})

    def test_currency_is_issuer_and_report_has_no_total_row(self):
        fields = {x["fieldname"]: x for x in report.get_columns()}
        self.assertEqual(fields["gross_sle_material_value"]["fieldtype"], "Currency")
        self.assertEqual(fields["gross_sle_material_value"]["options"], "issuing_currency")
        self.assertNotIn("net_maintenance_cost", fields)
        self.assertNotIn("priced_job_cost", fields)

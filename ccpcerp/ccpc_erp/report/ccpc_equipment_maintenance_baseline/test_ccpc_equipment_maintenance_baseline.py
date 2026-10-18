"""Mocked unit scope only: not evidence of real SLE/GL reconciliation."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from ccpcerp.ccpc_erp.report.ccpc_equipment_maintenance_baseline.ccpc_equipment_maintenance_baseline import (
    _merge_activity,
    _require_system_manager,
    _validate_filters,
)


class TestEquipmentMaintenanceBaseline(TestCase):
    def test_material_issue_value_is_gross_and_explicitly_incomplete(self):
        with patch("ccpcerp.ccpc_erp.report.ccpc_equipment_maintenance_baseline.ccpc_equipment_maintenance_baseline._", side_effect=lambda s: s):
            rows = _merge_activity(
                [{"equipment": "P-01", "equipment_description": "Pump 1",
                  "issue_rows": 2, "gross_issued_stock_value": 1250}],
                [{"equipment": "P-01", "job_orders": 1, "jobs_with_labor_hours": 1,
                  "recorded_labor_hours": 3.5}], "PHP"
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["gross_issued_stock_value"], 1250)
        self.assertEqual(rows[0]["recorded_labor_hours"], 3.5)
        self.assertEqual(rows[0]["currency"], "PHP")
        self.assertIn("returns", rows[0]["scope_note"])
        self.assertNotIn("net_cost", rows[0])

    def test_unrecorded_labor_hours_are_unknown_not_zero(self):
        with patch("ccpcerp.ccpc_erp.report.ccpc_equipment_maintenance_baseline.ccpc_equipment_maintenance_baseline._", side_effect=lambda s: s):
            rows = _merge_activity([], [
                {"equipment": "P-02", "equipment_description": "Pump 2",
                 "job_orders": 2, "jobs_with_labor_hours": 0, "recorded_labor_hours": 0}
            ], "PHP")
        self.assertEqual(rows[0]["gross_issued_stock_value"], 0)
        self.assertIsNone(rows[0]["recorded_labor_hours"])
        self.assertEqual(rows[0]["hours_completeness"], "0 of 2 Job Orders have hours")

    def test_multiple_equipment_and_partial_hours_remain_separate(self):
        with patch("ccpcerp.ccpc_erp.report.ccpc_equipment_maintenance_baseline.ccpc_equipment_maintenance_baseline._", side_effect=lambda s: s):
            rows = _merge_activity(
                [{"equipment": "P-02", "issue_rows": 1, "gross_issued_stock_value": 400}],
                [{"equipment": "P-01", "job_orders": 4, "jobs_with_labor_hours": 2,
                  "recorded_labor_hours": 12}], "PHP"
            )
        self.assertEqual([x["equipment"] for x in rows], ["P-01", "P-02"])
        self.assertEqual(rows[0]["hours_completeness"], "2 of 4 Job Orders have hours")
        self.assertEqual(rows[1]["job_orders"], 0)

    def test_duplicate_issue_equipment_group_is_not_silently_added_twice(self):
        with self.assertRaises(ValueError):
            _merge_activity(
                [{"equipment": "P-01", "issue_rows": 1},
                 {"equipment": "P-01", "issue_rows": 1}], [], "PHP"
            )

    @patch("frappe.has_permission", return_value=True)
    @patch("frappe.get_roles", return_value=["CCPC Operational Supervisor"])
    def test_general_supervisor_cannot_read_cost_report(self, _roles, _permission):
        with self.assertRaises(frappe.PermissionError):
            _require_system_manager()

    @patch("frappe.db.get_value", return_value="Other Company")
    @patch("frappe.db.exists", return_value=True)
    def test_cross_company_equipment_filter_rejected(self, _exists, _get_value):
        with self.assertRaises(frappe.ValidationError):
            _validate_filters(frappe._dict(company="CCPC", equipment="P-OTHER"))

    @patch("frappe.db.exists", return_value=True)
    def test_reversed_date_range_rejected(self, _exists):
        with self.assertRaises(frappe.ValidationError):
            _validate_filters(frappe._dict(company="CCPC", from_date="2026-10-18", to_date="2026-10-01"))


# Copyright (c) 2026, CCPC and contributors
"""Real ERPNext/MariaDB report integration tests. Synthetic disposable site ONLY.

Never run on existing CCPC DEVELOPMENT/PRODUCTION, even in test mode.
These tests do not implement a physical return or correct original expense GL
Department mapping; those remain on separate review and accounting gates.
"""

import os
import unittest

import frappe
from frappe.utils import flt, nowdate

from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.tests.utils import ERPNextTestSuite

from ccpcerp.ccpc_erp.report.ccpc_equipment_maintenance_baseline.ccpc_equipment_maintenance_baseline import (
    execute,
)
from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip


COMPANY = "_Test Company"
WAREHOUSE = "_Test Warehouse - _TC"
PREPARER = "test@example.com"
ISSUER = "ci-stockroom-integration@invalid.test"
REPORTER = "ci-equipment-cost-reader@invalid.test"
UNIT_RATE = 100
TEST_SITE = "ci-equipment-report.localhost"
TEST_BRANCH = "review/equipment-maintenance-baseline-2026-10-18"


class TestEquipmentMaintenanceRealIntegration(ERPNextTestSuite):
    """Only genuine SQL/SLE/GL and effective roles on a disposable v16 bench."""

    def setUp(self):
        # Guard BEFORE any Frappe fixture read/write. Never relax to a local
        # shell/bench command or another GitHub branch without explicit review.
        if not (
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "ccpcerp/ccpcerp"
            and os.environ.get("GITHUB_HEAD_REF") == TEST_BRANCH
            and os.environ.get("CCPC_ALLOW_EQUIPMENT_REAL_PROBE") == "isolated-only"
            and getattr(frappe.local, "site", None) == TEST_SITE
        ):
            raise RuntimeError("Equipment real-ledger probe restricted to approved disposable GitHub CI site")

        frappe.set_user("Administrator")
        self.load_test_records("Stock Entry")
        for doctype, name in (
            ("Company", COMPANY), ("Warehouse", WAREHOUSE), ("User", PREPARER),
        ):
            self.assertTrue(frappe.db.exists(doctype, name))

        self.department = frappe.db.get_value(
            "Department",
            {"department_name": "CI Equipment Report Department", "company": COMPANY},
            "name",
        )
        if not self.department:
            self.department = frappe.get_doc({
                "doctype": "Department",
                "department_name": "CI Equipment Report Department",
                "company": COMPANY,
                "is_group": 0,
            }).insert(ignore_permissions=True).name
        self.assertTrue(frappe.get_meta("Stock Entry").has_field("withdrawal_slip"))
        self.assertTrue(frappe.get_meta("Stock Entry Detail").has_field("withdrawal_slip_item"))

        adjustment = "Stock Adjustment - _TC"
        self.assertTrue(frappe.db.exists("Account", adjustment))
        frappe.db.set_value("Company", COMPANY, {
            "enable_perpetual_inventory": 1,
            "stock_adjustment_account": adjustment,
        })
        frappe.clear_cache(doctype="Company")
        if hasattr(frappe.local, "enable_perpetual_inventory"):
            frappe.local.enable_perpetual_inventory.pop(COMPANY, None)
        from erpnext import is_perpetual_inventory_enabled
        self.assertEqual(int(is_perpetual_inventory_enabled(COMPANY)), 1)

        self._ensure_user(ISSUER, "Stockroom Personnel")
        self._ensure_user(REPORTER, "System Manager")

        self.equipment = frappe.get_doc({
            "doctype": "Equipment Registry",
            "equipment_number": "CI-REPORT-" + frappe.generate_hash(length=10),
            "equipment_description": "Disposable pump for equipment report",
            "company": COMPANY,
            "department": self.department,
            "status": "Active",
        }).insert(ignore_permissions=True).name

        self.item = make_item(
            item_code="CI-EQUIP-REPORT-" + frappe.generate_hash(length=12),
            properties={
                "is_stock_item": 1,
                "stock_uom": "_Test UOM",
                "item_group": "_Test Item Group",
                "opening_stock": 0,
                "valuation_rate": UNIT_RATE,
            },
        )
        self.incoming = make_stock_entry(
            item_code=self.item.name,
            qty=5,
            company=COMPANY,
            to_warehouse=WAREHOUSE,
            rate=UNIT_RATE,
        )
        self.assertEqual(self.incoming.docstatus, 1)

        frappe.set_user(PREPARER)
        slip = frappe.get_doc({
            "doctype": "Withdrawal Slip",
            "requestor": "CI Synthetic Equipment Reporter",
            "approving_supervisor": PREPARER,
            "required_by": nowdate(),
            "company": COMPANY,
            "requesting_company": COMPANY,
            "department": self.department,
            "work_for_department": self.department,
            "equipment": self.equipment,
            "source_warehouse": WAREHOUSE,
            "physical_location": "Disposable CI bench",
            "purpose": "Test read-only equipment ledger attribution",
            "received_by": "CI test receiver",
            "items": [{
                "item_code": self.item.name,
                "requested_qty": 1,
                "issue_qty": 1,
                "uom": "_Test UOM",
            }],
        })
        slip.insert(ignore_permissions=True)
        self.slip_name = slip.name
        self.slip_item_name = slip.items[0].name
        frappe.db.set_value("Withdrawal Slip", slip.name, "status", "Stockroom Review")

        frappe.set_user(ISSUER)
        posted = issue_withdrawal_slip(slip.name)
        self.assertEqual(posted["submitted"], 1)
        self.entry_name = posted["name"]
        self.entry = frappe.get_doc("Stock Entry", self.entry_name)
        self.assertEqual(self.entry.docstatus, 1)

    @staticmethod
    def _ensure_user(email, role):
        if not frappe.db.exists("User", email):
            frappe.get_doc({
                "doctype": "User",
                "email": email,
                "first_name": "CI Equipment " + role,
                "enabled": 1,
                "user_type": "System User",
                "send_welcome_email": 0,
                "roles": [{"role": role}],
            }).insert(ignore_permissions=True)
        assert role in frappe.get_roles(email)

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def _report(self, **overrides):
        frappe.set_user(REPORTER)
        filters = {"company": COMPANY, "equipment": self.equipment}
        filters.update(overrides)
        return execute(filters)[1]

    def _source_sle(self):
        return frappe.get_all(
            "Stock Ledger Entry",
            filters={
                "voucher_type": "Stock Entry",
                "voucher_no": self.entry_name,
                "is_cancelled": 0,
            },
            fields=[
                "voucher_detail_no", "actual_qty",
                "stock_value_difference", "company", "posting_date",
            ],
        )

    def test_real_posted_issue_exact_sle_row_and_gl_value(self):
        self.assertEqual(len(self.entry.items), 1)
        source_sle = self._source_sle()
        self.assertEqual(len(source_sle), 1)
        self.assertEqual(source_sle[0].voucher_detail_no, self.entry.items[0].name)
        self.assertEqual(source_sle[0].company, COMPANY)
        self.assertLess(flt(source_sle[0].actual_qty), 0)
        issue_value = -flt(source_sle[0].stock_value_difference)
        self.assertGreater(issue_value, 0)

        gl = frappe.get_all("GL Entry", filters={
            "voucher_type": "Stock Entry",
            "voucher_no": self.entry_name, "is_cancelled": 0,
        }, fields=["debit", "credit", "company"])
        self.assertGreaterEqual(len(gl), 2)
        self.assertTrue(all(row.company == COMPANY for row in gl))
        self.assertAlmostEqual(
            sum(flt(row.debit) for row in gl),
            sum(flt(row.credit) for row in gl), places=2,
        )
        self.assertAlmostEqual(sum(flt(row.credit) for row in gl), issue_value, places=2)

        rows = self._report()
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(row["equipment"], self.equipment)
        self.assertEqual(row["issue_rows"], 1)
        self.assertAlmostEqual(row["gross_issued_stock_value"], issue_value, places=2)
        self.assertEqual(row["unexpected_value_rows"], 0)
        self.assertIn("PARTIAL", row["scope_note"])
        self.assertNotIn("net_cost", row)

        # Bounds operate on real ledger posting_date and are inclusive.
        self.assertEqual(len(self._report(from_date=nowdate(), to_date=nowdate())), 1)
        self.assertEqual(
            self._report(from_date="2000-01-01", to_date="2000-12-31"), []
        )

    def test_real_job_order_recorded_hours_not_currency_or_downtime(self):
        frappe.set_user("Administrator")
        job = frappe.get_doc({
            "doctype": "Job Order",
            "company": COMPANY,
            "equipment": self.equipment,
            "department": self.department,
            "priority": "2",
            "status": "Open",
            "date_assigned": nowdate() + " 09:00:00",
            "labor_hours": 2.5,
        }).insert(ignore_permissions=True)
        self.assertTrue(job.name)
        rows = self._report()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["job_orders"], 1)
        self.assertAlmostEqual(row["recorded_labor_hours"], 2.5, places=3)
        self.assertIn("1 of 1", row["hours_completeness"])
        self.assertIn("priced labor", row["scope_note"])

        self.assertEqual(
            self._report(from_date="2000-01-01", to_date="2000-12-31"), []
        )

    def test_real_canceled_issue_excluded_and_role_not_bypassed(self):
        frappe.set_user(ISSUER)
        with self.assertRaises(frappe.PermissionError):
            execute({"company": COMPANY, "equipment": self.equipment})

        frappe.set_user(REPORTER)
        with self.assertRaises(frappe.ValidationError):
            execute({"company": "_Test Company", "equipment": "nonexistent-equipment"})

        frappe.set_user("Administrator")
        self.entry.cancel()
        self.assertEqual(self.entry.docstatus, 2)
        self.assertEqual(self._report(), [])
        self.assertEqual(frappe.db.count("Stock Entry", {
            "withdrawal_slip": self.slip_name, "docstatus": 1,
        }), 0)

    def test_real_report_rejects_access_without_underlying_doctype_read(self):
        # A configured System Manager might still have tightened source access.
        # Simulate only that permission guard, with real frappe roles and site.
        from unittest.mock import patch
        frappe.set_user(REPORTER)
        with patch("frappe.has_permission", side_effect=lambda doctype, action: doctype != "Stock Entry"):
            with self.assertRaises(frappe.PermissionError):
                execute({"company": COMPANY, "equipment": self.equipment})

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
        # This disposable reader carries both roles because native Stock Entry
        # DocPerm does not grant read to System Manager alone. Do NOT
        # automatically grant these broad stock rights to CCPC real users.
        self._ensure_user(REPORTER, "System Manager", "Stock User")

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
    def _ensure_user(email, *roles):
        if not frappe.db.exists("User", email):
            frappe.get_doc({
                "doctype": "User",
                "email": email,
                "first_name": "CI Equipment Integration Reader",
                "enabled": 1,
                "user_type": "System User",
                "send_welcome_email": 0,
                "roles": [{"role": role} for role in roles],
            }).insert(ignore_permissions=True)
        assert all(role in frappe.get_roles(email) for role in roles)

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


    def test_real_posted_issue_equipment_normal_edit_denied(self):
        # The Withdrawal Slip request-intent lock prevents normal ORM
        # reattribution after issue. A direct database write bypasses this
        # guard, hence the report must keep its PROVISIONAL warning.
        frappe.set_user(PREPARER)
        slip = frappe.get_doc("Withdrawal Slip", self.slip_name)
        self.assertEqual(slip.equipment, self.equipment)
        self.assertIn(slip.status, ("Fully Issued", "Issued with Shortage"))
        slip.equipment = None
        with self.assertRaises(frappe.ValidationError):
            slip.save(ignore_permissions=True)
        self.assertEqual(
            frappe.db.get_value("Withdrawal Slip", self.slip_name, "equipment"),
            self.equipment,
        )

        result = self._report()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["equipment"], self.equipment)
        self.assertIn("PROVISIONAL equipment attribution", result[0]["scope_note"])

    def test_real_multiple_issue_rows_sum_once_and_equipment_filter_isolates(self):
        # The original 1-row issue from setUp already exists; append a second
        # submitted issue containing two distinct rows for the same Equipment.
        # An additional third issue belongs to a different Equipment and
        # must never leak into the filtered primary Equipment value.
        frappe.set_user("Administrator")
        new_item = make_item(
            item_code="CI-EQUIP-SECOND-" + frappe.generate_hash(length=12),
            properties={
                "is_stock_item": 1,
                "stock_uom": "_Test UOM",
                "item_group": "_Test Item Group",
                "opening_stock": 0,
                "valuation_rate": UNIT_RATE,
            },
        )
        incoming2 = make_stock_entry(
            item_code=new_item.name,
            qty=5,
            company=COMPANY,
            to_warehouse=WAREHOUSE,
            rate=UNIT_RATE,
        )
        self.assertEqual(incoming2.docstatus, 1)

        equipment2 = frappe.get_doc({
            "doctype": "Equipment Registry",
            "equipment_number": "CI-REPORT-OTHER-" + frappe.generate_hash(length=10),
            "equipment_description": "Independent equipment - CI",
            "company": COMPANY,
            "department": self.department,
            "status": "Active",
        }).insert(ignore_permissions=True).name

        def submit_issue(equipment, item_codes):
            frappe.set_user(PREPARER)
            slip = frappe.get_doc({
                "doctype": "Withdrawal Slip",
                "requestor": "CI Synthetic Multi-row Test",
                "approving_supervisor": PREPARER,
                "required_by": nowdate(),
                "company": COMPANY,
                "requesting_company": COMPANY,
                "department": self.department,
                "work_for_department": self.department,
                "equipment": equipment,
                "source_warehouse": WAREHOUSE,
                "physical_location": "Disposable CI multi-equipment bay",
                "purpose": "Synthetic separate equipment source aggregation",
                "received_by": "CI test receiver",
                "items": [
                    {"item_code": code, "requested_qty": 1, "issue_qty": 1, "uom": "_Test UOM"}
                    for code in item_codes
                ],
            })
            slip.insert(ignore_permissions=True)
            frappe.db.set_value("Withdrawal Slip", slip.name, "status", "Stockroom Review")
            frappe.set_user(ISSUER)
            result = issue_withdrawal_slip(slip.name)
            self.assertEqual(result["submitted"], 1)
            doc = frappe.get_doc("Stock Entry", result["name"])
            self.assertEqual(len(doc.items), len(item_codes))
            return doc

        same_equipment = submit_issue(self.equipment, [self.item.name, new_item.name])
        other_equipment = submit_issue(equipment2, [self.item.name])
        self.assertEqual(len(same_equipment.items), 2)

        def source_value(stock_entry):
            ledger = frappe.get_all(
                "Stock Ledger Entry",
                filters={"voucher_type": "Stock Entry", "voucher_no": stock_entry.name, "is_cancelled": 0},
                fields=["voucher_detail_no", "actual_qty", "stock_value_difference"],
            )
            self.assertEqual(len(ledger), len(stock_entry.items))
            self.assertEqual(
                {r.voucher_detail_no for r in ledger},
                {r.name for r in stock_entry.items},
            )
            self.assertTrue(all(flt(row.actual_qty) == -1 for row in ledger))
            return -sum(flt(row.stock_value_difference) for row in ledger)

        value_primary = source_value(same_equipment)
        value_other = source_value(other_equipment)
        original = -sum(flt(row.stock_value_difference) for row in self._source_sle())

        filtered = self._report()
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["issue_rows"], 3)
        self.assertAlmostEqual(filtered[0]["gross_issued_stock_value"], original + value_primary, places=2)

        frappe.set_user(REPORTER)
        unfiltered = execute({"company": COMPANY})[1]
        by_equipment = {row["equipment"]: row for row in unfiltered}
        self.assertIn(self.equipment, by_equipment)
        self.assertIn(equipment2, by_equipment)
        self.assertEqual(by_equipment[equipment2]["issue_rows"], 1)
        self.assertAlmostEqual(by_equipment[equipment2]["gross_issued_stock_value"], value_other, places=2)
        self.assertAlmostEqual(by_equipment[self.equipment]["gross_issued_stock_value"], original + value_primary, places=2)

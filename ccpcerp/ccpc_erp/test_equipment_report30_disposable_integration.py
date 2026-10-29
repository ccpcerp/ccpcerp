# Copyright (c) 2026, CCPC and contributors
"""PR30: real Equipment/SLE and unpriced Job Order data; disposable CI only."""
import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import flt, nowdate

from ccpcerp.ccpc_erp import test_finance_withdrawal_billing_bridge_disposable as bridge
from ccpcerp.ccpc_erp.ci_equipment30_site_guard import assert_approved_site
from ccpcerp.ccpc_erp.doctype.withdrawal_slip import (
    test_withdrawal_real_ledger_integration as ledger_fixture,
)
from ccpcerp.ccpc_erp.report.ccpc_equipment_job_material_activity import (
    ccpc_equipment_job_material_activity as report,
)
from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip


class TestEquipmentReport30Disposable(ERPNextTestSuite):
    _verify_genuine_finance_workflow = True
    _actor = bridge.TestFinanceWithdrawalBillingBridgeDisposable._actor
    _approve_signed = bridge.TestFinanceWithdrawalBillingBridgeDisposable._approve_signed
    _bin_qty = ledger_fixture.TestWithdrawalRealLedgerIntegration._bin_qty

    def setUp(self):
        # The very first operation: no fixtures or SQL before the site guard.
        assert_approved_site(getattr(frappe.local, "site", None))
        ERPNextTestSuite.setUp(self)
        ledger_fixture.TestWithdrawalRealLedgerIntegration.setUp(self)
        frappe.set_user("Administrator")
        self.account = "Stock Adjustment - _TC"
        self.center = frappe.db.get_value(
            "Cost Center",
            {"company": bridge.COMPANY, "is_group": 0, "disabled": 0},
            "name",
        )
        self.assertTrue(self.center)
        self.equipment = frappe.get_doc({
            "doctype": "Equipment Registry",
            "equipment_number": "CI-EQUIP30-" + frappe.generate_hash(length=12),
            "equipment_description": "Isolated equipment source fixture",
            "company": bridge.COMPANY,
            "department": self.department,
            "status": "Active",
        }).insert(ignore_permissions=True).name

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def _filters(self, **kwargs):
        f = {"company": bridge.COMPANY, "from_date": nowdate(), "to_date": nowdate()}
        f.update(kwargs)
        return f

    def _rows(self, **kwargs):
        columns, rows = report.execute(self._filters(**kwargs))
        self.assertTrue(columns)
        return rows

    def _issue(self):
        # The equipment is assigned before real issue; source snapshot freezes then.
        frappe.db.set_value(
            "Withdrawal Slip", self.slip_name, "equipment", self.equipment,
            update_modified=False,
        )
        self._actor(bridge.MAKER, "CCPC Mapping Preparer")
        self._actor(bridge.CHECKER, "CCPC Accounting Mapping Approver")
        self._approve_signed("Expense", "_Test Item Group", self.account)
        self._approve_signed("Center", self.department, self.center)
        frappe.set_user(bridge.ISSUER)
        result = issue_withdrawal_slip(self.slip_name)
        frappe.set_user("Administrator")
        stock = frappe.get_doc("Stock Entry", result["name"])
        self.assertEqual(stock.docstatus, 1)
        detail = stock.items[0]
        self.assertEqual(detail.ccpc_equipment_at_issue, self.equipment)
        self.assertEqual(detail.ccpc_equipment_code_at_issue, self.equipment)
        return stock, detail

    def _source_row(self, detail, **kwargs):
        matches = [
            r for r in self._rows(**kwargs)
            if r["activity_type"] == "ORIGINAL_MATERIAL_ISSUE"
            and r["stock_entry_detail"] == detail.name
        ]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_a_real_signed_source_and_exact_outbound_sle(self):
        stock, detail = self._issue()
        row = self._source_row(detail, equipment=self.equipment)
        sle = frappe.get_all("Stock Ledger Entry", filters={
            "voucher_type": "Stock Entry", "voucher_no": stock.name,
            "voucher_detail_no": detail.name, "is_cancelled": 0,
        }, fields=["actual_qty", "stock_value_difference"])
        outgoing = [x for x in sle if flt(x.actual_qty) < 0]
        self.assertTrue(outgoing)
        self.assertEqual(row["equipment"], self.equipment)
        self.assertEqual(row["stock_entry"], stock.name)
        self.assertEqual(row["withdrawal_slip"], self.slip_name)
        self.assertEqual(row["job_attribution_quality"], "NO_FROZEN_JOB_LINK_ON_ORIGINAL_ISSUE")
        self.assertIsNone(row["job_order"])
        self.assertAlmostEqual(
            row["gross_sle_material_value"],
            -sum(flt(x.stock_value_difference) for x in outgoing), places=2,
        )
        self.assertAlmostEqual(
            row["outbound_sle_qty"],
            -sum(flt(x.actual_qty) for x in outgoing),
        )
        self.assertEqual(
            row["issuing_currency"],
            frappe.db.get_value("Company", bridge.COMPANY, "default_currency"),
        )

    def test_b_mutable_slip_equipment_does_not_relabel_posted_snapshot(self):
        _, detail = self._issue()
        original = self._source_row(detail)
        frappe.db.set_value(
            "Withdrawal Slip", self.slip_name, "equipment", None,
            update_modified=False,
        )
        changed = self._source_row(detail)
        self.assertEqual(changed["equipment"], self.equipment)
        self.assertEqual(changed["gross_sle_material_value"], original["gross_sle_material_value"])
        self.assertIn("CURRENT_SLIP_EQUIPMENT_CHANGED", changed["source_quality"])

    def test_c_invalid_snapshot_and_source_membership_surface_exceptions(self):
        _, detail = self._issue()
        frappe.db.set_value(
            "Stock Entry Detail", detail.name,
            "ccpc_equipment_code_at_issue", "CI-FORGED-NOT-EQUAL",
            update_modified=False,
        )
        line = self._source_row(detail)
        self.assertIsNone(line["equipment"])
        self.assertIn("SNAPSHOT_PAIR_CONFLICT", line["source_quality"])
        self.assertGreater(line["gross_sle_material_value"], 0)
        frappe.db.set_value(
            "Stock Entry Detail", detail.name,
            "withdrawal_slip_item", "CI-UNKNOWN-ITEM",
            update_modified=False,
        )
        line = self._source_row(detail)
        self.assertIn("INVALID_OR_FOREIGN_SLIP_ITEM", line["source_quality"])

    def test_d_real_job_order_activity_is_unpriced_and_not_source_link(self):
        _, detail = self._issue()
        job = frappe.get_doc({
            "doctype": "Job Order",
            "company": bridge.COMPANY,
            "equipment": self.equipment,
            "department": self.department,
            "date_assigned": nowdate(),
            "status": "Open",
            "priority": "3",
            "labor_hours": 2.5,
        }).insert(ignore_permissions=True)
        rows = self._rows(equipment=self.equipment)
        issue = self._source_row(detail, equipment=self.equipment)
        matched = [r for r in rows if r["job_order"] == job.name]
        self.assertEqual(len(matched), 1)
        activity = matched[0]
        self.assertEqual(activity["activity_type"], "UNPRICED_JOB_ORDER_ACTIVITY")
        self.assertEqual(activity["recorded_labor_hours"], 2.5)
        self.assertIsNone(activity["gross_sle_material_value"])
        self.assertIsNone(activity["issuing_currency"])
        self.assertIsNone(issue["job_order"])
        self.assertIsNone(issue["recorded_labor_hours"])

    def test_e_actual_role_filter_company_and_date_rejections(self):
        _, detail = self._issue()
        frappe.set_user(bridge.ISSUER)
        try:
            with self.assertRaises(frappe.PermissionError):
                report.execute(self._filters())
        finally:
            frappe.set_user("Administrator")
        for wrong in (
            {"company": "CI-NOT-A-COMPANY"},
            {"from_date": None},
            {"to_date": None},
            {"from_date": "2026-12-01", "to_date": "2026-10-01"},
            {"from_date": "2025-01-01", "to_date": "2026-10-01"},
        ):
            with self.assertRaises(frappe.ValidationError):
                report.execute(self._filters(**wrong))
        self.assertEqual(self._source_row(detail)["equipment"], self.equipment)

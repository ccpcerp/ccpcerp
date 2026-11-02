# Copyright (c) 2026, CCPC and contributors
"""Integrated 4-report exact-SHA isolated disposable CircleCI ONLY.

Reuses the established synthetic signed-Finance/real-Stock/GL/invoice fixture
helpers. Never imports actual CCPC masters, submits invoices, commits a
transaction, or operates on persistent DEVELOPMENT/PRODUCTION. Native DB
writes are confined to the guarded disposable site's test transactions.

These tests deliberately exclude the four inherited bridge test methods:
they already passed separately on Finance build #34 and must not silently
expand this manually scoped PR28 run.
"""
from datetime import timedelta

import frappe
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import add_months, flt, get_first_day, getdate, nowdate

from ccpcerp.ccpc_erp.ci_four_reports_site_guard import assert_approved_site
from ccpcerp.ccpc_erp.doctype.withdrawal_slip import (
    test_withdrawal_real_ledger_integration as base_ledger,
)
from ccpcerp.ccpc_erp.report.ccpc_intercompany_withdrawal_billing_status import (
    ccpc_intercompany_withdrawal_billing_status as report,
)
from ccpcerp.ccpc_erp import (
    test_finance_withdrawal_billing_bridge_disposable as bridge,
)
from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip


class TestIntercompanyReport28Disposable(bridge.TestFinanceWithdrawalBillingBridgeDisposable):
    """Only this class runs; shared helper logic remains in the Finance module."""

    # Explicitly mask base test methods so that only report cases execute.
    test_a_signed_issue_reaches_real_ledger_and_billing_source = None
    test_b_unsigned_issue_cannot_enter_billing_preview = None
    test_c_real_source_finalizes_to_actual_paired_invoice_drafts = None
    test_d_second_draft_insert_failure_rolls_back_real_first_invoice = None

    _verify_genuine_finance_workflow = True

    def setUp(self):
        # FIRST action. Never weaken/monkeypatch the older Finance CI guard:
        # this is an independently approved job/branch/site/SHA.
        assert_approved_site(getattr(frappe.local, "site", None))
        ERPNextTestSuite.setUp(self)
        # Source setup is not another TestCase subclass of this suite.
        # It loads real ERPNext synthetic fixtures and respects the flag above
        # (no faked approved Finance mappings).
        base_ledger.TestWithdrawalRealLedgerIntegration.setUp(self)
        frappe.set_user("Administrator")
        self.load_test_records("Sales Invoice")
        self.assertTrue(frappe.db.exists("Company", bridge.BUYER))
        self.assertNotEqual(bridge.COMPANY, bridge.BUYER)
        self.assertEqual(
            frappe.db.get_value("Company", bridge.COMPANY, "default_currency"),
            frappe.db.get_value("Company", bridge.BUYER, "default_currency"),
        )
        buyer_department = frappe.get_doc({
            "doctype": "Department",
            "department_name": "CI PR28 Request " + frappe.generate_hash(length=10),
            "company": bridge.BUYER,
            "is_group": 0,
        }).insert(ignore_permissions=True)
        frappe.set_user(base_ledger.PREPARER)
        slip = frappe.get_doc({
            "doctype": "Withdrawal Slip",
            "requestor": "Disposable PR28 Requestor",
            "approving_supervisor": base_ledger.PREPARER,
            "required_by": nowdate(),
            "source_warehouse": base_ledger.WAREHOUSE,
            "company": bridge.COMPANY,
            "requesting_company": bridge.BUYER,
            "department": buyer_department.name,
            "work_for_department": self.department,
            "physical_location": "CI PR28 synthetic bay",
            "purpose": "Disposable report fixture",
            "received_by": "CI Synthetic Receiver",
            "items": [{
                "item_code": self.item.name,
                "requested_qty": bridge.QTY_ISSUED,
                "issue_qty": bridge.QTY_ISSUED,
                "uom": "_Test UOM",
            }],
        }).insert(ignore_permissions=True)
        self.assertEqual(slip.status, "Draft")
        # The prior bridge fixture's reviewed test-only Stockroom Review
        # shortcut is retained; this does not claim workflow transition proof.
        frappe.db.set_value("Withdrawal Slip", slip.name, "status", "Stockroom Review")
        self.slip_name = slip.name
        self.slip_item_name = slip.items[0].name
        frappe.set_user("Administrator")
        self.account = "Stock Adjustment - _TC"
        self.center = frappe.db.get_value(
            "Cost Center",
            {"company": bridge.COMPANY, "is_group": 0, "disabled": 0},
            "name",
        )
        self.assertTrue(self.center)

    def _filters(self, **change):
        f = {
            "issuing_company": bridge.COMPANY,
            "requesting_company": bridge.BUYER,
            "from_date": nowdate(),
            "to_date": nowdate(),
        }
        f.update(change)
        return f

    def _row(self, detail, **change):
        columns, rows = report.execute(self._filters(**change))
        self.assertTrue(columns)
        matches = [row for row in rows if row["stock_entry_detail"] == detail.name]
        self.assertEqual(len(matches), 1, "Report must present exact original source once")
        return matches[0]

    def _issue_only(self):
        self._actor(bridge.MAKER, "CCPC Mapping Preparer")
        self._actor(bridge.CHECKER, "CCPC Accounting Mapping Approver")
        self._approve_signed("Expense", "_Test Item Group", self.account)
        self._approve_signed("Center", self.department, self.center)
        frappe.set_user(bridge.ISSUER)
        result = issue_withdrawal_slip(self.slip_name)
        frappe.set_user("Administrator")
        stock = frappe.get_doc("Stock Entry", result["name"])
        self.assertEqual(stock.docstatus, 1)
        self.assertEqual(len(stock.items), 1)
        return stock, stock.items[0]

    def test_report_a_submitted_source_unbilled_then_draft_claim(self):
        """Actual SLE + real expense GL; no invented billing or grand total."""
        stock, detail = self._issue_only()
        sle = frappe.get_all(
            "Stock Ledger Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": stock.name,
                     "voucher_detail_no": detail.name, "is_cancelled": 0},
            fields=["actual_qty", "stock_value_difference"],
        )
        self.assertTrue(sle)
        expected_value = -sum(flt(s.stock_value_difference) for s in sle if flt(s.actual_qty) < 0)
        expected_qty = -sum(flt(s.actual_qty) for s in sle if flt(s.actual_qty) < 0)
        row = self._row(detail)
        self.assertEqual(row["stock_entry"], stock.name)
        self.assertEqual(row["withdrawal_slip"], self.slip_name)
        self.assertEqual(row["billing_status"], "Unbilled")
        self.assertEqual((row["sales_status"], row["purchase_status"]),
                         ("Not Created", "Not Created"))
        self.assertEqual(row["source_quality"], "OK")
        self.assertAlmostEqual(row["sle_issued_qty"], expected_qty)
        self.assertAlmostEqual(row["gross_sle_issue_value"], expected_value, places=2)
        self.assertIsNone(row["frozen_billing_amount"])
        self.assertEqual(
            row["issuing_currency"],
            frappe.db.get_value("Company", bridge.COMPANY, "default_currency"),
        )
        gl = frappe.get_all(
            "GL Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": stock.name,
                     "account": self.account, "is_cancelled": 0},
            fields=["debit", "credit"],
        )
        self.assertTrue(gl)
        self.assertAlmostEqual(sum(flt(g.debit - g.credit) for g in gl),
                               expected_value, places=2)
        batch = self._billing_candidate()
        self.assertEqual(batch.refresh_preview()["line_count"], 1)
        batch.reload()
        draft = self._row(detail)
        self.assertEqual(draft["billing_batch"], batch.name)
        self.assertEqual(draft["billing_status"], "Draft")
        self.assertAlmostEqual(
            draft["frozen_billing_amount"], flt(batch.items[0].frozen_basic_amount),
        )
        self.assertAlmostEqual(draft["gross_sle_issue_value"], expected_value, places=2)

    def test_report_b_finalized_real_invoice_pair_stays_draft(self):
        """One genuine original issue and two reciprocal native draft invoices."""
        batch, stock, detail = self._issue_signed_source_for_real_pair()
        self._prepare_disposable_invoice_masters()
        module, after_month_end, next_day = self._finalize_real_month_end(batch)
        approved = self._approve_real_pair_at_month_end(
            batch, module, after_month_end, next_day,
        )
        batch.reload()
        self._assert_drafts_are_real_and_unposted(approved, stock, detail, batch)
        row = self._row(detail)
        self.assertEqual(row["source_quality"], "OK")
        self.assertEqual(row["billing_batch"], batch.name)
        self.assertEqual(row["billing_status"], "Approved")
        self.assertEqual(row["sales_invoice"], approved["sales_invoice"])
        self.assertEqual(row["purchase_invoice"], approved["purchase_invoice"])
        self.assertEqual((row["sales_status"], row["purchase_status"]),
                         ("Draft", "Draft"))
        self.assertAlmostEqual(
            row["frozen_billing_amount"], flt(batch.items[0].frozen_basic_amount),
            places=2,
        )

    def test_report_c_two_active_claims_fail_closed(self):
        """Corrupt duplicate child link is isolated and rolled back by test suite."""
        batch, stock, detail = self._issue_signed_source_for_real_pair()
        other_month = get_first_day(add_months(nowdate(), -1))
        second = frappe.get_doc({
            "doctype": "Intercompany Material Billing Batch",
            "issuing_company": bridge.COMPANY,
            "requesting_company": bridge.BUYER,
            "billing_month": other_month,
            "status": "Draft",
        }).insert(ignore_permissions=True)
        # Deliberately bypass the parent's duplicate-claim validator only on
        # this disposable transaction to prove the report rejects corrupt DB.
        duplicate = frappe.copy_doc(batch.items[0])
        duplicate.name = None
        duplicate.parent = second.name
        duplicate.parenttype = "Intercompany Material Billing Batch"
        duplicate.parentfield = "items"
        duplicate.idx = 1
        duplicate.db_insert()
        with self.assertRaises(frappe.ValidationError):
            report.execute(self._filters())
        self.assertTrue(frappe.db.exists(
            "Stock Entry", {"name": stock.name, "docstatus": 1}
        ))

    def test_report_d_scope_and_non_manager_are_rejected(self):
        """Real Frappe identity check, not stubbed application permission API."""
        stock, detail = self._issue_only()
        frappe.set_user(bridge.ISSUER)
        try:
            with self.assertRaises(frappe.PermissionError):
                report.execute(self._filters())
        finally:
            frappe.set_user("Administrator")
        with self.assertRaises(frappe.ValidationError):
            report.execute(self._filters(requesting_company=None))
        with self.assertRaises(frappe.ValidationError):
            report.execute(self._filters(requesting_company=bridge.COMPANY))
        with self.assertRaises(frappe.ValidationError):
            report.execute(self._filters(
                from_date=getdate(nowdate()) + timedelta(days=1)
            ))
        with self.assertRaises(frappe.ValidationError):
            report.execute(self._filters(
                from_date=getdate(nowdate()) - timedelta(days=368)
            ))
        self.assertEqual(self._row(detail)["source_quality"], "OK")

    def test_report_e_forged_source_and_invoice_links_raise_quality_flags(self):
        """Actual MariaDB query must see anomalous links without merging values."""
        batch, stock, detail = self._issue_signed_source_for_real_pair()
        self._prepare_disposable_invoice_masters()
        module, after_month_end, next_day = self._finalize_real_month_end(batch)
        approved = self._approve_real_pair_at_month_end(
            batch, module, after_month_end, next_day,
        )
        batch.reload()
        row_before = self._row(detail)
        self.assertEqual(row_before["source_quality"], "OK")
        # Deliberate corruption only inside the disposable test transaction.
        frappe.db.set_value(
            "Intercompany Material Billing Item", batch.items[0].name,
            "stock_entry", "CI-FORGED-STOCK-ENTRY", update_modified=False,
        )
        frappe.db.set_value(
            "Sales Invoice", approved["sales_invoice"],
            "inter_company_invoice_reference", "CI-NONRECIPROCAL-PI",
            update_modified=False,
        )
        row_after = self._row(detail)
        self.assertIn("BILLING_ORIGINAL_VOUCHER_MISMATCH",
                      row_after["source_quality"])
        self.assertIn("INVOICE_RECIPROCAL_LINK_MISMATCH",
                      row_after["source_quality"])
        self.assertEqual(row_after["stock_entry"], stock.name)
        self.assertEqual(row_after["gross_sle_issue_value"],
                         row_before["gross_sle_issue_value"])
        # Missing foreign link is observational, never silently 'Submitted'.
        frappe.db.set_value(
            "Intercompany Material Billing Batch", batch.name,
            "sales_invoice", "CI-FOREIGN-NOT-LOCAL", update_modified=False,
        )
        missing = self._row(detail)
        self.assertEqual(missing["sales_status"], "Missing Reference")
        self.assertIn("SALES_COMPANY_OR_LINK_INVALID", missing["source_quality"])


    def test_report_f_same_signed_issue_all_four_read_only_reports(self):
        """One submitted original source is consistently seen by four reports.

        Values are compared by original voucher/detail, NEVER added together.
        This is synthetic MariaDB data, not full accounting consolidation.
        """
        from ccpcerp.ccpc_erp.report.ccpc_department_material_consumption import (
            ccpc_department_material_consumption as department_report,
        )
        from ccpcerp.ccpc_erp.report.ccpc_equipment_job_material_activity import (
            ccpc_equipment_job_material_activity as equipment_report,
        )
        from ccpcerp.ccpc_erp.report.ccpc_company_stock_pnl_gl_reconciliation import (
            ccpc_company_stock_pnl_gl_reconciliation as company_report,
        )

        stock, detail = self._issue_only()
        source = self._row(detail)
        common = {
            "company": bridge.COMPANY,
            "from_date": nowdate(),
            "to_date": nowdate(),
        }
        dept = [r for r in department_report.execute(common)[1]
                if r["stock_entry_detail"] == detail.name]
        equip = [r for r in equipment_report.execute(common)[1]
                 if r["activity_type"] == "ORIGINAL_MATERIAL_ISSUE"
                 and r["stock_entry_detail"] == detail.name]
        company = [r for r in company_report.execute(common)[1]
                   if r["perspective"] == "STOCK_ISSUE_VOUCHER_COMPARISON"
                   and r["voucher_no"] == stock.name]
        self.assertEqual((len(dept), len(equip), len(company)), (1, 1, 1))
        self.assertEqual(dept[0]["stock_entry"], stock.name)
        self.assertEqual(equip[0]["stock_entry"], stock.name)
        self.assertEqual(source["stock_entry"], stock.name)
        self.assertAlmostEqual(
            source["gross_sle_issue_value"],
            dept[0]["gross_sle_issue_value"], places=2,
        )
        self.assertAlmostEqual(
            source["gross_sle_issue_value"],
            equip[0]["gross_sle_material_value"], places=2,
        )
        self.assertAlmostEqual(
            source["gross_sle_issue_value"],
            company[0]["gross_original_sle_value"], places=2,
        )
        self.assertIsNone(equip[0]["job_order"])
        self.assertIsNone(source["frozen_billing_amount"])
        self.assertIsNotNone(company[0]["voucher_pnl_expense_gl_value"])

def test_report_g_real_executive_role_gateway(self):
        """Actual disposable System User + Role reaches real report SQL.

        Only the allowlist's canonical names are substituted with the fixture
        Companies in this test. Role, User, SQL and per-row Company checks are
        genuine Frappe/MariaDB operations, with no persistent-site writes.
        """
        from unittest.mock import patch

        from ccpcerp.ccpc_erp import executive_reporting as gateway

        stock, detail = self._issue_only()
        user = (
            "ci-report-executive-"
            + frappe.generate_hash(length=10)
            + "@example.test"
        )
        self._actor(user, gateway.EXECUTIVE_ROLE)

        issuer_abbr = frappe.db.get_value("Company", bridge.COMPANY, "abbr")
        buyer_abbr = frappe.db.get_value("Company", bridge.BUYER, "abbr")
        self.assertTrue(issuer_abbr)
        self.assertTrue(buyer_abbr)
        self.assertNotEqual(issuer_abbr, buyer_abbr)

        common = {
            "company": bridge.COMPANY,
            "from_date": nowdate(),
            "to_date": nowdate(),
        }
        scenarios = (
            ("department", common, "stock_entry_detail", detail.name),
            ("equipment", common, "stock_entry_detail", detail.name),
            (
                "intercompany",
                self._filters(),
                "stock_entry_detail",
                detail.name,
            ),
            ("stock_gl", common, "voucher_no", stock.name),
        )

        frappe.set_user(user)
        try:
            roles = set(frappe.get_roles(user))
            self.assertIn(gateway.EXECUTIVE_ROLE, roles)
            self.assertNotIn("System Manager", roles)
            self.assertFalse(gateway.in_executive_report())
            with self.assertRaises(frappe.PermissionError):
                report.execute(self._filters())

            # The synthetic CI Companies are NOT the actual four production
            # Company IDs. Never change the real gateway allowlist in app
            # code or claim this substitutes for a live four-Company test.
            with patch.dict(
                gateway.COMPANIES,
                {bridge.COMPANY: issuer_abbr, bridge.BUYER: buyer_abbr},
                clear=True,
            ):
                for key, filters, identity, expected in scenarios:
                    result = gateway.run_group_report(key, dict(filters))
                    self.assertTrue(result["columns"])
                    self.assertTrue(result["rows"])
                    self.assertTrue(
                        any(row.get(identity) == expected
                            for row in result["rows"]),
                        key,
                    )
                    for row in result["rows"]:
                        self.assertEqual(
                            row.get("issuing_company", row.get("company")),
                            bridge.COMPANY,
                        )
                        if key == "intercompany":
                            self.assertEqual(
                                row["requesting_company"], bridge.BUYER
                            )
                    self.assertFalse(gateway.in_executive_report())

                with self.assertRaises(frappe.PermissionError):
                    gateway.run_group_report(
                        "department",
                        {**common, "company": "CI-UNLISTED-COMPANY"},
                    )
                with self.assertRaises(frappe.PermissionError):
                    gateway.run_group_report(
                        "intercompany",
                        self._filters(requesting_company=bridge.COMPANY),
                    )
        finally:
            frappe.set_user("Administrator")
        self.assertFalse(gateway.in_executive_report())

    def test_report_h_approved_invoice_pair_replay_does_not_duplicate(self):
        """One real paired draft creation; a second approval must fail closed."""
        batch, stock, detail = self._issue_signed_source_for_real_pair()
        self._prepare_disposable_invoice_masters()
        module, after_month_end, next_day = self._finalize_real_month_end(batch)
        approved = self._approve_real_pair_at_month_end(
            batch, module, after_month_end, next_day,
        )
        batch.reload()
        self.assertEqual(batch.status, "Approved")
        self._assert_drafts_are_real_and_unposted(
            approved, stock, detail, batch
        )

        original = (approved["sales_invoice"], approved["purchase_invoice"])
        before_sales = frappe.db.count(
            "Sales Invoice", {"company": bridge.COMPANY}
        )
        before_purchase = frappe.db.count(
            "Purchase Invoice", {"company": bridge.BUYER}
        )
        with self.assertRaises(frappe.ValidationError):
            self._approve_real_pair_at_month_end(
                batch, module, after_month_end, next_day,
            )
        batch.reload()
        self.assertEqual(
            (batch.sales_invoice, batch.purchase_invoice), original
        )
        self.assertEqual(batch.status, "Approved")
        self.assertEqual(
            frappe.db.count("Sales Invoice", {"company": bridge.COMPANY}),
            before_sales,
        )
        self.assertEqual(
            frappe.db.count("Purchase Invoice", {"company": bridge.BUYER}),
            before_purchase,
        )
        self._assert_drafts_are_real_and_unposted(
            approved, stock, detail, batch
        )

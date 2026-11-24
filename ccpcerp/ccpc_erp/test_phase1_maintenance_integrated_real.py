# Copyright (c) 2026, CCPC and contributors
"""Phase1 integrated-only real MariaDB/ERPNext posting tests on a disposable GitHub runner.

The fixture uses ERPNext's synthetic _Test Company, not real CCPC data.
Pure policy tests separately verify the exact literal CCPC/Bearings scope.
Patch constants ONLY inside this test process so actual locked SQL + SLE/GL
posting can be exercised with existing ERPNext synthetic masters.
"""
import multiprocessing
import os
import re
from queue import Empty
from unittest.mock import patch

import frappe
from frappe.utils import flt, nowdate
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.tests.utils import ERPNextTestSuite

from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
from ccpcerp.ccpc_erp.doctype.withdrawal_slip import (
    test_withdrawal_real_ledger_integration as ledger,
)
from ccpcerp.ccpc_erp import test_finance_withdrawal_billing_bridge_disposable as finance
from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip
from ccpcerp.ccpc_erp.ci_phase1_integrated_concurrent_issue_worker import _phase1_integrated_concurrent_worker

SITE = "ci-phase1-integrated.localhost"
GROUP = "_Test Item Group"
EXPENSE = "Stock Adjustment - _TC"


def _is_disposable_phase1_integrated_site():
    env = os.environ
    return all((
        getattr(frappe.local, "site", None) == SITE,
        env.get("GITHUB_ACTIONS") == "true",
        env.get("GITHUB_REPOSITORY") == "ccpcerp/ccpcerp",
        env.get("GITHUB_EVENT_NAME") == "pull_request",
        bool(re.fullmatch(r"refs/pull/[1-9][0-9]*/merge", env.get("GITHUB_REF", ""))),
        env.get("GITHUB_HEAD_REF") == "review/phase1-maintenance-integrated-tests-2026-11-24",
        env.get("GITHUB_BASE_REF") == "review/phase1-maintenance-release-integration-2026-11-24",
        env.get("GITHUB_WORKFLOW") == "CCPC Phase1 - manual integrated Bearings MariaDB acceptance",
        bool(env.get("GITHUB_RUN_ID")),
        len(env.get("CCPC_PHASE1_TEST_SHA", "")) == 40,
        env.get("CCPC_ALLOW_MAINTENANCE_DB_PROBE") == "phase1-integrated-only",
        frappe.conf.get("db_host") == "127.0.0.1",
        frappe.conf.get("db_type", "mariadb") == "mariadb",
    ))



class TestPhase1MaintenanceRealIntegration(ERPNextTestSuite):
    """No database records are even loaded unless the CI site guard succeeds."""

    _set_up_stock = ledger.TestWithdrawalRealLedgerIntegration._set_up_stock
    _bin_qty = ledger.TestWithdrawalRealLedgerIntegration._bin_qty
    _actor = finance.TestFinanceWithdrawalBillingBridgeDisposable._actor
    _approve_signed = finance.TestFinanceWithdrawalBillingBridgeDisposable._approve_signed

    def setUp(self):
        if not _is_disposable_phase1_integrated_site():
            raise RuntimeError("Phase1 integrated real SQL tests require the dedicated disposable GitHub Actions site")
        super().setUp()
        self.addCleanup(frappe.set_user, "Administrator")
        self._set_up_stock()
        self.assertTrue(frappe.get_meta("Withdrawal Slip").has_field("ccpc_material_use"))
        self.assertTrue(frappe.db.sql(
            "SHOW COLUMNS FROM `tabWithdrawal Slip` LIKE %s",
            ("ccpc_material_use",), as_dict=True,
        ))
        self.center = frappe.db.get_value(
            "Cost Center", {"company": ledger.COMPANY, "is_group": 0, "disabled": 0}, "name"
        )
        self.assertTrue(self.center)

        # Actual independently signed synthetic Finance revisions on MariaDB.
        self._actor(finance.MAKER, "CCPC Mapping Preparer")
        self._actor(finance.CHECKER, "CCPC Accounting Mapping Approver")
        self.expense_revision = self._approve_signed("Expense", GROUP, EXPENSE)
        self.center_revision = self._approve_signed("Center", self.department, self.center)
        frappe.set_user("Administrator")

        # Only adapt the test-process pilot labels to ERPNext's synthetic
        # finance fixture. Never change application code or real master data.
        for symbol, value in (
            ("ccpcerp.ccpc_erp.phase1_maintenance_use.PILOT_COMPANY", ledger.COMPANY),
            ("ccpcerp.ccpc_erp.phase1_maintenance_use.PILOT_ITEM_GROUP", GROUP),
            ("ccpcerp.ccpc_erp.phase1_maintenance_use.PILOT_BENEFICIARY", self.department),
            ("ccpcerp.ccpc_erp.withdrawal_slip_stock.PILOT_COMPANY", ledger.COMPANY),
            ("ccpcerp.ccpc_erp.withdrawal_slip_stock.PILOT_ITEM_GROUP", GROUP),
        ):
            mock = patch(symbol, value)
            mock.start()
            self.addCleanup(mock.stop)

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def _slip(self, use, *, department=None, extra=None):
        frappe.set_user(ledger.PREPARER)
        items = [{"item_code": self.item.name, "requested_qty": 1,
                  "issue_qty": 1, "uom": "_Test UOM"}] + (extra or [])
        slip = frappe.get_doc({
            "doctype": "Withdrawal Slip",
            "requestor": "CI synthetic test employee",
            "approving_supervisor": ledger.PREPARER,
            "required_by": nowdate(),
            "company": ledger.COMPANY,
            "requesting_company": ledger.COMPANY,
            "department": self.department,
            "work_for_department": department or self.department,
            "source_warehouse": ledger.WAREHOUSE,
            "physical_location": "Disposable Phase1 integrated CI test bay",
            "purpose": "Phase1 integrated disposable maintenance review",
            "ccpc_material_use": use,
            "received_by": "Synthetic material receiver",
            "items": items,
        }).insert(ignore_permissions=True)
        self.assertEqual(slip.owner, ledger.PREPARER)
        self.assertEqual(slip.status, "Draft")
        # Existing suite separately verifies request workflow transitions.
        # Only this temporary fixture state uses a direct DB change.
        frappe.db.set_value("Withdrawal Slip", slip.name, "status", "Stockroom Review")
        slip.reload()
        frappe.set_user(ledger.ISSUER)
        return slip

    def _snapshot(self, slip=None):
        return (
            self._bin_qty(),
            frappe.db.count("Stock Entry", {"withdrawal_slip": slip.name}) if slip else None,
            frappe.db.count("Stock Ledger Entry", {"item_code": self.item.name}),
            frappe.db.count("GL Entry", {"company": ledger.COMPANY}),
        )

    def _denied_without_ledger(self, slip, expected):
        before = self._snapshot(slip)
        with self.assertRaisesRegex(MappingPolicyError, expected):
            issue_withdrawal_slip(slip.name)
        self.assertEqual(self._snapshot(slip), before)
        self.assertFalse(frappe.db.exists(
            "Stock Entry", {"withdrawal_slip": slip.name, "docstatus": 1}
        ))
        self.assertEqual(frappe.db.get_value("Withdrawal Slip", slip.name, "status"),
                         "Stockroom Review")

    def test_a_signed_routine_issue_posts_stock_and_balanced_gl_once(self):
        slip = self._slip("Routine Maintenance")
        before = self._bin_qty()
        result = issue_withdrawal_slip(slip.name)
        self.assertEqual(result["submitted"], 1)
        frappe.set_user("Administrator")
        entry = frappe.get_doc("Stock Entry", result["name"])
        self.assertEqual(entry.docstatus, 1)
        self.assertEqual(entry.items[0].ccpc_expense_mapping_revision, self.expense_revision)
        self.assertEqual(entry.items[0].ccpc_center_mapping_revision, self.center_revision)
        self.assertEqual(entry.items[0].expense_account, EXPENSE)
        self.assertEqual(entry.items[0].cost_center, self.center)
        self.assertEqual(entry.items[0].department, self.department)
        self.assertAlmostEqual(self._bin_qty(), before - 1)
        voucher = {"voucher_type": "Stock Entry", "voucher_no": entry.name, "is_cancelled": 0}
        sle = frappe.get_all("Stock Ledger Entry", filters=voucher,
                            fields=["actual_qty", "stock_value_difference"])
        self.assertTrue(sle)
        self.assertAlmostEqual(sum(flt(r.actual_qty) for r in sle), -1)
        gl = frappe.get_all("GL Entry", filters=voucher,
                            fields=["account", "cost_center", "department", "debit", "credit"])
        self.assertTrue(gl)
        self.assertAlmostEqual(sum(flt(r.debit) for r in gl),
                               sum(flt(r.credit) for r in gl), places=2)
        expenses = [r for r in gl if r.account == EXPENSE and flt(r.debit) > 0]
        self.assertTrue(expenses)
        self.assertTrue(all(r.cost_center == self.center and r.department == self.department
                            for r in expenses))
        self.assertAlmostEqual(sum(flt(r.debit) for r in expenses),
                               -sum(flt(r.stock_value_difference) for r in sle), places=2)
        self.assertEqual(frappe.db.get_value("Withdrawal Slip", slip.name, "status"),
                         "Fully Issued")
        already = self._snapshot(slip)
        frappe.set_user(ledger.ISSUER)
        with self.assertRaises(Exception):
            issue_withdrawal_slip(slip.name)
        self.assertEqual(self._snapshot(slip), already)

    def test_b_capital_other_and_unclassified_leave_ledgers_intact(self):
        for use in ("Capital Project / Improvement", "Other / Unclassified", None):
            with self.subTest(material_use=use):
                self._denied_without_ledger(self._slip(use), "Routine Maintenance")

    def test_c_foreign_beneficiary_is_denied(self):
        frappe.set_user("Administrator")
        dept = frappe.get_doc({
            "doctype": "Department",
            "department_name": "Phase1 integrated Foreign Beneficiary " + frappe.generate_hash(length=8),
            "company": ledger.COMPANY,
            "is_group": 0,
        }).insert(ignore_permissions=True)
        self._denied_without_ledger(
            self._slip("Routine Maintenance", department=dept.name), "Maintenance beneficiary"
        )

    def test_d_mixed_group_slip_is_denied(self):
        frappe.set_user("Administrator")
        group = frappe.get_doc({
            "doctype": "Item Group",
            "item_group_name": "Phase1 integrated Other Item Group " + frappe.generate_hash(length=8),
            "parent_item_group": "All Item Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)
        extra = make_item(
            item_code="CI-Phase1 integrated-MIXED-" + frappe.generate_hash(length=12),
            properties={"is_stock_item": 1, "stock_uom": "_Test UOM",
                        "item_group": group.name, "opening_stock": 0,
                        "valuation_rate": ledger.UNIT_RATE},
        )
        make_stock_entry(item_code=extra.name, qty=ledger.QTY_RECEIVED,
                         company=ledger.COMPANY, to_warehouse=ledger.WAREHOUSE,
                         rate=ledger.UNIT_RATE)
        self._denied_without_ledger(self._slip("Routine Maintenance", extra=[{
            "item_code": extra.name, "requested_qty": 1,
            "issue_qty": 1, "uom": "_Test UOM",
        }]), "cannot mix")

    def test_e_unlinked_native_material_issue_is_denied(self):
        frappe.set_user("Administrator")
        before = self._snapshot()
        entry = frappe.new_doc("Stock Entry")
        entry.company = ledger.COMPANY
        entry.purpose = "Material Issue"
        entry.set_stock_entry_type()
        entry.from_warehouse = ledger.WAREHOUSE
        entry.append("items", {"item_code": self.item.name, "qty": 1,
                               "s_warehouse": ledger.WAREHOUSE})
        with self.assertRaisesRegex(frappe.ValidationError, "Withdrawal Slip"):
            entry.insert(ignore_permissions=True)
        self.assertEqual(self._snapshot(), before)

    def test_f_material_use_is_immutable_after_approval(self):
        slip = self._slip("Routine Maintenance")
        frappe.set_user(ledger.PREPARER)
        changed = frappe.get_doc("Withdrawal Slip", slip.name)
        changed.ccpc_material_use = "Capital Project / Improvement"
        with self.assertRaises(frappe.ValidationError):
            changed.save(ignore_permissions=True)
        self.assertEqual(
            frappe.db.get_value("Withdrawal Slip", slip.name, "ccpc_material_use"),
            "Routine Maintenance",
        )


    def test_g_failed_posting_rollback_discards_stock_and_gl_as_one_unit(self):
        """Inject failure AFTER genuine submission; transaction rollback is atomic."""
        from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry

        slip = self._slip("Routine Maintenance")
        baseline = self._snapshot(slip)
        frappe.db.savepoint("phase1_integrated_posted_rollback")
        original_submit = StockEntry.submit

        def submit_then_abort(doc):
            original_submit(doc)
            self.assertEqual(doc.docstatus, 1)
            self.assertGreater(frappe.db.count(
                "Stock Ledger Entry", {"voucher_type": "Stock Entry", "voucher_no": doc.name}
            ), 0)
            self.assertGreater(frappe.db.count(
                "GL Entry", {"voucher_type": "Stock Entry", "voucher_no": doc.name}
            ), 0)
            raise RuntimeError("Phase1 integrated injected failure after genuine ERPNext posting")

        try:
            with patch.object(StockEntry, "submit", submit_then_abort):
                with self.assertRaisesRegex(RuntimeError, "Phase1 integrated injected failure"):
                    issue_withdrawal_slip(slip.name)
        finally:
            # A Frappe request would roll back its failed transaction; a
            # savepoint lets this synthetic test keep its fixtures intact.
            frappe.db.rollback(save_point="phase1_integrated_posted_rollback")
        self.assertEqual(self._snapshot(slip), baseline)
        self.assertFalse(frappe.db.exists("Stock Entry", {"withdrawal_slip": slip.name}))
        self.assertEqual(frappe.db.get_value("Withdrawal Slip", slip.name, "status"),
                         "Stockroom Review")

    def test_h_actual_cancellation_reverses_inventory_and_gl(self):
        """ERPNext native cancel, not a mock or direct docstatus update."""
        slip = self._slip("Routine Maintenance")
        initial_qty = self._bin_qty()
        initial_stock_value = flt(frappe.db.get_value(
            "Bin", {"item_code": self.item.name, "warehouse": ledger.WAREHOUSE},
            "stock_value",
        ))
        posted = issue_withdrawal_slip(slip.name)
        frappe.set_user("Administrator")
        entry = frappe.get_doc("Stock Entry", posted["name"])
        self.assertEqual(entry.docstatus, 1)
        entry.cancel()
        self.assertEqual(entry.docstatus, 2)
        self.assertAlmostEqual(self._bin_qty(), initial_qty)
        self.assertAlmostEqual(flt(frappe.db.get_value(
            "Bin", {"item_code": self.item.name, "warehouse": ledger.WAREHOUSE},
            "stock_value",
        )), initial_stock_value, places=2)
        sle = frappe.get_all(
            "Stock Ledger Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": entry.name},
            fields=["actual_qty", "is_cancelled"],
        )
        self.assertGreaterEqual(len(sle), 2)
        self.assertAlmostEqual(sum(flt(row.actual_qty) for row in sle), 0)
        self.assertTrue(all(int(row.is_cancelled) == 1 for row in sle))
        gl = frappe.get_all(
            "GL Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": entry.name},
            fields=["account", "debit", "credit"],
        )
        self.assertGreaterEqual(len(gl), 4)
        per_account = {}
        for line in gl:
            per_account[line.account] = per_account.get(line.account, 0.0) + (
                flt(line.debit) - flt(line.credit)
            )
        self.assertGreaterEqual(len(per_account), 2)
        for name, balance in per_account.items():
            self.assertAlmostEqual(balance, 0, places=2, msg=name)
        slip.reload()
        self.assertEqual(slip.status, "Stockroom Review")
        self.assertAlmostEqual(flt(slip.items[0].issued_qty), 0)

    def test_i_non_stockroom_and_request_creator_cannot_issue(self):
        """Real roles, no mocked authorization, no phantom financial rows."""
        slip = self._slip("Routine Maintenance")
        before = self._snapshot(slip)
        for unauthorized in (ledger.PREPARER, "Guest", "Administrator"):
            with self.subTest(user=unauthorized):
                frappe.set_user(unauthorized)
                with self.assertRaises(frappe.PermissionError):
                    issue_withdrawal_slip(slip.name)
                self.assertEqual(self._snapshot(slip), before)

    def test_j_two_real_independent_stockroom_connections_only_issue_once(self):
        """Two separate Frappe processes reach one held FOR UPDATE lock."""
        slip = self._slip("Routine Maintenance")
        frappe.set_user("Administrator")
        # Commit synthetic fixture ONLY inside this throwaway Actions site.
        frappe.db.commit()
        site = frappe.local.site
        sites_path = os.path.abspath(frappe.local.sites_path)
        self.assertEqual(site, SITE)
        locked = frappe.db.sql(
            "SELECT name FROM `tabWithdrawal Slip` WHERE name=%s FOR UPDATE",
            (slip.name,),
        )
        self.assertEqual(locked[0][0], slip.name)

        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        children = [
            ctx.Process(
                target=_phase1_integrated_concurrent_worker,
                args=(slot, site, sites_path, slip.name, self.department, queue),
                daemon=False,
            )
            for slot in (1, 2)
        ]
        released = False
        results = []
        try:
            for child in children:
                child.start()
            attempts = {}
            while len(attempts) < 2:
                try:
                    message = queue.get(timeout=40)
                except Empty as exc:
                    raise AssertionError("Both issuers must reach the original row-lock method") from exc
                self.assertEqual(message[0], "attempt", repr(message))
                self.assertNotIn(message[1], attempts)
                attempts[message[1]] = message[2]
            self.assertEqual(set(attempts), {1, 2})
            self.assertEqual(len(set(attempts.values())), 2)
            self.assertNotIn(int(frappe.db.sql("SELECT CONNECTION_ID()")[0][0]),
                             attempts.values())
            self.assertTrue(all(child.is_alive() for child in children))
            self.assertEqual(frappe.db.count(
                "Stock Entry", {"withdrawal_slip": slip.name, "docstatus": 1}
            ), 0)

            frappe.db.rollback()  # Release original parent row lock.
            released = True
            while len(results) < 2:
                try:
                    results.append(queue.get(timeout=40))
                except Empty as exc:
                    raise AssertionError("Concurrent workers did not finish") from exc
            for child in children:
                child.join(timeout=10)
                self.assertEqual(child.exitcode, 0)
        finally:
            if not released:
                frappe.db.rollback()
            for child in children:
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=5)
            queue.close()
            queue.join_thread()

        successes = [item for item in results if item[0] == "success"]
        denials = [item for item in results if item[0] == "rejected"]
        self.assertEqual(len(successes), 1, repr(results))
        self.assertEqual(len(denials), 1, repr(results))
        self.assertTrue(
            any(word in denials[0][2].lower() for word in ("already", "stockroom review")),
            repr(denials),
        )
        frappe.db.sql("SELECT name FROM `tabWithdrawal Slip` WHERE name=%s", (slip.name,))
        entries = frappe.get_all(
            "Stock Entry", filters={"withdrawal_slip": slip.name, "docstatus": 1},
            pluck="name",
        )
        self.assertEqual(entries, [successes[0][2]])
        voucher = {"voucher_type": "Stock Entry", "voucher_no": entries[0], "is_cancelled": 0}
        sle = frappe.get_all("Stock Ledger Entry", filters=voucher, fields=["actual_qty"])
        gl = frappe.get_all("GL Entry", filters=voucher, fields=["debit", "credit"])
        self.assertEqual(len(sle), 1)
        self.assertAlmostEqual(sum(flt(row.actual_qty) for row in sle), -1)
        self.assertGreaterEqual(len(gl), 2)
        self.assertAlmostEqual(sum(flt(row.debit) for row in gl),
                               sum(flt(row.credit) for row in gl), places=2)
        self.assertAlmostEqual(self._bin_qty(), ledger.QTY_RECEIVED - 1)
        self.assertEqual(frappe.db.get_value("Withdrawal Slip", slip.name, "status"),
                         "Fully Issued")

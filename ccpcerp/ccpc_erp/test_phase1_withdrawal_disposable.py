# Copyright (c) 2026, CCPC and contributors
"""Phase 1 acceptance on a freshly created, isolated MariaDB bench site.

CCPC_PHASE1_DISPOSABLE=1 bench --site phase1-acceptance.localhost run-tests \
    --module ccpcerp.ccpc_erp.test_phase1_withdrawal_disposable
"""

import os
from unittest.mock import patch

import frappe
from erpnext import is_perpetual_inventory_enabled
from erpnext.tests.utils import ERPNextTestSuite
from frappe.model.workflow import apply_workflow

from ccpcerp.ccpc_erp import phase1_finance_onboarding as onboarding
from ccpcerp.ccpc_erp import test_finance_withdrawal_billing_bridge_disposable as finance
from ccpcerp.ccpc_erp import withdrawal_slip_stock as issuance
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
from ccpcerp.ccpc_erp.doctype.withdrawal_slip import test_withdrawal_real_ledger_integration as ledger
from ccpcerp.ccpc_erp.report.ccpc_department_material_consumption import (
    ccpc_department_material_consumption as report,
)
from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip, make_material_issue


SITE = "phase1-acceptance.localhost"
SUPERVISOR = "phase1-withdrawal-supervisor@invalid.test"
ACCOUNT = "Stock Adjustment - _TC"
ITEM_GROUP = "_Test Item Group"
STOCK_UOM = "_Test UOM"


class TestPhase1WithdrawalDisposable(ERPNextTestSuite):
    _verify_genuine_finance_workflow = True
    _set_up_stock = ledger.TestWithdrawalRealLedgerIntegration._set_up_stock
    _bin_qty = ledger.TestWithdrawalRealLedgerIntegration._bin_qty
    _actor = finance.TestFinanceWithdrawalBillingBridgeDisposable._actor
    _approve_signed = finance.TestFinanceWithdrawalBillingBridgeDisposable._approve_signed

    def setUp(self):
        if (
            frappe.local.site != SITE
            or os.environ.get("CCPC_PHASE1_DISPOSABLE") != "1"
            or not frappe.conf.get("db_socket")
            or frappe.conf.get("db_type", "mariadb") != "mariadb"
        ):
            self.skipTest("Requires the explicitly enabled disposable Phase 1 MariaDB socket site")
        super().setUp()
        self.addCleanup(frappe.set_user, "Administrator")
        self._set_up_stock()
        self._actor(SUPERVISOR, "Withdrawal Slip Requestor")
        self.center = frappe.db.get_value(
            "Cost Center", {"company": ledger.COMPANY, "is_group": 0, "disabled": 0}, "name"
        )
        self.assertTrue(self.center)

    def _review_slip(self, requesting_company=ledger.COMPANY, department=None):
        # The workflow records a named requestor and a signed-in preparing
        # supervisor. The Stockroom user then reviews and issues independently.
        frappe.set_user(SUPERVISOR)
        slip = frappe.get_doc({
            "doctype": "Withdrawal Slip",
            "requestor": "Synthetic employee requesting material",
            "company": ledger.COMPANY,
            "requesting_company": requesting_company,
            "department": department or self.department,
            "work_for_department": self.department,
            "source_warehouse": ledger.WAREHOUSE,
            "physical_location": "Synthetic test bay",
            "purpose": "Phase 1 inventory and accounting acceptance",
            "items": [{"item_code": self.item.name, "requested_qty": ledger.QTY_ISSUED}],
        }).insert()
        self.assertEqual(slip.status, "Draft")
        self.assertEqual(slip.approving_supervisor, SUPERVISOR)
        doc = apply_workflow(slip.as_dict(), "Approve & Send to Stockroom")
        self.assertEqual(doc.status, "Approved")
        frappe.set_user(ledger.ISSUER)
        doc = apply_workflow(slip.as_dict(), "Start Review")
        self.assertEqual(doc.status, "Stockroom Review")
        slip.reload()
        slip.received_by = "Synthetic material receiver"
        slip.save()
        return slip

    def _sign_mappings(self):
        self._actor(finance.MAKER, "CCPC Mapping Preparer")
        self._actor(finance.CHECKER, "CCPC Accounting Mapping Approver")
        return (
            self._approve_signed("Expense", ITEM_GROUP, ACCOUNT),
            self._approve_signed("Center", self.department, self.center),
        )

    def _onboard_initial_mappings(self):
        self._actor(finance.MAKER, onboarding.PREPARER)
        self._actor(finance.CHECKER, onboarding.CHECKER)
        revisions = []
        for kind, source, value in (("Expense", ITEM_GROUP, ACCOUNT), ("Center", self.department, self.center)):
            frappe.set_user(finance.MAKER)
            self.assertEqual(onboarding.get_pilot_context(), {
                "companies": [ledger.COMPANY], "can_propose": True, "can_review": False,
            })
            self.assertIn(source, onboarding.search_pilot_master(kind, ledger.COMPANY, "source", source))
            self.assertIn(value, onboarding.search_pilot_master(kind, ledger.COMPANY, "value", value))
            self.assertEqual(onboarding.get_pilot_coverage(kind, ledger.COMPANY, source), {"active": None, "draft": None})
            draft = onboarding.propose_pilot_mapping(kind, ledger.COMPANY, source, value)
            self.assertEqual(draft["state"], "Draft")

            frappe.set_user(finance.CHECKER)
            self.assertEqual(onboarding.get_pilot_context(), {
                "companies": [ledger.COMPANY], "can_propose": False, "can_review": True,
            })
            self.assertEqual(onboarding.get_pilot_coverage(kind, ledger.COMPANY, source), {
                "active": None, "draft": draft["name"],
            })
            review = onboarding.review_pilot_mapping(kind, draft["name"])
            self.assertEqual((review["source"], review["value"], review["owner"]), (source, value, finance.MAKER))
            approved = onboarding.sign_and_approve_initial_mapping(kind, draft["name"], review["modified"])
            self.assertEqual(approved["approved_by"], finance.CHECKER)
            self.assertTrue(approved["version"] and approved["decision_use"])
            self.assertEqual(onboarding.get_pilot_coverage(kind, ledger.COMPANY, source), {
                "active": {"revision": draft["name"], "value": value}, "draft": None,
            })
            revisions.append(draft["name"])
        return revisions

    def _inventory_and_ledger_counts(self, slip):
        return (
            self._bin_qty(),
            frappe.db.count("Stock Entry", {"withdrawal_slip": slip.name}),
            frappe.db.count("Stock Ledger Entry", {"item_code": self.item.name}),
            frappe.db.count("GL Entry", {"company": ledger.COMPANY}),
        )

    def test_actual_issue_reconciles_with_ledger_and_department_report_once(self):
        expense_revision, center_revision = self._onboard_initial_mappings()
        slip = self._review_slip()
        self.assertFalse(frappe.has_permission("Stock Entry", "create"))
        self.assertEqual(frappe.session.user, ledger.ISSUER)
        result = issue_withdrawal_slip(slip.name)
        self.assertEqual(result["submitted"], 1)

        frappe.set_user("Administrator")
        stock = frappe.get_doc("Stock Entry", result["name"])
        self.assertEqual(stock.docstatus, 1)
        self.assertEqual(stock.purpose, "Material Issue")
        self.assertEqual(stock.company, ledger.COMPANY)
        self.assertEqual(stock.department, self.department)
        self.assertEqual(len(stock.items), 1)
        detail = stock.items[0]
        self.assertEqual(detail.withdrawal_slip_item, slip.items[0].name)
        self.assertEqual(detail.department, self.department)
        self.assertEqual(detail.expense_account, ACCOUNT)
        self.assertEqual(detail.cost_center, self.center)
        self.assertEqual(detail.ccpc_expense_mapping_revision, expense_revision)
        self.assertEqual(detail.ccpc_center_mapping_revision, center_revision)
        self.assertEqual(detail.stock_uom, STOCK_UOM)

        voucher = {"voucher_type": "Stock Entry", "voucher_no": stock.name, "is_cancelled": 0}
        sle = frappe.get_all("Stock Ledger Entry", filters=voucher,
                             fields=["company", "warehouse", "voucher_detail_no", "actual_qty",
                                     "stock_value_difference"])
        self.assertEqual(len(sle), 1)
        self.assertEqual(sle[0].company, ledger.COMPANY)
        self.assertEqual(sle[0].warehouse, ledger.WAREHOUSE)
        self.assertEqual(sle[0].voucher_detail_no, detail.name)
        self.assertEqual(sle[0].actual_qty, -ledger.QTY_ISSUED)
        value = ledger.QTY_ISSUED * ledger.UNIT_RATE
        self.assertAlmostEqual(sle[0].stock_value_difference, -value, places=2)
        self.assertEqual(self._bin_qty(), ledger.QTY_RECEIVED - ledger.QTY_ISSUED)

        gl = frappe.get_all("GL Entry", filters=voucher,
                            fields=["company", "account", "cost_center", "department", "debit", "credit"])
        self.assertTrue(all(row.company == ledger.COMPANY for row in gl))
        expenses = [row for row in gl if row.account == ACCOUNT]
        self.assertEqual(len(expenses), 1)
        self.assertEqual(expenses[0].cost_center, self.center)
        self.assertEqual(expenses[0].department, self.department)
        self.assertAlmostEqual(expenses[0].debit, value, places=2)
        self.assertEqual(expenses[0].credit, 0)
        self.assertEqual(len(gl), 2)
        inventory = next(row for row in gl if row.account != ACCOUNT)
        self.assertEqual(frappe.db.get_value("Account", inventory.account, "account_type"), "Stock")
        self.assertAlmostEqual(sum(row.debit for row in gl), value, places=2)
        self.assertAlmostEqual(sum(row.credit for row in gl), value, places=2)

        _, rows = report.execute({"company": ledger.COMPANY, "department": self.department,
                                  "from_date": stock.posting_date, "to_date": stock.posting_date})
        issue_rows = [row for row in rows if row["withdrawal_slip"] == slip.name]
        self.assertEqual(len(issue_rows), 1)
        row = issue_rows[0]
        self.assertEqual(row["stock_entry_detail"], detail.name)
        self.assertEqual(row["issuing_company"], ledger.COMPANY)
        self.assertEqual(row["issuing_currency"], frappe.db.get_value("Company", stock.company, "default_currency"))
        self.assertEqual(row["beneficiary_department_at_issue"], self.department)
        self.assertEqual(row["stock_uom"], STOCK_UOM)
        self.assertEqual(row["issued_qty"], ledger.QTY_ISSUED)
        self.assertEqual(row["sle_issued_qty"], -sle[0].actual_qty)
        self.assertAlmostEqual(row["gross_sle_issue_value"], -sle[0].stock_value_difference, places=2)
        self.assertEqual(row["source_quality"], "OK")
        slip.reload()
        self.assertEqual(slip.status, "Fully Issued")
        self.assertEqual(slip.issued_by, ledger.ISSUER)
        self.assertEqual(slip.items[0].issued_qty, ledger.QTY_ISSUED)

        before = self._inventory_and_ledger_counts(slip)
        frappe.set_user(ledger.ISSUER)
        with self.assertRaisesRegex(frappe.ValidationError, "Stockroom Review|already has"):
            issue_withdrawal_slip(slip.name)
        self.assertEqual(self._inventory_and_ledger_counts(slip), before)
        self.assertEqual(before[1], 1)

    def test_wrong_role_and_missing_mapping_leave_inventory_and_gl_untouched(self):
        slip = self._review_slip()
        before = self._inventory_and_ledger_counts(slip)
        frappe.set_user(SUPERVISOR)
        with self.assertRaisesRegex(frappe.PermissionError, "Only Stockroom users"):
            issue_withdrawal_slip(slip.name)
        frappe.set_user(ledger.ISSUER)
        with self.assertRaises(MappingPolicyError):
            issue_withdrawal_slip(slip.name)
        self.assertEqual(self._inventory_and_ledger_counts(slip), before)

    def test_finance_onboarding_enforces_role_company_and_exact_master(self):
        self.load_test_records("Sales Invoice")
        self._actor(finance.MAKER, onboarding.PREPARER)
        self._actor(finance.CHECKER, onboarding.CHECKER)
        for user in ("Administrator", ledger.ISSUER):
            frappe.set_user(user)
            with self.assertRaises(frappe.ValidationError):
                onboarding.get_pilot_context()

        frappe.set_user(finance.MAKER)
        with self.assertRaisesRegex(frappe.ValidationError, "Company-scoped permission"):
            onboarding.search_pilot_master("Expense", finance.BUYER, "value")
        with self.assertRaisesRegex(frappe.ValidationError, "Company must exist"):
            onboarding.get_pilot_coverage("Expense", ledger.COMPANY.lower(), ITEM_GROUP)
        with self.assertRaisesRegex(frappe.ValidationError, "exact leaf Item Group"):
            onboarding.propose_pilot_mapping("Expense", ledger.COMPANY, ITEM_GROUP.lower(), ACCOUNT)
        self.assertEqual(onboarding.search_pilot_master("Expense", ledger.COMPANY, "source", "All Item Groups"), [])
        self.assertEqual(onboarding.search_pilot_master("Expense", ledger.COMPANY, "value", "Stock In Hand - _TC"), [])

        draft = onboarding.propose_pilot_mapping("Expense", ledger.COMPANY, ITEM_GROUP, ACCOUNT)
        with self.assertRaisesRegex(frappe.ValidationError, "explicit Finance mapping role"):
            onboarding.review_pilot_mapping("Expense", draft["name"])
        frappe.set_user(finance.CHECKER)
        with self.assertRaisesRegex(frappe.ValidationError, "Only Finance preparers"):
            onboarding.propose_pilot_mapping("Expense", ledger.COMPANY, ITEM_GROUP, ACCOUNT)
        review = onboarding.review_pilot_mapping("Expense", draft["name"])
        with self.assertRaisesRegex(frappe.ValidationError, "changed"):
            onboarding.sign_and_approve_initial_mapping("Expense", draft["name"], "stale")
        self._actor(finance.MAKER, onboarding.CHECKER)
        frappe.set_user(finance.MAKER)
        with self.assertRaisesRegex(frappe.ValidationError, "cannot approve their own"):
            onboarding.sign_and_approve_initial_mapping("Expense", draft["name"], review["modified"])

    def test_finance_onboarding_rolls_back_signature_and_approval_together(self):
        self._actor(finance.MAKER, onboarding.PREPARER)
        self._actor(finance.CHECKER, onboarding.CHECKER)
        frappe.set_user(finance.MAKER)
        draft = onboarding.propose_pilot_mapping("Expense", ledger.COMPANY, ITEM_GROUP, ACCOUNT)["name"]
        frappe.set_user(finance.CHECKER)
        review = onboarding.review_pilot_mapping("Expense", draft)
        original = onboarding.approve_mapping

        def fail_after_approval(*args):
            approved = original(*args)
            self.assertTrue(frappe.db.exists("CCPC Mapping Decision Use", approved["decision_use"]))
            raise RuntimeError("Synthetic audit transaction failure")

        with patch.object(onboarding, "approve_mapping", fail_after_approval):
            with self.assertRaisesRegex(RuntimeError, "Synthetic audit transaction failure"):
                onboarding.sign_and_approve_initial_mapping("Expense", draft, review["modified"])
        self.assertEqual(onboarding.review_pilot_mapping("Expense", draft)["state"], "Draft")
        self.assertFalse(frappe.db.exists("CCPC Accounting Mapping Decision", {"revision": draft}))
        self.assertFalse(frappe.db.exists("CCPC Mapping Decision Use", {"revision": draft}))
        self.assertFalse(frappe.db.exists("Version", {"ref_doctype": "CCPC Expense Mapping Revision", "docname": draft}))

        approved = onboarding.sign_and_approve_initial_mapping("Expense", draft, review["modified"])
        self.assertEqual(approved["state"], "Approved")
        with self.assertRaisesRegex(frappe.ValidationError, "untouched Draft"):
            onboarding.sign_and_approve_initial_mapping("Expense", draft, review["modified"])

    def test_company_without_perpetual_inventory_cannot_issue_without_expense_gl(self):
        self._sign_mappings()
        slip = self._review_slip()
        draft_slip = self._review_slip()
        frappe.set_user("Administrator")
        frappe.get_doc("User", ledger.ISSUER).add_roles("Stock Manager")
        frappe.set_user(ledger.ISSUER)
        draft = make_material_issue(draft_slip.name)["name"]

        frappe.set_user("Administrator")
        frappe.db.set_value("Company", ledger.COMPANY, "enable_perpetual_inventory", 0)
        frappe.clear_cache(doctype="Company")
        frappe.local.enable_perpetual_inventory.pop(ledger.COMPANY, None)
        self.assertFalse(is_perpetual_inventory_enabled(ledger.COMPANY))
        frappe.set_user(ledger.ISSUER)
        before = self._inventory_and_ledger_counts(slip)
        draft_before = self._inventory_and_ledger_counts(draft_slip)

        with self.assertRaisesRegex(frappe.ValidationError, "Perpetual Inventory"):
            issue_withdrawal_slip(slip.name)
            self.fail(f"Issue posted without perpetual inventory: {before} -> "
                      f"{self._inventory_and_ledger_counts(slip)} (stock, issues, SLEs, GL rows)")
        with self.assertRaisesRegex(frappe.ValidationError, "Perpetual Inventory"):
            frappe.get_doc("Stock Entry", draft).submit()

        self.assertEqual(self._inventory_and_ledger_counts(slip), before)
        self.assertEqual(self._inventory_and_ledger_counts(draft_slip), draft_before)
        self.assertEqual(frappe.db.get_value("Stock Entry", draft, "docstatus"), 0)
        self.assertEqual(slip.reload().status, "Stockroom Review")

    def test_cross_company_denied_in_stockroom_and_native_stock_entry(self):
        self.load_test_records("Sales Invoice")
        frappe.set_user("Administrator")
        buyer = frappe.get_doc({
            "doctype": "Department",
            "department_name": "Phase 1 foreign buyer " + frappe.generate_hash(length=8),
            "company": finance.BUYER,
            "is_group": 0,
        }).insert()
        # Permit preparing either Company for this fixture so it reaches the
        # Phase 1 issuance boundary rather than stopping at Company permissions.
        frappe.get_doc({"doctype": "User Permission", "user": SUPERVISOR, "allow": "Company",
                        "for_value": finance.BUYER, "apply_to_all_doctypes": 1}).insert()
        frappe.get_doc("User", ledger.ISSUER).add_roles("Stock Manager")
        slip = self._review_slip(finance.BUYER, buyer.name)
        before = self._inventory_and_ledger_counts(slip)
        with self.assertRaisesRegex(frappe.PermissionError, "Phase 1"):
            issue_withdrawal_slip(slip.name)

        stock = frappe.get_doc({
            "doctype": "Stock Entry", "stock_entry_type": "Material Issue", "purpose": "Material Issue",
            "company": ledger.COMPANY, "from_warehouse": ledger.WAREHOUSE,
            "items": [{"item_code": self.item.name, "s_warehouse": ledger.WAREHOUSE,
                       "qty": ledger.QTY_ISSUED, "expense_account": ACCOUNT, "cost_center": self.center}],
        }).insert()
        # A draft written before Phase 1 must also be rejected at native submit.
        stock.db_set("withdrawal_slip", slip.name)
        stock.items[0].db_set("withdrawal_slip_item", slip.items[0].name)
        with self.assertRaisesRegex(frappe.PermissionError, "Phase 1"):
            stock.reload().submit()
        self.assertEqual(frappe.db.get_value("Stock Entry", stock.name, "docstatus"), 0)
        self.assertEqual(self._inventory_and_ledger_counts(slip), (before[0], 1, before[2], before[3]))

    def test_posting_failure_rolls_back_stock_issue_ledgers_and_slip(self):
        self._sign_mappings()
        slip = self._review_slip()
        before = self._inventory_and_ledger_counts(slip)
        posting = []
        original = issuance.sync_withdrawal_slip_issue_status

        def fail_after_posting(stock, method):
            original(stock, method)
            posting.append((
                self._inventory_and_ledger_counts(slip),
                frappe.db.get_value("Withdrawal Slip", slip.name, "status"),
            ))
            raise frappe.ValidationError("Synthetic failure after native ledger posting")

        frappe.db.savepoint("phase1_post_failure")
        try:
            with patch.object(issuance, "sync_withdrawal_slip_issue_status", fail_after_posting):
                with self.assertRaisesRegex(frappe.ValidationError, "Synthetic failure"):
                    issue_withdrawal_slip(slip.name)
        finally:
            frappe.db.rollback(save_point="phase1_post_failure")

        posted_counts = (before[0] - ledger.QTY_ISSUED, 1, before[2] + 1, before[3] + 2)
        self.assertEqual(posting, [(posted_counts, "Fully Issued")])
        self.assertEqual(self._inventory_and_ledger_counts(slip), before)
        slip.reload()
        self.assertEqual(slip.status, "Stockroom Review")
        self.assertEqual(slip.items[0].issued_qty, 0)
        self.assertEqual(issue_withdrawal_slip(slip.name)["submitted"], 1)

    def test_cancelled_issue_excluded_and_reissue_reported_once(self):
        """A cancelled issue must leave no consumption row; replacement is counted once."""
        self._onboard_initial_mappings()
        slip = self._review_slip()
        self.assertEqual(frappe.session.user, ledger.ISSUER)

        original_name = issue_withdrawal_slip(slip.name)["name"]
        frappe.set_user("Administrator")
        original = frappe.get_doc("Stock Entry", original_name)
        filters = {
            "company": ledger.COMPANY,
            "department": self.department,
            "from_date": original.posting_date,
            "to_date": original.posting_date,
        }
        _, initial_rows = report.execute(filters)
        initial = [row for row in initial_rows if row["withdrawal_slip"] == slip.name]
        self.assertEqual(len(initial), 1)
        self.assertEqual(initial[0]["stock_entry"], original_name)

        # Use genuine ERPNext cancellation and CCPC on_cancel hooks, never
        # manually change docstatus or purge historical ledger entries.
        original.cancel()
        self.assertEqual(original.docstatus, 2)
        self.assertEqual(self._bin_qty(), ledger.QTY_RECEIVED)
        slip.reload()
        self.assertEqual(slip.status, "Stockroom Review")
        self.assertEqual(slip.items[0].issued_qty, 0)

        _, cancelled_rows = report.execute(filters)
        self.assertEqual(
            [row for row in cancelled_rows if row["withdrawal_slip"] == slip.name],
            [],
            "Cancelled voucher must not remain in Department consumption",
        )
        for doctype in ("Stock Ledger Entry", "GL Entry"):
            self.assertEqual(
                frappe.db.count(doctype, {
                    "voucher_type": "Stock Entry",
                    "voucher_no": original_name,
                    "is_cancelled": 0,
                }),
                0,
                f"Cancelled {doctype} must have no active original rows",
            )

        # The same approved slip may be reissued only by its independent
        # Stockroom issuer; the preparer/self-approval policy is unchanged.
        frappe.set_user(ledger.ISSUER)
        replacement_name = issue_withdrawal_slip(slip.name)["name"]
        self.assertNotEqual(replacement_name, original_name)
        frappe.set_user("Administrator")
        replacement = frappe.get_doc("Stock Entry", replacement_name)
        self.assertEqual(replacement.docstatus, 1)
        self.assertEqual(replacement.company, ledger.COMPANY)
        self.assertEqual(self._bin_qty(), ledger.QTY_RECEIVED - ledger.QTY_ISSUED)
        self.assertEqual(
            frappe.db.count("Stock Entry", {"withdrawal_slip": slip.name, "docstatus": 1}),
            1,
        )

        _, replacement_rows = report.execute(filters)
        actual = [row for row in replacement_rows if row["withdrawal_slip"] == slip.name]
        self.assertEqual(len(actual), 1, "Replacement must appear exactly once")
        row = actual[0]
        self.assertEqual(row["stock_entry"], replacement_name)
        self.assertEqual(row["stock_entry_detail"], replacement.items[0].name)
        self.assertEqual(row["beneficiary_department_at_issue"], self.department)
        self.assertEqual(row["issuing_company"], ledger.COMPANY)
        self.assertEqual(row["stock_uom"], STOCK_UOM)
        self.assertEqual(row["issued_qty"], ledger.QTY_ISSUED)
        self.assertEqual(row["sle_issued_qty"], ledger.QTY_ISSUED)
        self.assertAlmostEqual(
            row["gross_sle_issue_value"], ledger.QTY_ISSUED * ledger.UNIT_RATE, places=2
        )
        self.assertEqual(row["source_quality"], "OK")
        self.assertNotIn(original_name, [r["stock_entry"] for r in actual])

        active_sle = frappe.get_all(
            "Stock Ledger Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": replacement_name, "is_cancelled": 0},
            fields=["voucher_detail_no", "actual_qty", "stock_value_difference"],
        )
        self.assertEqual(len(active_sle), 1)
        self.assertEqual(active_sle[0].voucher_detail_no, replacement.items[0].name)
        self.assertAlmostEqual(active_sle[0].actual_qty, -ledger.QTY_ISSUED, places=5)
        self.assertAlmostEqual(
            row["gross_sle_issue_value"], -active_sle[0].stock_value_difference, places=2
        )
        active_expense = frappe.get_all(
            "GL Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": replacement_name,
                     "account": ACCOUNT, "is_cancelled": 0},
            fields=["debit", "credit", "cost_center", "department"],
        )
        self.assertEqual(len(active_expense), 1)
        self.assertEqual(active_expense[0].cost_center, self.center)
        self.assertEqual(active_expense[0].department, self.department)
        self.assertAlmostEqual(
            active_expense[0].debit, row["gross_sle_issue_value"], places=2
        )
        self.assertAlmostEqual(active_expense[0].credit, 0, places=2)

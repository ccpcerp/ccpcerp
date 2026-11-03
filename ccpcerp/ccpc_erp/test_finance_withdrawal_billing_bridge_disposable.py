# Copyright (c) 2026, CCPC and contributors
"""Exact-source Finance -> Withdrawal -> SLE/GL -> billing-source SQL bridge.

DISPOSABLE-CI-ONLY suite. Real Frappe/ERPNext tables, authenticated
signed Finance mapping decisions, submitted stock/ledger records, true billing
preview and REAL paired Sales/Purchase *draft* invoice inserts. No invoice is
submitted; no production data or live site is accessed.

The new month-end invoice tests simulate only the billing Controller clock,
and inject a second-invoice failure to probe actual MariaDB savepoint rollback.
Runs only via the default-off, manually held exact-SHA Finance bridge workflow;
every test requires its established disposable CI identity/site guard.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from erpnext.accounts.doctype.account.account import get_root_company
from erpnext.tests.utils import ERPNextTestSuite
from frappe.utils import flt, get_last_day, getdate, nowdate

from ccpcerp.ccpc_erp.doctype.intercompany_material_billing_batch.intercompany_material_billing_batch import (
    IntercompanyMaterialBillingBatch as Billing,
)

from ccpcerp.ccpc_erp.bc_finance_evidence import sign_mapping_decision
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
from ccpcerp.ccpc_erp.bc_mapping_workflow import (
    approve_mapping,
    get_mapping_review,
    propose_mapping,
)
from ccpcerp.ccpc_erp.ci_finance_lifecycle_site_guard import (
    assert_finance_disposable_site,
)
from ccpcerp.ccpc_erp.doctype.withdrawal_slip import (
    test_withdrawal_real_ledger_integration as base_ledger,
)
from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip

COMPANY = base_ledger.COMPANY
ISSUER = base_ledger.ISSUER
QTY_ISSUED = base_ledger.QTY_ISSUED
QTY_RECEIVED = base_ledger.QTY_RECEIVED
UNIT_RATE = base_ledger.UNIT_RATE
MAKER = "ci-bridge-finance-maker@invalid.test"
CHECKER = "ci-bridge-finance-checker@invalid.test"
BUYER = "_Test Company 5"


class TestFinanceWithdrawalBillingBridgeDisposable(ERPNextTestSuite):
    # Borrow the existing reviewed real-stock fixture, but do NOT let it seed
    # synthetic Approved mappings: approval below uses the actual signed API.
    _verify_genuine_finance_workflow = True
    _bin_qty = base_ledger.TestWithdrawalRealLedgerIntegration._bin_qty

    def setUp(self):
        # This MUST precede all fixture loading, SQL queries and user writes.
        assert_finance_disposable_site(getattr(frappe.local, "site", None))
        super().setUp()
        base_ledger.TestWithdrawalRealLedgerIntegration.setUp(self)
        frappe.set_user("Administrator")
        self.load_test_records("Sales Invoice")
        self.assertTrue(frappe.db.exists("Company", BUYER))
        self.assertNotEqual(COMPANY, BUYER)
        self.assertEqual(
            frappe.db.get_value("Company", COMPANY, "default_currency"),
            frappe.db.get_value("Company", BUYER, "default_currency"),
            "Disposable intercompany sample requires equal currencies.",
        )
        # Build the cross-company Withdrawal request with CORRECT fields at
        # original insert, before the base fixture's test-only workflow shortcut.
        # Never change immutable request intent on a Stockroom Review slip.
        buyer_department = frappe.get_doc({
            "doctype": "Department",
            "department_name": "CI Bridge Request " + frappe.generate_hash(length=10),
            "company": BUYER,
            "is_group": 0,
        }).insert(ignore_permissions=True)
        self.assertEqual(
            frappe.db.get_value("Department", buyer_department.name, "company"),
            BUYER,
        )
        frappe.set_user(base_ledger.PREPARER)
        intercompany_slip = frappe.get_doc({
            "doctype": "Withdrawal Slip",
            "requestor": "CI Cross-Company Synthetic Requestor",
            "approving_supervisor": base_ledger.PREPARER,
            "required_by": nowdate(),
            "source_warehouse": base_ledger.WAREHOUSE,
            "company": COMPANY,
            "requesting_company": BUYER,
            "department": buyer_department.name,
            "work_for_department": self.department,
            "physical_location": "Disposable intercompany CI bay",
            "purpose": "Disposable signed-ledger-to-billing integration test",
            "received_by": "CI Synthetic Receiver",
            "items": [{
                "item_code": self.item.name,
                "requested_qty": QTY_ISSUED,
                "issue_qty": QTY_ISSUED,
                "uom": "_Test UOM",
            }],
        }).insert(ignore_permissions=True)
        self.assertEqual(intercompany_slip.status, "Draft")
        self.assertEqual(intercompany_slip.company, COMPANY)
        self.assertEqual(intercompany_slip.requesting_company, BUYER)
        self.assertEqual(intercompany_slip.department, buyer_department.name)
        self.assertEqual(intercompany_slip.work_for_department, self.department)
        self.assertEqual(intercompany_slip.owner, base_ledger.PREPARER)
        # Reuse the preexisting real-stock fixture's explicitly test-only
        # Stockroom Review shortcut; we are NOT proving the workflow transition.
        frappe.db.set_value(
            "Withdrawal Slip", intercompany_slip.name,
            "status", "Stockroom Review",
        )
        self.slip_name = intercompany_slip.name
        self.slip_item_name = intercompany_slip.items[0].name
        frappe.set_user("Administrator")
        self.account = "Stock Adjustment - _TC"
        self.center = frappe.db.get_value(
            "Cost Center", {"company": COMPANY, "is_group": 0, "disabled": 0},
            "name",
        )
        self.assertTrue(self.center)

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()

    def _actor(self, email, role):
        frappe.set_user("Administrator")
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role}).insert(
                ignore_permissions=True
            )
        if not frappe.db.exists("User", email):
            frappe.get_doc({
                "doctype": "User",
                "email": email,
                "first_name": "Disposable Bridge Finance",
                "enabled": 1,
                "user_type": "System User",
                "send_welcome_email": 0,
            }).insert(ignore_permissions=True)
        user = frappe.get_doc("User", email)
        self.assertFalse(any(r.role == "Stockroom Personnel" for r in user.roles))
        if not any(r.role == role for r in user.roles):
            user.append("roles", {"role": role})
            user.save(ignore_permissions=True)
        if not frappe.db.exists("User Permission", {
            "user": email, "allow": "Company", "for_value": COMPANY,
            "apply_to_all_doctypes": 1,
        }):
            frappe.get_doc({
                "doctype": "User Permission",
                "user": email,
                "allow": "Company",
                "for_value": COMPANY,
                "apply_to_all_doctypes": 1,
            }).insert(ignore_permissions=True)

    def _approve_signed(self, kind, source, value):
        frappe.set_user(MAKER)
        draft = propose_mapping(kind, COMPANY, source, value)
        self.assertEqual(draft["state"], "Draft")
        frappe.set_user(CHECKER)
        reviewed = get_mapping_review(kind, draft["name"])
        self.assertEqual(reviewed["owner"], MAKER)
        with self.assertRaises(MappingPolicyError):
            approve_mapping(kind, draft["name"], reviewed["modified"], "FORGED CI APPROVAL")
        signed = sign_mapping_decision(
            kind, draft["name"], reviewed["modified"], "Approve"
        )
        self.assertEqual(signed["state"], "Signed")
        approved = approve_mapping(
            kind, draft["name"], reviewed["modified"], signed["name"]
        )
        self.assertEqual(approved["state"], "Approved")
        self.assertEqual(approved["approved_by"], CHECKER)
        self.assertEqual(
            frappe.db.count("CCPC Mapping Decision Use", {"decision": signed["name"]}),
            1,
        )
        return draft["name"]

    def _billing_candidate(self):
        frappe.set_user("Administrator")
        batch = frappe.get_doc({
            "doctype": "Intercompany Material Billing Batch",
            "issuing_company": COMPANY,
            "requesting_company": BUYER,
            "billing_month": nowdate(),
            "status": "Draft",
        }).insert(ignore_permissions=True)
        return batch

    def test_a_signed_issue_reaches_real_ledger_and_billing_source(self):
        """One approved original issue appears once in a real billing preview."""
        self._actor(MAKER, "CCPC Mapping Preparer")
        self._actor(CHECKER, "CCPC Accounting Mapping Approver")
        expense_revision = self._approve_signed(
            "Expense", "_Test Item Group", self.account
        )
        center_revision = self._approve_signed(
            "Center", self.department, self.center
        )
        frappe.set_user(ISSUER)
        result = issue_withdrawal_slip(self.slip_name)
        stock = frappe.get_doc("Stock Entry", result["name"])
        self.assertEqual(stock.docstatus, 1)
        self.assertEqual(stock.company, COMPANY)
        self.assertEqual(stock.withdrawal_slip, self.slip_name)
        self.assertEqual(len(stock.items), 1)
        detail = stock.items[0]
        self.assertEqual(detail.ccpc_expense_mapping_revision, expense_revision)
        self.assertEqual(detail.ccpc_center_mapping_revision, center_revision)
        self.assertEqual(detail.expense_account, self.account)
        self.assertEqual(detail.cost_center, self.center)
        self.assertEqual(detail.department, self.department)
        self.assertAlmostEqual(self._bin_qty(), QTY_RECEIVED - QTY_ISSUED)

        ledger = frappe.get_all(
            "Stock Ledger Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": stock.name,
                     "is_cancelled": 0},
            fields=["item_code", "actual_qty", "stock_value_difference"],
        )
        self.assertTrue(ledger, "Submitted source has no real Stock Ledger Entry")
        self.assertAlmostEqual(sum(flt(r.actual_qty) for r in ledger), -QTY_ISSUED)
        inventory_outflow_value = -sum(
            flt(r.stock_value_difference) for r in ledger
        )
        self.assertGreater(inventory_outflow_value, 0)
        gl = frappe.get_all(
            "GL Entry", filters={"voucher_type": "Stock Entry",
                                 "voucher_no": stock.name, "is_cancelled": 0},
            fields=["account", "cost_center", "department", "debit", "credit"],
        )
        expense = [g for g in gl if g.account == self.account and flt(g.debit) > 0]
        self.assertTrue(expense, "Signed mapping did not reach real expense GL")
        expense_debit = sum(
            flt(r.debit) - flt(r.credit) for r in expense
        )
        self.assertAlmostEqual(expense_debit, QTY_ISSUED * UNIT_RATE, places=2)
        self.assertAlmostEqual(
            expense_debit, inventory_outflow_value, places=2,
            msg="Finance GL expense must reconcile to real stock-value outflow",
        )
        self.assertTrue(all(
            g.cost_center == self.center and g.department == self.department
            for g in expense
        ))

        batch = self._billing_candidate()
        first = batch.refresh_preview()
        self.assertEqual(first["line_count"], 1)
        current = frappe.get_doc("Intercompany Material Billing Batch", batch.name)
        self.assertEqual(current.status, "Draft")
        self.assertEqual(len(current.items), 1)
        row = current.items[0]
        self.assertEqual(row.stock_entry, stock.name)
        self.assertEqual(row.stock_entry_detail, detail.name)
        self.assertEqual(row.withdrawal_slip, self.slip_name)
        self.assertEqual(row.withdrawal_slip_item, detail.withdrawal_slip_item)
        self.assertEqual(row.item_code, detail.item_code)
        self.assertAlmostEqual(flt(row.quantity), QTY_ISSUED)
        self.assertAlmostEqual(flt(row.frozen_basic_amount), flt(detail.basic_amount))
        self.assertFalse(current.sales_invoice or current.purchase_invoice)
        second = current.refresh_preview()
        self.assertEqual(second["line_count"], 1)
        self.assertEqual(frappe.db.count("Stock Entry", {
            "withdrawal_slip": self.slip_name, "docstatus": 1,
        }), 1, "Preview replay must not post another Stock Entry")
        self.assertEqual(frappe.db.count("GL Entry", {
            "voucher_type": "Stock Entry", "voucher_no": stock.name,
            "is_cancelled": 0,
        }), len(gl), "Preview replay must not repost GL")
        with self.assertRaises(Exception):
            frappe.set_user(ISSUER)
            issue_withdrawal_slip(self.slip_name)

    def test_b_unsigned_issue_cannot_enter_billing_preview(self):
        """Missing signed maps must leave zero stock, GL and billable source."""
        frappe.set_user(ISSUER)
        before_qty = self._bin_qty()
        before_sle = frappe.db.count("Stock Ledger Entry", {
            "voucher_type": "Stock Entry", "item_code": self.item.name,
        })
        with self.assertRaises(MappingPolicyError):
            issue_withdrawal_slip(self.slip_name)
        self.assertAlmostEqual(self._bin_qty(), before_qty)
        self.assertEqual(frappe.db.count("Stock Entry", {
            "withdrawal_slip": self.slip_name, "docstatus": 1,
        }), 0)
        self.assertEqual(frappe.db.count("Stock Ledger Entry", {
            "voucher_type": "Stock Entry", "item_code": self.item.name,
        }), before_sle, "Rejected issue must not add SLE beyond the fixture receipt")
        batch = self._billing_candidate()
        preview = batch.refresh_preview()
        self.assertEqual(preview["line_count"], 0)
        self.assertFalse(frappe.get_doc(
            "Intercompany Material Billing Batch", batch.name
        ).items)

    # These source-to-invoice tests intentionally reuse the already passing
    # synthetic original-issue fixture and FINANCE site/SHA guard above. The
    # only mocks are a controlled month-end clock and a deliberate failure
    # injection; real Frappe Documents and MariaDB writes are not mocked.

    def _create_internal_party(self, kind, represents_company, allowed_company):
        self.assertIn(kind, ("Customer", "Supplier"))
        internal_field = (
            "is_internal_customer" if kind == "Customer"
            else "is_internal_supplier"
        )
        self.assertFalse(frappe.get_all(
            kind,
            filters={internal_field: 1, "represents_company": represents_company,
                     "disabled": 0},
            pluck="name",
        ), "Disposable test fixture must not reuse an unknown internal party")
        grouping = "Customer Group" if kind == "Customer" else "Supplier Group"
        group = frappe.db.get_value(grouping, {"is_group": 0}, "name")
        self.assertTrue(group, "Missing ERPNext synthetic " + grouping)
        name = "CI-SOURCE-PAIR-" + frappe.generate_hash(length=12)
        values = {
            "doctype": kind,
            "customer_name" if kind == "Customer" else "supplier_name": name,
            "customer_group" if kind == "Customer" else "supplier_group": group,
            internal_field: 1,
            "represents_company": represents_company,
            "companies": [{"company": allowed_company}],
        }
        if kind == "Customer":
            values.update(
                customer_type="Company",
                territory=frappe.db.get_value(
                    "Territory", {"is_group": 0}, "name"
                ),
            )
        else:
            values["supplier_type"] = "Company"
        return frappe.get_doc(values).insert(ignore_permissions=True).name

    def _ensure_disposable_account(
        self, company, number, label, root_type, account_type=None
    ):
        """Create exact synthetic Accounts with ERPNext parent-company rules.

        '_Test Company 5' is nested below '_Test Company 3'. ERPNext v16
        requires the account name on the root Company first; its native
        Account validation may copy it to matching descendant COAs. Keep
        that validation enabled: no ignore flags and no Company policy toggle.
        This function executes ONLY after the disposable-site guard in setUp.
        """
        abbr = frappe.db.get_value("Company", company, "abbr")
        self.assertTrue(abbr)
        expected = f"{number} - {label} - {abbr}"

        def check_or_create(target_company, parent_account):
            target_abbr = frappe.db.get_value("Company", target_company, "abbr")
            self.assertTrue(target_abbr)
            target_name = f"{number} - {label} - {target_abbr}"
            by_number = frappe.db.get_value(
                "Account",
                {"company": target_company, "account_number": number},
                "name",
            )
            by_name = frappe.db.exists("Account", target_name)
            self.assertFalse(
                by_number and by_number != target_name,
                "Synthetic Account number already belongs to a different Account",
            )
            self.assertFalse(
                by_name and by_name != by_number,
                "Synthetic Account name has unexpected numbering",
            )
            if not by_name:
                doc = frappe.get_doc({
                    "doctype": "Account",
                    "account_number": number,
                    "account_name": label,
                    "company": target_company,
                    "parent_account": parent_account,
                    "account_type": account_type or "",
                    "is_group": 0,
                }).insert(ignore_permissions=True)
                self.assertEqual(doc.name, target_name)

            state = frappe.db.get_value(
                "Account", target_name,
                ["company", "account_name", "account_number",
                 "root_type", "account_type", "is_group", "disabled"],
                as_dict=True,
            )
            self.assertTrue(state, "ERPNext did not persist required Account")
            self.assertEqual(state.company, target_company)
            self.assertEqual(state.account_name, label)
            self.assertEqual(str(state.account_number), str(number))
            self.assertEqual(state.root_type, root_type)
            self.assertEqual(state.account_type or "", account_type or "")
            self.assertFalse(state.is_group or state.disabled)
            return target_name

        # All parent-child COAs must share the same parent Account identity,
        # including the account number when present. Do not guess a cross-
        # company Chart or create a child Account against a missing root name.
        parent = frappe.db.get_value(
            "Account",
            {"company": company, "root_type": root_type,
             "is_group": 1, "disabled": 0},
            ["name", "account_name", "account_number"],
            as_dict=True,
        )
        self.assertTrue(parent, "Missing synthetic Account parent " + root_type)

        roots = get_root_company(company)
        if roots:
            self.assertEqual(len(roots), 1)
            root_company = roots[0]
            self.assertNotEqual(root_company, company)
            # Require same existing parent group in the root chart. This is
            # deliberately fail-closed rather than bypassing ERPNext v16
            # root-first Account validation or changing Company settings.
            root_parent_filter = {
                "company": root_company,
                "account_name": parent.account_name,
                "root_type": root_type,
                "is_group": 1,
                "disabled": 0,
            }
            if parent.account_number:
                root_parent_filter["account_number"] = parent.account_number
            root_parent = frappe.db.get_value(
                "Account", root_parent_filter, "name",
            )
            self.assertTrue(
                root_parent,
                "Synthetic root Company lacks corresponding group Account "
                + str(parent.account_name),
            )
            check_or_create(root_company, root_parent)
            # ERPNext may already have created the descendant Account via
            # real Controller synchronization. If not, the root prerequisite
            # now exists and the child insert must validate normally.

        result = check_or_create(company, parent.name)
        self.assertEqual(result, expected)
        return result

    def _prepare_disposable_invoice_masters(self):
        """Set up exact internal parties and Chart paths in disposable DB.

        Real Controller account and Company validation is intentionally not
        patched or relaxed. Never run against actual CCPC master data.
        """
        frappe.set_user("Administrator")
        self.load_test_records("Sales Invoice")
        self.customer = self._create_internal_party("Customer", BUYER, COMPANY)
        self.supplier = self._create_internal_party("Supplier", COMPANY, BUYER)
        specs = (
            ("1312", "Intercompany Receivable", "Asset", "Receivable"),
            ("2121", "Intercompany Payable", "Liability", "Payable"),
            ("5150", "Intercompany Materials Expense", "Expense", ""),
            ("6050", "Intercompany Material Charges", "Income", ""),
        )
        for company in (COMPANY, BUYER):
            center = frappe.db.get_value(
                "Cost Center", {
                    "company": company, "is_group": 0, "disabled": 0,
                }, "name",
            )
            self.assertTrue(center, "Missing buyer/issuer synthetic leaf Cost Center")
            # Synthetic site Company test fixtures are not CCPC master charts.
            # Supply only the explicit default required by existing billing.
            frappe.db.set_value(
                "Company", company, "cost_center", center,
                update_modified=False,
            )
            for number, label, root_type, account_type in specs:
                self._ensure_disposable_account(
                    company, number, label, root_type, account_type
                )

    def _issue_signed_source_for_real_pair(self):
        self._actor(MAKER, "CCPC Mapping Preparer")
        self._actor(CHECKER, "CCPC Accounting Mapping Approver")
        self._approve_signed("Expense", "_Test Item Group", self.account)
        self._approve_signed("Center", self.department, self.center)
        frappe.set_user(ISSUER)
        issued = issue_withdrawal_slip(self.slip_name)
        stock = frappe.get_doc("Stock Entry", issued["name"])
        self.assertEqual(stock.docstatus, 1)
        self.assertEqual(len(stock.items), 1)
        item = stock.items[0]
        sle = frappe.get_all(
            "Stock Ledger Entry", filters={
                "voucher_type": "Stock Entry", "voucher_no": stock.name,
                "is_cancelled": 0,
            },
            fields=["stock_value_difference", "actual_qty"],
        )
        self.assertTrue(sle)
        self.assertAlmostEqual(
            -sum(flt(row.actual_qty) for row in sle), QTY_ISSUED
        )
        expense = frappe.get_all(
            "GL Entry", filters={
                "voucher_type": "Stock Entry", "voucher_no": stock.name,
                "account": self.account, "is_cancelled": 0,
            },
            fields=["debit", "credit", "cost_center", "department"],
        )
        self.assertTrue(expense)
        self.assertAlmostEqual(
            sum(flt(row.debit - row.credit) for row in expense),
            -sum(flt(row.stock_value_difference) for row in sle),
            places=2,
        )
        self.assertTrue(all(
            row.department == self.department and row.cost_center == self.center
            for row in expense
        ))
        frappe.set_user("Administrator")
        batch = self._billing_candidate()
        preview = batch.refresh_preview()
        self.assertEqual(preview["line_count"], 1)
        batch.reload()
        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.items[0].stock_entry, stock.name)
        self.assertEqual(batch.items[0].stock_entry_detail, item.name)
        self.assertEqual(batch.items[0].withdrawal_slip, self.slip_name)
        self.assertAlmostEqual(
            flt(batch.items[0].frozen_basic_amount),
            flt(item.basic_amount),
            places=2,
        )
        return batch, stock, item

    def _finalize_real_month_end(self, batch):
        """Move only billing Controller's site clock to first day next month.

        The actual submitted Stock Entry retains its real server-stamped
        posting date. No backdating, global clock change, or period bypass.
        This simulates elapsed wall-clock month-end for the CI fixture only.
        """
        period_end = getdate(get_last_day(nowdate()))
        # First prove the real Controller rejects premature finalization.
        # If CI runs on a boundary day when the period already ended, this
        # same-day request still correctly fails (<= period_end).
        with self.assertRaises(Exception):
            batch.finalize_month()
        batch.reload()
        self.assertEqual(batch.status, "Draft")
        self.assertFalse(batch.finalized_on or batch.finalized_by)
        simulated_next_day = period_end + timedelta(days=1)
        after_month_end = datetime.combine(
            simulated_next_day, datetime.min.time()
        ) + timedelta(hours=9)
        module = (
            "ccpcerp.ccpc_erp.doctype.intercompany_material_billing_batch."
            "intercompany_material_billing_batch"
        )
        with (
            patch(module + ".today", return_value=str(simulated_next_day)),
            patch(module + ".now_datetime", return_value=after_month_end),
        ):
            completed = batch.finalize_month()
        self.assertEqual(completed["status"], "Under Review")
        self.assertEqual(completed["line_count"], 1)
        batch.reload()
        self.assertEqual(batch.status, "Under Review")
        self.assertEqual(getdate(batch.finalized_on), simulated_next_day)
        self.assertEqual(getdate(batch.period_end), period_end)
        self.assertEqual(batch.finalized_by, "Administrator")
        self.assertEqual(len(batch.items), 1)
        return module, after_month_end, simulated_next_day

    def _approve_real_pair_at_month_end(
        self, batch, module, after_month_end, simulated_next_day
    ):
        with (
            patch(module + ".today", return_value=str(simulated_next_day)),
            patch(module + ".now_datetime", return_value=after_month_end),
        ):
            return batch.approve_and_create_invoices()

    def _assert_drafts_are_real_and_unposted(self, result, stock, item, batch):
        sales = frappe.get_doc("Sales Invoice", result["sales_invoice"])
        purchase = frappe.get_doc("Purchase Invoice", result["purchase_invoice"])
        self.assertEqual((sales.company, purchase.company), (COMPANY, BUYER))
        self.assertEqual(sales.customer, self.customer)
        self.assertEqual(purchase.supplier, self.supplier)
        self.assertEqual(sales.inter_company_invoice_reference, purchase.name)
        self.assertEqual(purchase.inter_company_invoice_reference, sales.name)
        self.assertEqual((sales.docstatus, purchase.docstatus), (0, 0))
        self.assertFalse(sales.update_stock or purchase.update_stock)
        self.assertFalse(sales.taxes or purchase.taxes)
        self.assertEqual(len(sales.items), 1)
        self.assertEqual(len(purchase.items), 1)
        self.assertEqual(sales.items[0].item_code, item.item_code)
        self.assertEqual(purchase.items[0].item_code, item.item_code)
        self.assertAlmostEqual(flt(sales.items[0].qty), flt(item.transfer_qty))
        self.assertAlmostEqual(flt(purchase.items[0].qty), flt(item.transfer_qty))
        self.assertAlmostEqual(
            flt(sales.grand_total), flt(batch.total_amount), places=2
        )
        self.assertAlmostEqual(
            flt(purchase.grand_total), flt(batch.total_amount), places=2
        )
        for doctype, invoice in (
            ("Sales Invoice", sales), ("Purchase Invoice", purchase)
        ):
            self.assertEqual(frappe.db.count(
                "GL Entry", {
                    "voucher_type": doctype, "voucher_no": invoice.name,
                }
            ), 0, "Unsubmitted draft invoice must not post GL")
        self.assertEqual(frappe.db.count(
            "Stock Entry", {
                "withdrawal_slip": self.slip_name, "docstatus": 1,
            }
        ), 1)
        self.assertEqual(batch.sales_invoice, sales.name)
        self.assertEqual(batch.purchase_invoice, purchase.name)
        self.assertEqual(batch.status, "Approved")
        self.assertEqual(batch.items[0].stock_entry, stock.name)
        self.assertEqual(batch.items[0].stock_entry_detail, item.name)
        return sales, purchase

    def test_c_real_source_finalizes_to_actual_paired_invoice_drafts(self):
        """Real source→month-end→draft pair; only billing time is simulated."""
        batch, stock, detail = self._issue_signed_source_for_real_pair()
        self._prepare_disposable_invoice_masters()
        module, after_month_end, next_day = self._finalize_real_month_end(
            batch
        )
        approved = self._approve_real_pair_at_month_end(
            batch, module, after_month_end, next_day
        )
        batch.reload()
        self._assert_drafts_are_real_and_unposted(
            approved, stock, detail, batch
        )
        self.assertEqual(batch.approved_by, "Administrator")
        self.assertTrue(batch.approved_on)
        with self.assertRaises(Exception):
            self._approve_real_pair_at_month_end(
                batch, module, after_month_end, next_day
            )
        batch.reload()
        self.assertEqual(batch.sales_invoice, approved["sales_invoice"])
        self.assertEqual(batch.purchase_invoice, approved["purchase_invoice"])

    def test_d_second_draft_insert_failure_rolls_back_real_first_invoice(self):
        """Real SQL savepoint deletes first invoice, retry creates one pair."""
        batch, stock, detail = self._issue_signed_source_for_real_pair()
        self._prepare_disposable_invoice_masters()
        module, after_month_end, next_day = self._finalize_real_month_end(
            batch
        )
        # Real sales insert must occur before injecting the second invoice
        # builder failure. The first invoice SQL write must roll back even
        # though this test deliberately catches the exception.
        with (
            patch.object(Billing, "_build_purchase_invoice",
                         side_effect=RuntimeError("CI second invoice failure")),
            self.assertRaisesRegex(RuntimeError, "CI second invoice failure"),
        ):
            self._approve_real_pair_at_month_end(
                batch, module, after_month_end, next_day
            )
        batch.reload()
        self.assertEqual(batch.status, "Under Review")
        self.assertFalse(batch.sales_invoice or batch.purchase_invoice)
        self.assertFalse(batch.approved_by or batch.approved_on)
        marker = "Intercompany material billing batch " + batch.name
        self.assertEqual(frappe.db.count(
            "Sales Invoice", {"remarks": marker}
        ), 0, "Failed purchase must not leave an orphan real Sales Invoice")
        self.assertEqual(frappe.db.count(
            "Purchase Invoice", {"remarks": marker}
        ), 0)
        self.assertEqual(frappe.db.count(
            "Stock Entry", {
                "withdrawal_slip": self.slip_name, "docstatus": 1,
            }
        ), 1, "Failed invoice approval must not duplicate/reverse original stock")
        approved = self._approve_real_pair_at_month_end(
            batch, module, after_month_end, next_day
        )
        batch.reload()
        self._assert_drafts_are_real_and_unposted(
            approved, stock, detail, batch
        )
        self.assertEqual(frappe.db.count(
            "Sales Invoice", {"remarks": marker}
        ), 1)
        self.assertEqual(frappe.db.count(
            "Purchase Invoice", {"remarks": marker}
        ), 1)

    # Post-approval acceptance: use the SAME signed-issue/real-ledger source
    # fixture exercised by Build #122. These additional cases deliberately
    # invoke ERPNext's real submit/cancel methods and MariaDB GL, not mocks.
    # Only the existing billing-controller month-end clock is simulated.
    # IMPORTANT: no automatic posting of invoices in the production service.
    # They remain Draft until a separately authorized accounting actor posts.
    def _draft_pair_for_posting(self):
        batch, stock, detail = self._issue_signed_source_for_real_pair()
        self._prepare_disposable_invoice_masters()
        module, clock, next_day = self._finalize_real_month_end(batch)
        result = self._approve_real_pair_at_month_end(
            batch, module, clock, next_day
        )
        batch.reload()
        sales, purchase = self._assert_drafts_are_real_and_unposted(
            result, stock, detail, batch
        )
        return batch, stock, sales, purchase

    def _invoice_gl(self, invoice, active_only=True):
        filters = {
            "voucher_type": invoice.doctype,
            "voucher_no": invoice.name,
        }
        if active_only:
            filters["is_cancelled"] = 0
        return frappe.get_all(
            "GL Entry", filters=filters,
            fields=["company", "account", "debit", "credit", "is_cancelled"],
        )

    def _assert_posted_invoice_gl(self, invoice, debit_account, credit_account):
        rows = self._invoice_gl(invoice)
        self.assertTrue(rows, invoice.doctype + " did not create actual GL")
        self.assertTrue(all(row.company == invoice.company for row in rows))
        debits = sum(flt(row.debit) for row in rows)
        credits = sum(flt(row.credit) for row in rows)
        self.assertAlmostEqual(debits, credits, places=2)
        self.assertAlmostEqual(debits, flt(invoice.grand_total), places=2)
        by_account = {}
        for row in rows:
            by_account[row.account] = by_account.get(row.account, 0) + (
                flt(row.debit) - flt(row.credit)
            )
        self.assertAlmostEqual(
            by_account.get(debit_account, 0), flt(invoice.grand_total),
            places=2,
        )
        self.assertAlmostEqual(
            by_account.get(credit_account, 0), -flt(invoice.grand_total),
            places=2,
        )

    def _submit_real_pair(self):
        batch, stock, sales, purchase = self._draft_pair_for_posting()
        original_sle = frappe.db.count(
            "Stock Ledger Entry",
            {"voucher_type": "Stock Entry", "voucher_no": stock.name},
        )
        original_gl = frappe.db.count(
            "GL Entry", {"voucher_type": "Stock Entry", "voucher_no": stock.name},
        )
        # ERPNext (not the custom billing service) performs actual posting.
        # Both invoices must post without touching original Material Issue.
        sales.submit()
        purchase.submit()
        sales.reload()
        purchase.reload()
        self.assertEqual((sales.docstatus, purchase.docstatus), (1, 1))
        self.assertEqual(sales.inter_company_invoice_reference, purchase.name)
        self.assertEqual(purchase.inter_company_invoice_reference, sales.name)
        self.assertFalse(sales.update_stock or purchase.update_stock)
        self.assertEqual(
            frappe.db.count(
                "Stock Ledger Entry",
                {"voucher_type": "Stock Entry", "voucher_no": stock.name},
            ),
            original_sle,
        )
        self.assertEqual(
            frappe.db.count(
                "GL Entry",
                {"voucher_type": "Stock Entry", "voucher_no": stock.name},
            ),
            original_gl,
        )
        self.assertEqual(frappe.get_doc("Stock Entry", stock.name).docstatus, 1)
        seller_abbr = frappe.db.get_value("Company", COMPANY, "abbr")
        buyer_abbr = frappe.db.get_value("Company", BUYER, "abbr")
        self._assert_posted_invoice_gl(
            sales,
            f"1312 - Intercompany Receivable - {seller_abbr}",
            f"6050 - Intercompany Material Charges - {seller_abbr}",
        )
        self._assert_posted_invoice_gl(
            purchase,
            f"5150 - Intercompany Materials Expense - {buyer_abbr}",
            f"2121 - Intercompany Payable - {buyer_abbr}",
        )
        return batch, stock, sales, purchase, original_sle, original_gl

    def test_e_real_submitted_pair_posts_company_gl(self):
        """Real ERPNext submit creates balanced, separate Company ledgers."""
        batch, stock, sales, purchase, _, _ = self._submit_real_pair()
        batch.reload()
        self.assertEqual(batch.status, "Approved")
        self.assertEqual(batch.sales_invoice, sales.name)
        self.assertEqual(batch.purchase_invoice, purchase.name)

    def test_f_real_invoice_cancellation_reverses_gl_not_source(self):
        """Cancel buyer then seller: no active invoice GL or source reversal."""
        batch, stock, sales, purchase, original_sle, original_gl = (
            self._submit_real_pair()
        )
        # Native ERPNext cancellation order must be valid without bypassing
        # controller validation; failure is a real release-review blocker.
        purchase.cancel()
        sales.cancel()
        purchase.reload()
        sales.reload()
        self.assertEqual((sales.docstatus, purchase.docstatus), (2, 2))
        for invoice in (sales, purchase):
            # ERPNext may cancel old GL rows or create reversing rows.
            # What matters is no surviving active account balance.
            by_account = {}
            for row in self._invoice_gl(invoice):
                by_account[row.account] = by_account.get(row.account, 0) + (
                    flt(row.debit) - flt(row.credit)
                )
            for net in by_account.values():
                self.assertAlmostEqual(net, 0, places=2)
        self.assertEqual(frappe.get_doc("Stock Entry", stock.name).docstatus, 1)
        self.assertEqual(
            frappe.db.count(
                "Stock Ledger Entry",
                {"voucher_type": "Stock Entry", "voucher_no": stock.name},
            ), original_sle,
        )
        self.assertEqual(
            frappe.db.count(
                "GL Entry",
                {"voucher_type": "Stock Entry", "voucher_no": stock.name},
            ), original_gl,
        )
        # Do not silently clear approved billing links or allow rebilling:
        # a separate Accounting policy is needed for cancelled paired invoices.
        batch.reload()
        self.assertEqual(batch.sales_invoice, sales.name)
        self.assertEqual(batch.purchase_invoice, purchase.name)

    def test_g_unprivileged_actor_cannot_submit_invoice(self):
        """Synthetic non-Accounts user cannot post either approved invoice."""
        batch, stock, sales, purchase = self._draft_pair_for_posting()
        self._actor(
            "ci-bridge-no-invoice-permission@invalid.test",
            "CCPC CI No Invoice Access",
        )
        frappe.set_user("ci-bridge-no-invoice-permission@invalid.test")
        try:
            for doctype, name in (
                ("Sales Invoice", sales.name),
                ("Purchase Invoice", purchase.name),
            ):
                doc = frappe.get_doc(doctype, name)
                self.assertFalse(
                    frappe.has_permission(doctype, ptype="submit", doc=doc),
                    doctype + " incorrectly allowed unprivileged submit",
                )
                with self.assertRaises(frappe.PermissionError):
                    doc.submit()
        finally:
            frappe.set_user("Administrator")
        sales.reload()
        purchase.reload()
        self.assertEqual((sales.docstatus, purchase.docstatus), (0, 0))
        for invoice in (sales, purchase):
            self.assertFalse(self._invoice_gl(invoice))
        self.assertEqual(frappe.get_doc("Stock Entry", stock.name).docstatus, 1)
        batch.reload()
        self.assertEqual(batch.status, "Approved")

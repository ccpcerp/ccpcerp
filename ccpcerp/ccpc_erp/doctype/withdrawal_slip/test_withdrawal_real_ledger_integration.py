# Copyright (c) 2026, CCPC and contributors
# See license.txt
"""REAL disposable-site integration gate, unlike the 72 unit/mock tests.

This test requires a newly created, isolated ERPNext v16 site with the CCPC app
installed and ERPNext bootstrap test fixtures. NEVER run it on CCPC's existing
DEVELOPMENT or PRODUCTION sites. It uses synthetic _Test Company data only.
"""

import multiprocessing
import os
from contextlib import closing
from queue import Empty

import frappe
from frappe.utils import flt

from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.tests.utils import ERPNextTestSuite

from ccpcerp.ccpc_erp.doctype.withdrawal_slip.ci_concurrent_issue_worker import (
    _ci_concurrent_issue_worker,
)
from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip
from ccpcerp.ccpc_erp.ci_disposable_site_guard import is_reviewed_withdrawal_disposable_site


COMPANY = "_Test Company"
WAREHOUSE = "_Test Warehouse - _TC"
DEPARTMENT = "CI Withdrawal Integration Department"
PREPARER = "test@example.com"
ISSUER = "ci-stockroom-integration@invalid.test"
QTY_RECEIVED = 5
QTY_ISSUED = 1
UNIT_RATE = 100



class TestWithdrawalRealLedgerIntegration(ERPNextTestSuite):
    """Real database/ledger assertion; fixture bypasses APPROVAL workflow only."""

    def setUp(self):
        # Two methods below deliberately commit/roll back SQL. Block them at
        # FIRST contact, before loading or changing *any* ERPNext test records,
        # unless this is the specifically approved disposable Actions site.
        if self._testMethodName.startswith(("test_w_", "test_x_", "test_y_", "test_z_", "test_zz_", "test_zzz_", "test_zzzz_")):
            site = getattr(frappe.local, "site", None)
            if not (
                is_reviewed_withdrawal_disposable_site(site)
                and os.environ.get("CCPC_ALLOW_COMMIT_PROBE") == "isolated-only"
                and (not self._testMethodName.startswith(("test_w_", "test_x_"))
                     or os.environ.get("CCPC_ALLOW_ACCOUNTING_PROBE") == "isolated-only")
                and (not self._testMethodName.startswith("test_zzz_")
                     or os.environ.get("CCPC_ALLOW_HTTP_PROBE") == "isolated-only")
                and (not self._testMethodName.startswith("test_zzzz_")
                     or os.environ.get("CCPC_ALLOW_CANCELLATION_PROBE") == "isolated-only")
            ):
                raise RuntimeError("Transaction probes require an approved isolated CI runner and test site")
        self._set_up_stock()
        self._set_up_review_slip()

    def _set_up_stock(self):
        # ERPNext's own test bootstrap records, not CCPC production business data.
        frappe.set_user("Administrator")
        self.load_test_records("Stock Entry")
        for doctype, name in (
            ("Company", COMPANY),
            ("Warehouse", WAREHOUSE),
            ("User", PREPARER),
        ):
            self.assertTrue(frappe.db.exists(doctype, name), f"Missing isolated fixture: {doctype} {name}")

        # ERPNext's Stock Entry test-record loader does not seed Department.
        # Create the leaf department within THIS disposable test transaction.
        # Some ERPNext nested-set setup writes may cross test boundaries.
        # The site is disposable, but repeated tests must still be idempotent.
        self.department = frappe.db.get_value(
            "Department", {"department_name": DEPARTMENT, "company": COMPANY}, "name"
        )
        if not self.department:
            self.department = frappe.get_doc(
                {
                    "doctype": "Department",
                    "department_name": DEPARTMENT,
                    "company": COMPANY,
                    "is_group": 0,
                }
            ).insert(ignore_permissions=True).name
        self.assertEqual(frappe.db.get_value("Department", self.department, "company"), COMPANY)
        self.assertTrue(frappe.db.exists("Role", "Stockroom Personnel"))
        # The standalone Withdrawal real-ledger suite uses simulated approved
        # mappings on the disposable CircleCI site. The separate genuine Finance
        # workflow suite inherits this setup but must start WITHOUT synthetic
        # approvals, so it can test authentic maker/checker decisions and denials.
        from ccpcerp.ccpc_erp.ci_disposable_site_guard import is_reviewed_circleci_disposable_site
        if (is_reviewed_circleci_disposable_site(getattr(frappe.local, "site", None))
                and not getattr(self, "_verify_genuine_finance_workflow", False)):
            from ccpcerp.ccpc_erp.ci_synthetic_finance_fixture import seed_circleci_test_only_mapping
            self.synthetic_finance = seed_circleci_test_only_mapping(
                COMPANY, "_Test Item Group", self.department
            )


        stock_hooks = frappe.get_hooks("doc_events").get("Stock Entry", {})
        for method in ("validate", "before_submit", "on_submit"):
            self.assertIn(
                "ccpcerp.ccpc_erp.withdrawal_slip_stock",
                str(stock_hooks.get(method, [])),
                f"Installed Stock Entry hook missing: {method}",
            )
        self.assertTrue(frappe.get_meta("Stock Entry").has_field("withdrawal_slip"))
        self.assertTrue(frappe.get_meta("Stock Entry Detail").has_field("withdrawal_slip_item"))

        # ERPNext's canonical _Test Company has perpetual inventory OFF.
        # Turn it on exclusively in the test transaction to exercise real GL.
        # Stock Entry's normal ERPNext validator requires a Difference
        # Account once perpetual inventory is enabled. The upstream _Test
        # Company fixture intentionally has it unset, so configure ONLY this
        # isolated site's synthetic chart of accounts; never bypass validation.
        difference_account = "Stock Adjustment - _TC"
        self.assertTrue(
            frappe.db.exists("Account", difference_account),
            "Disposable ERPNext fixture must provide a valid Stock Adjustment Account",
        )
        frappe.db.set_value(
            "Company",
            COMPANY,
            {
                "enable_perpetual_inventory": 1,
                "stock_adjustment_account": difference_account,
            },
        )
        frappe.clear_cache(doctype="Company")
        self.assertEqual(
            frappe.db.get_value("Company", COMPANY, "stock_adjustment_account"),
            difference_account,
        )
        # ERPNext v16's is_perpetual_inventory_enabled caches this separately
        # in frappe.local and does NOT reread it on clear_cache(doctype=...).
        from erpnext import is_perpetual_inventory_enabled

        if hasattr(frappe.local, "enable_perpetual_inventory"):
            frappe.local.enable_perpetual_inventory.pop(COMPANY, None)
        self.assertEqual(
            int(frappe.db.get_value("Company", COMPANY, "enable_perpetual_inventory")), 1
        )
        self.assertEqual(
            int(is_perpetual_inventory_enabled(COMPANY)),
            1,
            "ERPNext must actually treat this disposable company as perpetual-inventory",
        )

        if not frappe.db.exists("User", ISSUER):
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": ISSUER,
                    "first_name": "CI Stockroom Only",
                    "enabled": 1,
                    "user_type": "System User",
                    "send_welcome_email": 0,
                    "roles": [{"role": "Stockroom Personnel"}],
                }
            ).insert(ignore_permissions=True)
        self.assertIn("Stockroom Personnel", frappe.get_roles(ISSUER))

        # Never reuse an existing test item: inventory isolation by unique code.
        self.item = make_item(
            item_code="CI-WITHDRAWAL-" + frappe.generate_hash(length=12),
            properties={
                "is_stock_item": 1,
                "stock_uom": "_Test UOM",
                "item_group": "_Test Item Group",
                "opening_stock": 0,
                "valuation_rate": UNIT_RATE,
            },
        )
        self.receipt = make_stock_entry(
            item_code=self.item.name,
            qty=QTY_RECEIVED,
            company=COMPANY,
            to_warehouse=WAREHOUSE,
            rate=UNIT_RATE,
        )
        self.assertEqual(self.receipt.docstatus, 1)
        self.assertAlmostEqual(self._bin_qty(), QTY_RECEIVED, places=5)

    def _set_up_review_slip(self):
        # A valid synthetic request is inserted as its actual preparer.
        frappe.set_user(PREPARER)
        slip = frappe.get_doc(
            {
                "doctype": "Withdrawal Slip",
                "requestor": "CI Synthetic Requestor",
                "approving_supervisor": PREPARER,
                "required_by": frappe.utils.nowdate(),
                "source_warehouse": WAREHOUSE,
                "company": COMPANY,
                "requesting_company": COMPANY,
                "department": self.department,
                "work_for_department": self.department,  # Explicit synthetic beneficiary, not an assumed requester fallback.
                "physical_location": "Disposable CI test bay",
                "purpose": "Disposable independent inventory ledger test",
                "received_by": "CI Synthetic Receiver",
                "items": [
                    {
                        "item_code": self.item.name,
                        "requested_qty": QTY_ISSUED,
                        "issue_qty": QTY_ISSUED,
                        "uom": "_Test UOM",
                    }
                ],
            }
        )
        slip.insert(ignore_permissions=True)
        self.assertEqual(slip.owner, PREPARER)
        self.assertEqual(slip.approving_supervisor, PREPARER)
        self.assertEqual(slip.status, "Draft")

        # Test fixture only: exercising the Stockroom endpoint, NOT proving
        # approval/workflow transitions. Keep intent/provenance immutable.
        frappe.db.set_value("Withdrawal Slip", slip.name, "status", "Stockroom Review")
        self.slip_name = slip.name
        self.slip_item_name = slip.items[0].name

    def tearDown(self):
        frappe.set_user("Administrator")
        super().tearDown()  # Real MariaDB rollback on this disposable site.

    def _bin_qty(self):
        return flt(
            frappe.db.get_value(
                "Bin", {"item_code": self.item.name, "warehouse": WAREHOUSE}, "actual_qty"
            )
        )

    def test_w_native_explicit_department_reaches_gl(self):
        """Isolate native dimension propagation from CCPC's issue builder.

        A separate, unlinked synthetic Material Issue is explicitly tagged
        with the beneficiary Department on both header and item. This must
        post its expense debit to that Department on the real GL. The linked
        CCPC issue test remains separate and intentionally checks the actual
        builder's missing-source-dimension behavior.
        """
        from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
            get_accounting_dimensions,
        )

        self.assertIn("department", get_accounting_dimensions())
        for doctype in ("Stock Entry", "Stock Entry Detail", "GL Entry"):
            self.assertTrue(
                frappe.get_meta(doctype).has_field("department"),
                f"Disposable ERPNext {doctype} lacks the Department dimension",
            )

        frappe.set_user("Administrator")
        entry = frappe.new_doc("Stock Entry")
        entry.company = COMPANY
        entry.purpose = "Material Issue"
        entry.set_stock_entry_type()
        entry.from_warehouse = WAREHOUSE
        entry.department = self.department
        entry.append(
            "items",
            {
                "item_code": self.item.name,
                "qty": QTY_ISSUED,
                "uom": "_Test UOM",
                "conversion_factor": 1,
                "s_warehouse": WAREHOUSE,
                "department": self.department,
            },
        )
        entry.insert(ignore_permissions=True)
        entry.submit()

        self.assertEqual(entry.docstatus, 1)
        self.assertEqual(entry.department, self.department)
        self.assertEqual(entry.items[0].department, self.department)
        line = entry.items[0]
        self.assertTrue(line.expense_account)
        self.assertTrue(line.cost_center)
        gl = frappe.get_all(
            "GL Entry",
            filters={
                "voucher_type": "Stock Entry",
                "voucher_no": entry.name,
                "is_cancelled": 0,
            },
            fields=["account", "cost_center", "department", "debit", "credit"],
        )
        charges = [
            row for row in gl
            if row.account == line.expense_account and flt(row.debit) > 0
        ]
        self.assertTrue(charges, "Native Material Issue expense debit missing")
        self.assertAlmostEqual(
            sum(flt(r.debit) - flt(r.credit) for r in charges),
            QTY_ISSUED * UNIT_RATE,
            places=2,
        )
        for row in charges:
            self.assertEqual(row.department, self.department)
            self.assertEqual(row.cost_center, line.cost_center)
        print(
            "Native ERPNext dimension proof: explicit beneficiary Department "
            + self.department
            + " reached submitted expense GL for "
            + entry.name
        )

    def test_x_real_issue_expense_cost_center_and_beneficiary_trace(self):
        """Check real issue-side GL attribution; no physical return is posted.

        This is a disposable Actions-only accounting baseline. The target
        company may not configure Equipment/Department as GL dimensions; in
        that case the row/account GL checks still run, and missing dimensions
        are reported instead of incorrectly claiming that they passed.
        """
        frappe.set_user("Administrator")
        # Make requesting Department deliberately different from the actual
        # beneficiary. The original Slip and issue must preserve this
        # difference, rather than silently charge the requestor's department.
        request_department = frappe.get_doc(
            {
                "doctype": "Department",
                "department_name": "CI Requesting " + frappe.generate_hash(length=8),
                "company": COMPANY,
                "is_group": 0,
            }
        ).insert(ignore_permissions=True)
        equipment = frappe.get_doc(
            {
                "doctype": "Equipment Registry",
                "equipment_number": "CI-EQUIP-" + frappe.generate_hash(length=10),
                "equipment_description": "Disposable CI beneficiary equipment",
                "company": COMPANY,
                "department": self.department,
                "status": "Active",
            }
        ).insert(ignore_permissions=True)
        # Test fixture only: approved request workflow is covered by other
        # tests. Do not mutate ordinary client request intent after approval.
        frappe.db.set_value(
            "Withdrawal Slip",
            self.slip_name,
            {
                "department": request_department.name,
                "work_for_department": self.department,
                "equipment": equipment.name,
            },
            update_modified=False,
        )
        slip = frappe.get_doc("Withdrawal Slip", self.slip_name)
        self.assertNotEqual(slip.department, slip.work_for_department)
        self.assertEqual(slip.work_for_department, self.department)

        frappe.set_user(ISSUER)
        result = issue_withdrawal_slip(self.slip_name)
        self.assertEqual(result["submitted"], 1)
        entry = frappe.get_doc("Stock Entry", result["name"])
        self.assertEqual(entry.docstatus, 1)
        self.assertEqual(len(entry.items), 1)
        line = entry.items[0]
        self.assertEqual(line.withdrawal_slip_item, self.slip_item_name)
        self.assertTrue(line.expense_account, "Issue row has no posted expense account")
        self.assertTrue(line.cost_center, "Issue row has no Cost Center")

        # Diagnostic only, without changing ledger posting or skipping the
        # beneficiary check: distinguish an active accounting dimension from
        # a GL Entry column that merely exists in the test schema.
        from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
            get_accounting_dimensions,
        )

        active_dimensions = get_accounting_dimensions()
        print(
            "Accounting attribution diagnostic: active dimensions="
            + repr(active_dimensions)
            + "; Stock Entry header department="
            + repr(entry.get("department"))
            + "; row department="
            + repr(line.get("department"))
            + "; header equipment="
            + repr(entry.get("equipment"))
            + "; row equipment="
            + repr(line.get("equipment"))
            + "; row expense account="
            + repr(line.expense_account)
            + "; row cost center="
            + repr(line.cost_center)
        )

        dimension_fields = [
            field for field in ("equipment", "department")
            if frappe.get_meta("GL Entry").has_field(field)
        ]
        gl = frappe.get_all(
            "GL Entry",
            filters={
                "voucher_type": "Stock Entry",
                "voucher_no": entry.name,
                "is_cancelled": 0,
            },
            fields=["account", "cost_center", "debit", "credit"] + dimension_fields,
        )
        charge_rows = [
            row for row in gl
            if row.account == line.expense_account and flt(row.debit) > 0
        ]
        self.assertTrue(
            charge_rows,
            "Issued Material Issue has no debit in its recorded expense account",
        )
        self.assertAlmostEqual(
            sum(flt(row.debit) - flt(row.credit) for row in charge_rows),
            QTY_ISSUED * UNIT_RATE,
            places=2,
        )
        for row in charge_rows:
            self.assertEqual(
                row.cost_center, line.cost_center,
                "GL expense Cost Center differs from the submitted issue row",
            )
        if frappe.get_meta("Stock Entry").has_field("equipment"):
            self.assertEqual(entry.equipment, equipment.name)
        if frappe.get_meta("Stock Entry Detail").has_field("equipment"):
            self.assertEqual(line.equipment, equipment.name)
        if "equipment" in dimension_fields:
            for row in charge_rows:
                self.assertEqual(
                    row.equipment, equipment.name,
                    "Expense GL lost the actual beneficiary Equipment dimension",
                )
        else:
            print("Accounting baseline: Equipment GL dimension NOT configured in disposable site")
        if "department" in dimension_fields:
            for row in charge_rows:
                self.assertEqual(
                    row.department, self.department,
                    "Expense GL department differs from the beneficiary Department",
                )
        else:
            print("Accounting baseline: Department GL dimension NOT configured in disposable site")
        print(
            "Accounting baseline: issued expense account="
            + line.expense_account + "; cost center=" + line.cost_center
            + "; requesting department=" + request_department.name
            + "; beneficiary department=" + self.department
        )

    def test_x_missing_beneficiary_blocks_issue_without_requester_fallback(self):
        """Missing approved beneficiary must not charge the requesting Department."""
        frappe.set_user("Administrator")
        frappe.db.set_value("Withdrawal Slip", self.slip_name, {
            "work_for_department": None, "equipment": None,
        }, update_modified=False)
        frappe.set_user(ISSUER)
        with self.assertRaisesRegex(frappe.ValidationError, "[Dd]epartment"):
            issue_withdrawal_slip(self.slip_name)
        self.assertEqual(
            frappe.db.count("Stock Entry", {"withdrawal_slip": self.slip_name, "docstatus": 1}), 0
        )

    def test_x_equipment_department_fallback_reaches_actual_gl(self):
        """Use registered Equipment's Company Department, never requester, when work-for absent."""
        frappe.set_user("Administrator")
        requester = frappe.get_doc({
            "doctype": "Department",
            "department_name": "CI Different Requester " + frappe.generate_hash(length=8),
            "company": COMPANY, "is_group": 0,
        }).insert(ignore_permissions=True)
        equip = frappe.get_doc({
            "doctype": "Equipment Registry",
            "equipment_number": "CI-ACCOUNT-FALLBACK-" + frappe.generate_hash(length=9),
            "equipment_description": "Synthetic Equipment beneficiary fallback",
            "company": COMPANY, "department": self.department, "status": "Active",
        }).insert(ignore_permissions=True)
        frappe.db.set_value("Withdrawal Slip", self.slip_name, {
            "department": requester.name,
            "work_for_department": None,
            "equipment": equip.name,
        }, update_modified=False)
        frappe.set_user(ISSUER)
        issued = issue_withdrawal_slip(self.slip_name)
        entry = frappe.get_doc("Stock Entry", issued["name"])
        self.assertEqual(entry.department, self.department)
        self.assertEqual(entry.items[0].department, self.department)
        self.assertNotEqual(entry.department, requester.name)
        row = entry.items[0]
        gl = frappe.get_all("GL Entry", filters={
            "voucher_type": "Stock Entry", "voucher_no": entry.name, "is_cancelled": 0,
        }, fields=["account", "debit", "department"])
        expense = [g for g in gl if g.account == row.expense_account and flt(g.debit) > 0]
        self.assertTrue(expense, "Fallback issue expense debit missing")
        self.assertTrue(all(g.department == self.department for g in expense))

    def test_x_equipment_beneficiary_conflict_blocks_before_stock_gl(self):
        """Never silently override explicit beneficiary from another Equipment Department."""
        frappe.set_user("Administrator")
        other = frappe.get_doc({
            "doctype": "Department",
            "department_name": "CI Other Equip Department " + frappe.generate_hash(length=8),
            "company": COMPANY, "is_group": 0,
        }).insert(ignore_permissions=True)
        equip = frappe.get_doc({
            "doctype": "Equipment Registry",
            "equipment_number": "CI-ACCOUNT-CONFLICT-" + frappe.generate_hash(length=9),
            "equipment_description": "Synthetic conflict validation",
            "company": COMPANY, "department": other.name, "status": "Active",
        }).insert(ignore_permissions=True)
        frappe.db.set_value("Withdrawal Slip", self.slip_name, {
            "equipment": equip.name, "work_for_department": self.department,
        }, update_modified=False)
        frappe.set_user(ISSUER)
        with self.assertRaisesRegex(frappe.ValidationError, "[Dd]epartment"):
            issue_withdrawal_slip(self.slip_name)
        self.assertEqual(
            frappe.db.count("Stock Entry", {"withdrawal_slip": self.slip_name, "docstatus": 1}), 0
        )

    def test_x_forged_direct_voucher_department_rejected_before_ledger(self):
        """Both beneficiary validation and saved-row immutability reject forged Department.

        On a saved Stock Entry the immutable provenance guard runs first during
        submit, so its error is expected instead of the later beneficiary error.
        Assert beneficiary validation directly too: these are distinct defenses.
        """
        from ccpcerp.ccpc_erp.withdrawal_slip_stock import _build_material_issue
        from ccpcerp.ccpc_erp.withdrawal_issue_beneficiary import validate_issue_beneficiary

        frappe.set_user(ISSUER)
        slip = frappe.get_doc("Withdrawal Slip", self.slip_name)
        doc = _build_material_issue(slip)
        self.assertEqual(doc.department, self.department)
        self.assertEqual(doc.items[0].department, self.department)
        doc.insert(ignore_permissions=True)
        doc.items[0].department = None
        doc.flags.ignore_permissions = True

        # Independent beneficiary attribution guard remains active for a
        # forged detail Department, even before Stock Entry.submit is called.
        with self.assertRaisesRegex(frappe.ValidationError, "[Dd]epartment"):
            validate_issue_beneficiary(doc, slip)

        # Real submit rejects the saved-row tamper at an earlier guard.
        # Do not bypass or disable provenance checks to reach later validation.
        with self.assertRaisesRegex(
            frappe.ValidationError, "original issue-time Equipment or financial mapping provenance cannot be modified"
        ):
            doc.submit()
        self.assertEqual(
            frappe.db.count("Stock Ledger Entry", {"voucher_type": "Stock Entry", "voucher_no": doc.name}), 0
        )
        self.assertEqual(
            frappe.db.count("GL Entry", {"voucher_type": "Stock Entry", "voucher_no": doc.name}), 0
        )

    def test_real_material_issue_updates_bin_sle_gl_and_blocks_second_issue(self):
        # Stockroom-only peer identity must not acquire broad Stock Entry rights.
        frappe.set_user(ISSUER)
        self.assertFalse(frappe.has_permission("Stock Entry", "create"))
        result = issue_withdrawal_slip(self.slip_name)

        self.assertEqual(result["submitted"], 1)
        entry = frappe.get_doc("Stock Entry", result["name"])
        self.assertEqual(entry.docstatus, 1)
        self.assertEqual(entry.purpose, "Material Issue")
        self.assertEqual(entry.company, COMPANY)
        self.assertEqual(entry.withdrawal_slip, self.slip_name)
        self.assertEqual(len(entry.items), 1)
        self.assertEqual(entry.items[0].withdrawal_slip_item, self.slip_item_name)
        self.assertAlmostEqual(flt(entry.items[0].transfer_qty), QTY_ISSUED, places=5)

        # These are actual ERPNext database rows, not unittest.mock results.
        sle = frappe.get_all(
            "Stock Ledger Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": entry.name},
            fields=["item_code", "warehouse", "actual_qty", "stock_value_difference", "is_cancelled"],
        )
        self.assertEqual(len(sle), 1)
        self.assertEqual(sle[0].item_code, self.item.name)
        self.assertEqual(sle[0].warehouse, WAREHOUSE)
        self.assertAlmostEqual(flt(sle[0].actual_qty), -QTY_ISSUED, places=5)
        self.assertLess(flt(sle[0].stock_value_difference), 0)
        self.assertAlmostEqual(self._bin_qty(), QTY_RECEIVED - QTY_ISSUED, places=5)

        gl = frappe.get_all(
            "GL Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": entry.name, "is_cancelled": 0},
            fields=["account", "debit", "credit", "company"],
        )
        self.assertGreaterEqual(len(gl), 2, "Expected real perpetual-inventory GL rows")
        self.assertTrue(all(row.company == COMPANY for row in gl))
        debits = sum(flt(row.debit) for row in gl)
        credits = sum(flt(row.credit) for row in gl)
        self.assertGreater(debits, 0)
        self.assertAlmostEqual(debits, credits, places=2)
        self.assertAlmostEqual(
            credits,
            -flt(sle[0].stock_value_difference),
            places=2,
            msg="Material Issue GL value must reconcile to SLE inventory decrease",
        )

        slip = frappe.get_doc("Withdrawal Slip", self.slip_name)
        self.assertEqual(slip.status, "Fully Issued")
        self.assertAlmostEqual(flt(slip.items[0].issued_qty), QTY_ISSUED, places=5)

        # Sequential retry must not create a second Stock Entry or ledger entry.
        with self.assertRaises(frappe.ValidationError):
            issue_withdrawal_slip(self.slip_name)
        self.assertEqual(
            frappe.db.count("Stock Entry", {"withdrawal_slip": self.slip_name, "docstatus": 1}),
            1,
        )
        self.assertAlmostEqual(self._bin_qty(), QTY_RECEIVED - QTY_ISSUED, places=5)

    def test_real_non_stockroom_user_cannot_post(self):
        frappe.set_user(PREPARER)
        with self.assertRaises(frappe.PermissionError):
            issue_withdrawal_slip(self.slip_name)
        self.assertEqual(frappe.db.count("Stock Entry", {"withdrawal_slip": self.slip_name}), 0)
        self.assertAlmostEqual(self._bin_qty(), QTY_RECEIVED, places=5)

    def _independent_sql_snapshot(self, entry_name):
        """Read through an ACTUAL second MariaDB connection, never frappe.db.

        The site credentials are obtained only from this fresh CI test site;
        this helper performs SELECT queries only and never prints credentials.
        A new autocommit connection avoids a stale REPEATABLE READ snapshot.
        """
        import pymysql

        with closing(
            pymysql.connect(
                host=frappe.conf.get("db_host") or "127.0.0.1",
                port=int(frappe.conf.get("db_port") or 3306),
                user=frappe.conf.db_name,
                password=frappe.conf.db_password,
                database=frappe.conf.db_name,
                autocommit=True,
                connect_timeout=5,
                read_timeout=5,
            )
        ) as other:
            with other.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM `tabStock Entry` WHERE name=%s AND docstatus=1",
                    (entry_name,),
                )
                entry_count = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT COUNT(*), COALESCE(SUM(actual_qty),0), "
                    "COALESCE(SUM(stock_value_difference),0) "
                    "FROM `tabStock Ledger Entry` "
                    "WHERE voucher_type='Stock Entry' AND voucher_no=%s AND is_cancelled=0",
                    (entry_name,),
                )
                sle_count, sle_qty, sle_value = cursor.fetchone()
                cursor.execute(
                    "SELECT COUNT(*), COALESCE(SUM(debit),0), COALESCE(SUM(credit),0) "
                    "FROM `tabGL Entry` "
                    "WHERE voucher_type='Stock Entry' AND voucher_no=%s AND is_cancelled=0",
                    (entry_name,),
                )
                gl_count, debits, credits = cursor.fetchone()
                cursor.execute(
                    "SELECT actual_qty, stock_value FROM `tabBin` WHERE item_code=%s AND warehouse=%s",
                    (self.item.name, WAREHOUSE),
                )
                bin_row = cursor.fetchone()
        return {
            "entry_count": entry_count,
            "sle_count": sle_count,
            "sle_qty": flt(sle_qty),
            "sle_value": flt(sle_value),
            "gl_count": gl_count,
            "debits": flt(debits),
            "credits": flt(credits),
            "bin_qty": flt(bin_row[0]) if bin_row else 0,
            "bin_value": flt(bin_row[1]) if bin_row else 0,
        }

    def test_y_rollback_hides_and_discards_real_issue_across_sessions(self):
        # The issued Stock Entry and SLE/GL exist on the issuing connection,
        # but none may become externally visible before a commit.
        frappe.set_user(ISSUER)
        name = issue_withdrawal_slip(self.slip_name)["name"]
        self.assertTrue(frappe.db.exists("Stock Entry", name))
        self.assertGreater(
            frappe.db.count("GL Entry", {"voucher_type": "Stock Entry", "voucher_no": name}),
            0,
        )
        before = self._independent_sql_snapshot(name)
        self.assertEqual(before["entry_count"], 0)
        self.assertEqual(before["sle_count"], 0)
        self.assertEqual(before["gl_count"], 0)
        self.assertEqual(before["bin_qty"], 0)  # Receipt also remains uncommitted.

        # This rollback occurs solely inside a disposable CI database.
        frappe.db.rollback()
        self.assertFalse(frappe.db.exists("Stock Entry", name))
        self.assertEqual(
            frappe.db.count("Stock Ledger Entry", {"voucher_type": "Stock Entry", "voucher_no": name}),
            0,
        )
        self.assertEqual(
            frappe.db.count("GL Entry", {"voucher_type": "Stock Entry", "voucher_no": name}),
            0,
        )
        after = self._independent_sql_snapshot(name)
        self.assertEqual(after["entry_count"], 0)
        self.assertEqual(after["sle_count"], 0)
        self.assertEqual(after["gl_count"], 0)
        self.assertEqual(after["bin_qty"], 0)

    def test_z_commit_makes_real_stock_and_gl_visible_across_sessions(self):
        # Commit is forbidden except the gated fresh Actions site above.
        # This verifies genuine multi-connection SQL durability/visibility,
        # not HTTP auth or simultaneous duplicate-prevention.
        frappe.set_user(ISSUER)
        name = issue_withdrawal_slip(self.slip_name)["name"]
        hidden = self._independent_sql_snapshot(name)
        self.assertEqual(hidden["entry_count"], 0)
        self.assertEqual(hidden["sle_count"], 0)
        self.assertEqual(hidden["gl_count"], 0)
        self.assertEqual(hidden["bin_qty"], 0)

        # This intentionally persists only unique synthetic CI fixtures on an
        # ephemeral runner which is torn down after the job. No shared site.
        frappe.db.commit()
        visible = self._independent_sql_snapshot(name)
        self.assertEqual(visible["entry_count"], 1)
        self.assertEqual(visible["sle_count"], 1)
        self.assertAlmostEqual(visible["sle_qty"], -QTY_ISSUED, places=5)
        self.assertAlmostEqual(visible["sle_value"], -(QTY_ISSUED * UNIT_RATE), places=2)
        self.assertGreaterEqual(visible["gl_count"], 2)
        self.assertGreater(visible["debits"], 0)
        self.assertAlmostEqual(visible["debits"], visible["credits"], places=2)
        self.assertAlmostEqual(visible["credits"], -visible["sle_value"], places=2)
        self.assertAlmostEqual(visible["bin_qty"], QTY_RECEIVED - QTY_ISSUED, places=5)


    def test_zz_two_independent_issuers_only_one_ledger_submission(self):
        """Force two independent Frappe processes against one held parent row lock.

        The parent first commits isolated synthetic fixture data and then holds
        SELECT ... FOR UPDATE on the Slip row. The children each report that
        they reached the ORIGINAL production lock method before release.
        After releasing, only one must commit the linked Stock Entry/SLE/GL.
        """
        # setUp() rejects non-CI or wrong-branch invocation BEFORE fixture work.
        if os.environ.get("CCPC_ALLOW_CONCURRENCY_PROBE") != "isolated-only":
            raise RuntimeError("Explicit isolated concurrent-issue CI flag required")
        frappe.set_user("Administrator")
        frappe.db.commit()  # Unique fake _Test Company fixture, CI site only.
        site = frappe.local.site
        sites_path = os.path.abspath(frappe.local.sites_path)

        # The coordinator holds the parent row, so neither worker can
        # finish its SELECT ... FOR UPDATE before BOTH reach the lock path.
        locked = frappe.db.sql(
            "SELECT name FROM `tabWithdrawal Slip` WHERE name=%s FOR UPDATE",
            (self.slip_name,),
        )
        self.assertEqual(locked[0][0], self.slip_name)

        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        children = [
            context.Process(
                target=_ci_concurrent_issue_worker,
                args=(i, site, sites_path, self.slip_name, ISSUER, queue),
                daemon=False,
            )
            for i in (1, 2)
        ]
        released = False
        results = []
        try:
            for child in children:
                child.start()

            attempts = {}
            while len(attempts) != 2:
                try:
                    message = queue.get(timeout=35)
                except Empty as exc:
                    raise AssertionError("Two issuers did not both reach the row-lock path") from exc
                if message[0] != "attempt":
                    raise AssertionError("Issuer exited before reaching the lock: " + repr(message))
                self.assertNotIn(message[1], attempts)
                attempts[message[1]] = message[2]
            self.assertEqual(set(attempts), {1, 2})
            self.assertEqual(len(set(attempts.values())), 2)
            coordinator_conn_id = int(frappe.db.sql("SELECT CONNECTION_ID()")[0][0])
            self.assertNotIn(coordinator_conn_id, attempts.values())

            # Fail if the lock was bypassed: no issuer may complete while
            # the coordinator still holds the parent Slip row for update.
            for child in children:
                self.assertTrue(child.is_alive(), "Issuer exited while Slip row was locked")
            self.assertEqual(
                frappe.db.count(
                    "Stock Entry",
                    {"withdrawal_slip": self.slip_name, "docstatus": 1},
                ),
                0,
            )

            # Coordinator does no writes after its fixture commit, so a
            # full rollback simply RELEASES its SELECT FOR UPDATE lock.
            frappe.db.rollback()
            released = True
            while len(results) != 2:
                try:
                    message = queue.get(timeout=35)
                except Empty as exc:
                    raise AssertionError("Concurrent workers failed to finish in time") from exc
                self.assertIn(message[0], {"success", "rejected", "error"})
                results.append(message)

            for child in children:
                child.join(timeout=10)
                self.assertEqual(child.exitcode, 0)
        finally:
            if not released:
                frappe.db.rollback()
            for child in children:
                if child.is_alive():
                    child.terminate()  # Own ephemeral CI worker only.
                    child.join(timeout=5)
            queue.close()
            queue.join_thread()

        successes = [x for x in results if x[0] == "success"]
        rejected = [x for x in results if x[0] == "rejected"]
        errors = [x for x in results if x[0] == "error"]
        self.assertFalse(errors, "A worker failed outside expected validation: " + repr(errors))
        self.assertEqual(len(successes), 1, repr(results))
        self.assertEqual(len(rejected), 1, repr(results))
        entry_name = successes[0][2]
        self.assertEqual(
            frappe.db.count("Stock Entry", {"withdrawal_slip": self.slip_name, "docstatus": 1}),
            1,
        )
        self.assertEqual(
            frappe.db.count("Stock Entry", {"withdrawal_slip": self.slip_name}),
            1,
        )
        verified = self._independent_sql_snapshot(entry_name)
        self.assertEqual(verified["entry_count"], 1)
        self.assertEqual(verified["sle_count"], 1)
        self.assertAlmostEqual(verified["sle_qty"], -QTY_ISSUED, places=5)
        self.assertAlmostEqual(verified["sle_value"], -(QTY_ISSUED * UNIT_RATE), places=2)
        self.assertAlmostEqual(verified["bin_qty"], QTY_RECEIVED - QTY_ISSUED, places=5)
        self.assertGreaterEqual(verified["gl_count"], 2)
        self.assertAlmostEqual(verified["debits"], verified["credits"], places=2)
        self.assertAlmostEqual(verified["credits"], -verified["sle_value"], places=2)
        slip = frappe.get_doc("Withdrawal Slip", self.slip_name)
        self.assertEqual(slip.status, "Fully Issued")
        self.assertAlmostEqual(flt(slip.items[0].issued_qty), QTY_ISSUED, places=5)


    def test_zzz_real_wsgi_method_session_role_and_csrf_enforcement(self):
        """Exercise the actual Frappe WSGI HTTP route, not direct Python dispatch.

        Construct real Frappe login-manager sessions for synthetic test users.
        The session cookie, CSRF token, request method, and authorization are
        then checked by the genuine Frappe request pipeline. No auth mocks.
        """
        if os.environ.get("CCPC_ALLOW_HTTP_PROBE") != "isolated-only":
            raise RuntimeError("Explicit disposable WSGI HTTP CI probe flag required")

        import frappe.app
        from frappe.auth import CookieManager, LoginManager
        from frappe.sessions import get_csrf_token
        from frappe.utils import get_test_client, set_request

        site = frappe.local.site
        sites_path = os.path.abspath(frappe.local.sites_path)
        self.assertTrue(is_reviewed_withdrawal_disposable_site(site))
        self.assertFalse(frappe.conf.get("ignore_csrf"))
        path = "/api/method/ccpcerp.ccpc_erp.withdrawal_slip_stock.issue_withdrawal_slip"
        base_url = "http://" + site
        headers = {"X-Frappe-Site-Name": site}
        slip_name = self.slip_name
        item_name = self.item.name

        # Site and test-item fixtures are committed ONLY on this ephemeral site
        # so the WSGI HTTP requests can see a legitimate Slip via fresh SQL.
        frappe.db.commit()

        old_sites_path = frappe.app._sites_path
        frappe.app._sites_path = sites_path

        def init_test_context():
            frappe.init(site, sites_path=sites_path, force=True)
            frappe.connect()
            frappe.set_user("Administrator")

        def create_cookie_session(user):
            # This is a real Frappe LoginManager session but avoids putting
            # synthetic plaintext passwords into logs or API request payloads.
            init_test_context()
            set_request(path="/", method="GET", base_url=base_url)
            frappe.local.cookie_manager = CookieManager()
            frappe.local.login_manager = LoginManager()
            frappe.local.login_manager.login_as(user)
            sid = frappe.session.sid
            token = get_csrf_token()
            # Frappe's test mode doesn't automatically persist newly generated
            # CSRF tokens; deliberately persist this disposable session.
            frappe.local.session_obj.update(force=True)
            frappe.db.commit()
            self.assertTrue(sid and sid != "Guest")
            self.assertTrue(token)
            return sid, token

        def make_client(sid=None):
            client = get_test_client()
            if sid:
                client.set_cookie("sid", sid, domain=site)
            return client

        def call(client, method, token=None):
            request_headers = dict(headers)
            if token is not None:
                request_headers["X-Frappe-CSRF-Token"] = token
            return getattr(client, method)(
                path,
                json={"withdrawal_slip": slip_name} if method == "post" else None,
                base_url=base_url,
                headers=request_headers,
            )

        try:
            # A guest is not a stockroom user, whether GET or POST.
            guest = make_client()
            guest_response = call(guest, "post")
            self.assertNotEqual(guest_response.status_code, 200)

            # An authenticated preparer must not issue this Slip, even with
            # a real session and a valid request CSRF token.
            preparer_sid, preparer_token = create_cookie_session(PREPARER)
            preparer = make_client(preparer_sid)
            denied = call(preparer, "post", preparer_token)
            self.assertNotEqual(denied.status_code, 200)

            # A real Stockroom issuer session still cannot use GET or bypass
            # the configured CSRF policy with missing/incorrect tokens.
            issuer_sid, issuer_token = create_cookie_session(ISSUER)
            issuer = make_client(issuer_sid)
            get_denied = call(issuer, "get")
            self.assertEqual(get_denied.status_code, 403)
            self.assertEqual((get_denied.get_json() or {}).get("exc_type"), "PermissionError")
            missing_csrf = call(issuer, "post")
            self.assertEqual(missing_csrf.status_code, 400)
            self.assertEqual(
                (missing_csrf.get_json() or {}).get("exc_type"),
                "CSRFTokenError",
                "Missing CSRF must fail specifically in Frappe's request-security layer",
            )
            invalid_csrf = call(issuer, "post", "incorrect-ci-token")
            self.assertEqual(invalid_csrf.status_code, 400)
            self.assertEqual(
                (invalid_csrf.get_json() or {}).get("exc_type"),
                "CSRFTokenError",
                "Wrong CSRF must fail specifically in Frappe's request-security layer",
            )

            # Correct method + genuine session + valid CSRF token should
            # execute Stock Entry submission via actual Frappe request/commit.
            accepted = call(issuer, "post", issuer_token)
            self.assertEqual(accepted.status_code, 200, accepted.get_data(as_text=True)[:700])
            message = (accepted.get_json() or {}).get("message") or {}
            self.assertEqual(message.get("submitted"), 1)
            entry_name = message.get("name")
            self.assertTrue(entry_name)

            repeated = call(issuer, "post", issuer_token)
            self.assertNotEqual(repeated.status_code, 200)
        finally:
            # Frappe's WSGI ClosingIterator destroys its request-local state.
            # Restore a CLI site context for ERPNextTestSuite.tearDown().
            frappe.app._sites_path = old_sites_path
            init_test_context()

        self.assertEqual(
            frappe.db.count("Stock Entry", {"withdrawal_slip": slip_name, "docstatus": 1}),
            1,
        )
        verified = self._independent_sql_snapshot(entry_name)
        self.assertEqual(verified["entry_count"], 1)
        self.assertEqual(verified["sle_count"], 1)
        self.assertAlmostEqual(verified["sle_qty"], -QTY_ISSUED, places=5)
        self.assertAlmostEqual(verified["sle_value"], -(QTY_ISSUED * UNIT_RATE), places=2)
        self.assertAlmostEqual(verified["bin_qty"], QTY_RECEIVED - QTY_ISSUED, places=5)
        self.assertGreaterEqual(verified["gl_count"], 2)
        self.assertAlmostEqual(verified["debits"], verified["credits"], places=2)
        self.assertAlmostEqual(verified["credits"], -verified["sle_value"], places=2)
        self.assertEqual(frappe.db.get_value("Withdrawal Slip", slip_name, "status"), "Fully Issued")


    def test_zzzz_real_cancel_reverses_stock_gl_and_restores_slip(self):
        """Real ERPNext cancellation must reverse stock and GL as one unit.

        This deliberately COMMITs only a uniquely generated synthetic CI
        fixture, its Material Issue, and its cancellation, to validate
        independent connection durability after the reversal. No shared sites.
        """
        if os.environ.get("CCPC_ALLOW_CANCELLATION_PROBE") != "isolated-only":
            raise RuntimeError("Explicit disposable cancellation CI probe flag required")

        initial_stock_value = flt(
            frappe.db.get_value("Bin", {"item_code": self.item.name, "warehouse": WAREHOUSE}, "stock_value")
        )
        self.assertAlmostEqual(initial_stock_value, QTY_RECEIVED * UNIT_RATE, places=2)
        frappe.set_user(ISSUER)
        posted = issue_withdrawal_slip(self.slip_name)
        entry_name = posted["name"]
        self.assertEqual(posted["submitted"], 1)
        self.assertAlmostEqual(self._bin_qty(), QTY_RECEIVED - QTY_ISSUED, places=5)
        self.assertEqual(frappe.db.get_value("Withdrawal Slip", self.slip_name, "status"), "Fully Issued")
        frappe.db.commit()  # Only this fresh GitHub-hosted synthetic CI site.

        issued = self._independent_sql_snapshot(entry_name)
        self.assertEqual(issued["entry_count"], 1)
        self.assertEqual(issued["sle_count"], 1)
        self.assertAlmostEqual(issued["sle_qty"], -QTY_ISSUED, places=5)
        self.assertAlmostEqual(issued["sle_value"], -(QTY_ISSUED * UNIT_RATE), places=2)
        self.assertAlmostEqual(issued["bin_qty"], QTY_RECEIVED - QTY_ISSUED, places=5)
        self.assertAlmostEqual(issued["bin_value"], initial_stock_value + issued["sle_value"], places=2)
        self.assertGreaterEqual(issued["gl_count"], 2)
        self.assertAlmostEqual(issued["debits"], issued["credits"], places=2)
        self.assertAlmostEqual(issued["credits"], -issued["sle_value"], places=2)

        # Actual Stock Entry.cancel(), not a mocked ledger or a direct
        # docstatus mutation. ERPNext's on_cancel and CCPC's installed hook
        # must both execute in the same MariaDB transaction.
        frappe.set_user("Administrator")
        entry = frappe.get_doc("Stock Entry", entry_name)
        entry.cancel()
        self.assertEqual(entry.docstatus, 2)
        self.assertAlmostEqual(self._bin_qty(), QTY_RECEIVED, places=5)
        restored_stock_value = flt(
            frappe.db.get_value("Bin", {"item_code": self.item.name, "warehouse": WAREHOUSE}, "stock_value")
        )
        self.assertAlmostEqual(restored_stock_value, initial_stock_value, places=2)

        slip = frappe.get_doc("Withdrawal Slip", self.slip_name)
        self.assertEqual(slip.status, "Stockroom Review")
        self.assertAlmostEqual(flt(slip.items[0].issued_qty), 0, places=5)
        self.assertAlmostEqual(flt(slip.items[0].shortage_qty), 0, places=5)
        self.assertAlmostEqual(flt(slip.items[0].net_used_qty), 0, places=5)
        self.assertFalse(slip.issued_by)
        self.assertFalse(slip.issued_on)

        # Cancellation should retain an auditable original SLE plus a
        # reversing SLE, whose per-voucher net qty/value are zero.
        sle = frappe.get_all(
            "Stock Ledger Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": entry_name},
            fields=["actual_qty", "stock_value_difference", "is_cancelled"],
        )
        self.assertGreaterEqual(len(sle), 2, "Missing auditable stock reversal")
        self.assertTrue(any(flt(row.actual_qty) < 0 for row in sle))
        self.assertTrue(any(flt(row.actual_qty) > 0 for row in sle))
        self.assertAlmostEqual(sum(flt(row.actual_qty) for row in sle), 0, places=5)
        # In Frappe/ERPNext v16 cancelled historical SLE preserve their
        # original stock_value_difference; reversal SLE can hold zero despite
        # restoring the actual current Bin valuation. The active Bin.stock_value
        # and per-account GL reversals are the accounting invariants, NOT a
        # simple sum of cancelled historical SLE valuation fields.
        self.assertTrue(all(int(row.is_cancelled) == 1 for row in sle))

        # For immutable and legacy GL policies, debit-minus-credit summed
        # per account must be zero across the issue plus cancellation.
        gl = frappe.get_all(
            "GL Entry",
            filters={"voucher_type": "Stock Entry", "voucher_no": entry_name},
            fields=["account", "debit", "credit", "is_cancelled"],
        )
        self.assertGreaterEqual(len(gl), 4, "Missing auditable GL reversal")
        by_account = {}
        for row in gl:
            by_account[row.account] = by_account.get(row.account, 0.0) + flt(row.debit) - flt(row.credit)
        self.assertGreaterEqual(len(by_account), 2)
        for account, balance in by_account.items():
            self.assertAlmostEqual(balance, 0, places=2, msg=f"Unreversed GL account: {account}")

        frappe.db.commit()  # Only disposable synthetic CI records.
        final = self._independent_sql_snapshot(entry_name)
        self.assertEqual(final["entry_count"], 0)
        self.assertEqual(final["sle_count"], 0)
        self.assertEqual(final["gl_count"], 0)
        self.assertAlmostEqual(final["bin_qty"], QTY_RECEIVED, places=5)
        self.assertAlmostEqual(final["bin_value"], initial_stock_value, places=2)
        self.assertEqual(frappe.db.get_value("Stock Entry", entry_name, "docstatus"), 2)
        self.assertEqual(frappe.db.get_value("Withdrawal Slip", self.slip_name, "status"), "Stockroom Review")

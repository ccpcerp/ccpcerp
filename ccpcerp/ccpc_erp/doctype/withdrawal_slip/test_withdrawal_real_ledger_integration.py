# Copyright (c) 2026, CCPC and contributors
# See license.txt
"""REAL disposable-site integration gate, unlike the 72 unit/mock tests.

This test requires a newly created, isolated ERPNext v16 site with the CCPC app
installed and ERPNext bootstrap test fixtures. NEVER run it on CCPC's existing
DEVELOPMENT or PRODUCTION sites. It uses synthetic _Test Company data only.
"""

import frappe
from frappe.utils import flt

from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.tests.utils import ERPNextTestSuite

from ccpcerp.ccpc_erp.withdrawal_slip_stock import issue_withdrawal_slip


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

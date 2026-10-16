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
        if self._testMethodName.startswith(("test_y_", "test_z_", "test_zz_", "test_zzz_")):
            if not (
                os.environ.get("GITHUB_ACTIONS") == "true"
                and os.environ.get("GITHUB_REPOSITORY") == "ccpcerp/ccpcerp"
                and os.environ.get("GITHUB_HEAD_REF")
                == "review/withdrawal-http-csrf-ci-2026-10-16"
                and os.environ.get("CCPC_ALLOW_COMMIT_PROBE") == "isolated-only"
                and getattr(frappe.local, "site", None) == "ci-withdrawal.localhost"
            ):
                raise RuntimeError(
                    "Transaction boundary probes require the explicitly approved "
                    "GitHub-hosted disposable ci-withdrawal.localhost only"
                )
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
                    "SELECT actual_qty FROM `tabBin` WHERE item_code=%s AND warehouse=%s",
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
        self.assertEqual(site, "ci-withdrawal.localhost")
        self.assertFalse(frappe.conf.get("ignore_csrf"))
        path = "/api/method/ccpcerp.ccpc_erp.withdrawal_slip_stock.issue_withdrawal_slip"
        base_url = "http://ci-withdrawal.localhost"
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
            self.assertNotEqual(get_denied.status_code, 200)
            missing_csrf = call(issuer, "post")
            self.assertNotEqual(missing_csrf.status_code, 200)
            invalid_csrf = call(issuer, "post", "incorrect-ci-token")
            self.assertNotEqual(invalid_csrf.status_code, 200)

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

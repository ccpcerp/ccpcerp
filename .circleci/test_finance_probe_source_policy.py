"""Offline-only source-policy checks for isolated Finance HTTP and SQL probes.

No Frappe import, database, network, credentials, subprocess or CircleCI job.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTTP = ROOT / ".circleci" / "finance_http_session_probe.py"
LOCK = ROOT / "ccpcerp" / "ccpc_erp" / "ci_finance_company_lock_probe.py"
RACE = ROOT / "ccpcerp" / "ccpc_erp" / "ci_finance_business_race_probe.py"
FIXTURE = ROOT / "ccpcerp" / "ccpc_erp" / "ci_finance_race_fixture.py"
SETUP = ROOT / "ccpcerp" / "ccpc_erp" / "ci_finance_setup_connection.py"
MASTER = ROOT / "ccpcerp" / "ccpc_erp" / "ci_finance_master_bootstrap.py"
CI = ROOT / ".circleci" / "config.yml"


class FinanceBoundaryProbeSourceTests(unittest.TestCase):
    def test_both_probes_parse_without_importing_frappe(self):
        for source in (HTTP, LOCK, RACE, FIXTURE, SETUP, MASTER):
            with self.subTest(source=str(source.name)):
                tree = ast.parse(source.read_text(encoding="utf-8"))
                self.assertIsInstance(tree, ast.Module)

    def test_http_probe_requires_isolated_identity_and_cookie_csrf(self):
        text = HTTP.read_text(encoding="utf-8")
        for value in (
            "assert_finance_disposable_site(SITE)",
            'CCPC_ALLOW_FINANCE_HTTP_PROBE',
            'CCPC_FINANCE_APPROVED_SHA',
            'CCPC_FINANCE_HTTP_BASE_URL',
            'CCPC_FINANCE_HTTP_CHECKER_PASSWORD',
            "ProxyHandler({})",
            "RejectRedirect()",
            "getaddrinfo(SITE, 8000)",
            "ccpc-finance-ci-checker@invalid.test",
            "get_logged_user",
            "not any(cookie.name == \"sid\"",
            "status != 403",
            "status != 400",
            'call(opener, "/app")',
            'error_type != "PermissionError"',
            'error_type != "CSRFTokenError"',
            'failure.get("exc_type")',
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertNotIn("approve_mapping(", text)
        self.assertNotIn("propose_mapping(", text)
        self.assertNotIn("frappe.db.commit(", text)
        self.assertNotIn("print(password", text)

    def test_lock_probe_requires_distinct_transactions_and_rollback(self):
        text = LOCK.read_text(encoding="utf-8")
        for value in (
            "assert_finance_disposable_site(",
            'CCPC_ALLOW_FINANCE_LOCK_PROBE',
            "CCPC_FINANCE_APPROVED_SHA",
            'COMPANY = "_Test Company"',
            "frappe.connect(set_admin_as_user=False)",
            "if second is primary:",
            "FOR UPDATE NOWAIT",
            "number not in (1205, 3572)",
            "second.rollback()",
            "primary.rollback()",
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertNotIn("frappe.db.commit(", text)
        self.assertNotIn("bench migrate", text)

    def test_real_business_race_uses_two_transactions_and_once_only_checks(self):
        text = RACE.read_text(encoding="utf-8")
        for value in (
            "assert_finance_disposable_site(",
            "CCPC_ALLOW_FINANCE_RACE_PROBE",
            "CCPC_FINANCE_APPROVED_SHA",
            'COMPANY = "_Test Company"',
            'CHECKER = "ccpc-finance-ci-checker@invalid.test"',
            "frappe.init(site=SITE, sites_path=sites_path)",
            "frappe.connect(set_admin_as_user=False)",
            "ThreadPoolExecutor(max_workers=2",
            "Barrier(2)",
            "barrier.wait(timeout=10)",
            '"Approve", "Retire"',
            "approve_mapping(",
            "decide_mapping_retirement(",
            "frappe.db.rollback()",
            "frappe.db.commit()",
            '"business_denied", "committed"',
            'frappe.db.count(USE, {"decision": decision_id}) != 1',
            'frappe.db.count(RETIRE_EVENT, {"revision": revision}) != 1',
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertNotIn("ignore_permissions=True", text)
        self.assertNotIn("Stock Entry", text)
        self.assertNotIn("GL Entry", text)
        self.assertNotIn('frappe.set_user("Administrator")', text)

    def test_real_fixture_builder_requires_independent_signed_and_committed_inputs(self):
        text = FIXTURE.read_text(encoding="utf-8")
        for value in (
            "assert_finance_disposable_site(",
            "CCPC_ALLOW_FINANCE_FIXTURE_PREP",
            "CCPC_ALLOW_FINANCE_FIXTURE_COMMIT",
            "CCPC_FINANCE_APPROVED_SHA",
            "frappe.conf.db_type",
            "with isolated_finance_setup_connection():",
            "verify_finance_master_records()",
            'COMPANY = "_Test Company"',
            'MAKER = "ccpc-finance-ci-maker@invalid.test"',
            'CHECKER = "ccpc-finance-ci-checker@invalid.test"',
            "frappe.db.exists(\"Item Group\", GROUPS[action])",
            "propose_mapping(",
            "get_mapping_review(",
            "sign_mapping_decision(",
            "approve_mapping(",
            "request_mapping_retirement(",
            "get_retirement_request_review(",
            "frappe.db.commit()",
            "frappe.db.rollback()",
            'if action == "Approve":',
            'if action == "Retire"',
        ):
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertNotIn("frappe.db.set_value(REV", text)
        self.assertNotIn("approval_state\": \"Approved\"", text)
        self.assertNotIn("set-password", text)
        self.assertNotIn("stock_entry", text.lower())

    def test_isolated_connection_restores_parent_and_rolls_back(self):
        text = SETUP.read_text(encoding="utf-8")
        for required in (
            "assert_finance_disposable_site(",
            "original_db = frappe.db",
            "original_flags = frappe.local.flags",
            'original_flags.get("read_only")',
            "frappe.local.flags = frappe._dict({",
            '"currently_saving": []',
            '"in_test": frappe.in_test',
            "frappe.connect(set_admin_as_user=False)",
            "if isolated_db is original_db:",
            'isolated_db.sql("SELECT DATABASE()")',
            "isolated_db.rollback()",
            "isolated_db.close()",
            "frappe.local.db = original_db",
            "frappe.set_user(original_user)",
            "frappe.local.flags = original_flags",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        # The setup context never commits the caller's transaction itself.
        self.assertNotIn(".commit(", text)

    def test_disposable_master_bootstrap_requires_commit_gate(self):
        text = MASTER.read_text(encoding="utf-8")
        for required in (
            "assert_finance_disposable_site(",
            "CCPC_ALLOW_FINANCE_MASTER_BOOTSTRAP",
            "CCPC_ALLOW_FINANCE_MASTER_COMMIT",
            "CCPC_FINANCE_APPROVED_SHA",
            "allow_tests",
            "isolated_finance_setup_connection()",
            'if frappe.db.exists("Company", COMPANY):',
            'if frappe.db.exists("Account", ACCOUNT):',
            '"company_name": COMPANY',
            '"chart_of_accounts": "Standard"',
            "verify_finance_master_records()",
            "frappe.db.commit()",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertNotIn("Stock Entry", text.split("def bootstrap_finance_test_masters():")[1])
        self.assertNotIn("load_test_records_for", text)
        self.assertNotIn("ignore_permissions=True", text.split("def verify_finance_master_records():")[1].split("def bootstrap_finance_test_masters():")[0])

    def test_neither_probe_is_wired_to_active_circleci_jobs(self):
        config = CI.read_text(encoding="utf-8")
        self.assertNotIn("finance_http_session_probe.py", config)
        self.assertNotIn("ci_finance_company_lock_probe", config)
        self.assertNotIn("ci_finance_business_race_probe", config)
        self.assertNotIn("ci_finance_race_fixture", config)
        self.assertNotIn("ci_finance_master_bootstrap", config)
        self.assertNotIn("ci_finance_setup_connection", config)
        self.assertIn("run_finance_lifecycle:\n    type: boolean\n    default: false", config)


if __name__ == "__main__":
    unittest.main()

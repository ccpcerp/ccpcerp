"""Offline-only source-policy checks for isolated Finance HTTP and SQL probes.

No Frappe import, database, network, credentials, subprocess or CircleCI job.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTTP = ROOT / ".circleci" / "finance_http_session_probe.py"
LOCK = ROOT / "ccpcerp" / "ccpc_erp" / "ci_finance_company_lock_probe.py"
CI = ROOT / ".circleci" / "config.yml"


class FinanceBoundaryProbeSourceTests(unittest.TestCase):
    def test_both_probes_parse_without_importing_frappe(self):
        for source in (HTTP, LOCK):
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
            "status != 405",
            "status != 403",
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

    def test_neither_probe_is_wired_to_active_circleci_jobs(self):
        config = CI.read_text(encoding="utf-8")
        self.assertNotIn("finance_http_session_probe.py", config)
        self.assertNotIn("ci_finance_company_lock_probe", config)
        self.assertIn("run_finance_lifecycle:\n    type: boolean\n    default: false", config)


if __name__ == "__main__":
    unittest.main()

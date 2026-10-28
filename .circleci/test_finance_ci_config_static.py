"""Static policy checks for CircleCI; NEVER starts a runner or imports Frappe."""
from pathlib import Path
from unittest import TestCase

text = (Path(__file__).resolve().parents[1] / ".circleci" / "config.yml").read_text()


class TestFinanceCiConfigStatic(TestCase):
    def test_finance_runner_is_opt_in_and_manually_approved(self):
        self.assertIn("run_finance_lifecycle:\n    type: boolean\n    default: false", text)
        self.assertIn("opt-in-finance-lifecycle:", text)
        self.assertIn("when: << pipeline.parameters.run_finance_lifecycle >>", text)
        self.assertIn("finance-lifecycle-manual-approval:", text)
        self.assertIn("type: approval", text)
        self.assertIn("- finance-lifecycle-manual-approval", text)

    def test_original_integration_job_keeps_hard_branch_guard(self):
        self.assertIn("run_integration:\n    type: boolean\n    default: false", text)
        self.assertIn('test "${CIRCLE_BRANCH:-}" = "review/circleci-isolated-erpnext-phase2-2026-10-26"', text)
        self.assertIn("opt-in-isolated-erpnext:", text)
        self.assertIn("when: << pipeline.parameters.run_integration >>", text)

    def test_finance_runner_requires_exact_reviewed_sha(self):
        self.assertIn("TEST_SITE: ci-finance-lifecycle.localhost", text)
        self.assertIn('test "${CIRCLE_SHA1}" = "${CCPC_FINANCE_APPROVED_SHA}"', text)
        self.assertIn('test "$(git rev-parse HEAD)" = "${CIRCLE_SHA1:-}"', text)
        self.assertIn("ci_finance_lifecycle_site_guard", text)
        self.assertIn("CCPC_ALLOW_FINANCE_LIFECYCLE_TESTS: isolated-only", text)
        self.assertNotIn("CCPC_FINANCE_APPROVED_SHA: ", text)
        self.assertIn("FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76", text)
        self.assertIn("ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd", text)

    def test_finance_job_does_not_run_existing_posting_suites(self):
        self.assertIn("--module ccpcerp.ccpc_erp.test_finance_lifecycle_disposable_integration", text)
        finance = text.split("  finance-lifecycle-isolated-integration:", 1)[1]
        finance = finance.split("\nworkflows:", 1)[0]
        for forbidden in (
            "test_bc_original_issue_real_integration",
            "test_intercompany_real_draft_invoice_database",
            "ccpc-integration2.localhost", "development.localhost",
            "docker.sock", "ssh-add", "context:",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, finance)

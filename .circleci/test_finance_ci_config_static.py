"""Static policy checks for CircleCI; NEVER starts a runner or imports Frappe."""
import ast
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
text = (ROOT / ".circleci" / "config.yml").read_text(encoding="utf-8")
stage = (ROOT / ".circleci" / "finance_baseline_stage.sh").read_text(encoding="utf-8")
deadline = (ROOT / ".circleci" / "finance_baseline_deadline.py").read_text(encoding="utf-8")
baseline = (ROOT / "ccpcerp" / "ccpc_erp" / "test_finance_lifecycle_disposable_integration.py").read_text(encoding="utf-8")


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
        self.assertIn("FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76", stage)
        self.assertIn("ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd", stage)

    def test_finance_job_does_not_run_existing_posting_suites(self):
        self.assertIn("--module ccpcerp.ccpc_erp.test_finance_lifecycle_disposable_integration", stage)
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
                self.assertNotIn(forbidden, stage)

    def test_each_finance_stage_is_guarded_and_cumulatively_timeboxed(self):
        finance = text.split("  finance-lifecycle-isolated-integration:", 1)[1].split("\nworkflows:", 1)[0]
        self.assertIn("python .circleci/finance_baseline_deadline.py start", finance)
        self.assertLess(
            finance.index('test "${CIRCLE_SHA1}" = "${CCPC_FINANCE_APPROVED_SHA}"'),
            finance.index("python .circleci/finance_baseline_deadline.py start"),
        )
        for task in ("services", "install", "tests"):
            with self.subTest(task=task):
                self.assertIn(
                    "python .circleci/finance_baseline_deadline.py run bash "
                    ".circleci/finance_baseline_stage.sh " + task, finance,
                )
        self.assertNotIn("bench init --", finance)
        self.assertNotIn("bench --site", finance)
        self.assertIn("MAX_SECONDS = 5400", deadline)
        self.assertIn("start_new_session=True", deadline)
        self.assertIn("os.killpg(child.pid, signal.SIGTERM)", deadline)
        self.assertIn("os.killpg(child.pid, signal.SIGKILL)", deadline)

    def test_stage_keeps_fixed_feature_branch_and_sha_guard(self):
        for required in (
            'test "${CIRCLE_BRANCH:-}" = "feature/finance-mapping-lifecycle-2026-10-28"',
            'test "${CIRCLE_JOB:-}" = "finance-lifecycle-isolated-integration"',
            'test "$(git rev-parse HEAD)" = "${CCPC_FINANCE_APPROVED_SHA:?}"',
            "assert_finance_disposable_site",
            "case \"${1:-}\" in",
        ):
            with self.subTest(required=required):
                self.assertIn(required, stage)
        for forbidden in (
            "ci_finance_business_race_probe", "ci_finance_company_lock_probe",
            "finance_http_session_probe", "ci_finance_race_fixture",
            "ci_finance_master_bootstrap",
        ):
            self.assertNotIn(forbidden, stage)

    def test_finance_has_no_unwrapped_duplicate_sidecar_wait(self):
        finance = text.split(
            "  finance-lifecycle-isolated-integration:", 1
        )[1].split("\nworkflows:", 1)[0]
        self.assertEqual(
            finance.count("name: Confirm disposable database and Redis sidecars"), 1
        )
        self.assertNotIn("for port in 3306 13000 11000; do", finance)
        self.assertEqual(
            finance.count(
                "python .circleci/finance_baseline_deadline.py run bash "
                ".circleci/finance_baseline_stage.sh services"
            ), 1
        )
        self.assertIn("for port in 3306 13000 11000; do", stage)


    def test_bench_upstream_remotes_fetch_both_pinned_commits(self):
        # bench init and bench get-app name their cloned Git remotes 'upstream'.
        # CircleCI #11 failed with exit 128 when Frappe was fetched from 'origin'.
        for app, sha in (
            ("frappe", "FRAPPE_SHA"),
            ("erpnext", "ERPNEXT_SHA"),
        ):
            with self.subTest(app=app):
                self.assertIn(
                    f'git -C apps/{app} fetch --depth 1 upstream "${sha}"',
                    stage,
                )
                self.assertIn(
                    f'test "$(git -C apps/{app} rev-parse HEAD)" = "${sha}"',
                    stage,
                )
        self.assertNotIn("fetch --depth 1 origin", stage)

    def test_baseline_suite_has_exactly_eight_reviewed_methods(self):
        expected = {
            "test_a_schema_has_required_lifecycle_fields_and_no_general_permissions",
            "test_b_draft_rejection_is_terminal_and_audited",
            "test_c_signed_approval_is_once_only_and_denies_forged_reference",
            "test_d_signing_version_failure_rolls_back_decision",
            "test_e_retirement_request_replay_decline_and_accept_are_audited",
            "test_f_unauthorized_guest_and_administrator_fail_closed",
            "test_g_center_approval_uses_exact_company_department",
            "test_h_rejection_audit_failure_rolls_back_event_and_state",
        }
        module = ast.parse(baseline)
        found = {
            n.name for cls in module.body if isinstance(cls, ast.ClassDef)
            for n in cls.body if isinstance(n, ast.FunctionDef)
            and n.name.startswith("test_")
        }
        self.assertEqual(found, expected)
        self.assertIn('self.load_test_records("Stock Entry")', baseline)

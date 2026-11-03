"""Stdlib-only negative tests for bounded, manually held billing review CI."""
import importlib.util
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ccpcerp" / "ccpc_erp" / "ci_billing_controls_site_guard.py"
spec = importlib.util.spec_from_file_location("billing_review_guard", TARGET)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

CONFIG = (ROOT / ".circleci" / "config.yml").read_text(encoding="utf-8")
STAGE = (ROOT / ".circleci" / "billing_controls_stage.sh").read_text(encoding="utf-8")
BRIDGE = (
    ROOT / "ccpcerp" / "ccpc_erp" /
    "test_finance_withdrawal_billing_bridge_disposable.py"
).read_text(encoding="utf-8")


class TestBillingControlsCiGuard(TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.env = {
            "CIRCLECI": "true",
            "CIRCLE_PROJECT_USERNAME": "CCPCerp_2026",
            "CIRCLE_PROJECT_REPONAME": "ccpcerp",
            "CIRCLE_BRANCH": guard.BILLING_BRANCH,
            "CIRCLE_JOB": guard.BILLING_JOB,
            "CIRCLE_PIPELINE_ID": "synthetic-pipeline",
            "CIRCLE_WORKFLOW_ID": "synthetic-workflow",
            "CIRCLE_BUILD_NUM": "1",
            "CIRCLE_SHA1": self.sha,
            "CCPC_BILLING_APPROVED_SHA": self.sha,
            "CCPC_ALLOW_BILLING_CONTROLS_TESTS": "isolated-only",
        }

    def allowed(self, site=None, env=None):
        with patch.dict(os.environ, self.env if env is None else env, clear=True):
            target = guard.BILLING_SITE if site is None else site
            return guard.is_reviewed_billing_controls_disposable_site(target)

    def test_exact_reviewed_identity_is_necessary(self):
        self.assertTrue(self.allowed())
        with patch.dict(os.environ, self.env, clear=True):
            guard.assert_billing_controls_disposable_site(guard.BILLING_SITE)

    def test_missing_or_changed_field_is_denied(self):
        for field in self.env:
            for change in ("", None):
                with self.subTest(field=field, change=change):
                    env = dict(self.env)
                    if change is None:
                        env.pop(field)
                    else:
                        env[field] = change
                    self.assertFalse(self.allowed(env=env))
        for field, value in {
            "CIRCLE_BRANCH": "feature/finance-mapping-lifecycle-2026-10-28",
            "CIRCLE_JOB": "finance-lifecycle-isolated-integration",
            "CIRCLE_PROJECT_USERNAME": "different-org",
            "CIRCLE_PROJECT_REPONAME": "different-repo",
            "CIRCLE_SHA1": "b" * 40,
            "CCPC_BILLING_APPROVED_SHA": "b" * 40,
            "CCPC_ALLOW_BILLING_CONTROLS_TESTS": "true",
        }.items():
            with self.subTest(field=field):
                self.assertFalse(self.allowed(env=dict(self.env, **{field: value})))
        for bad in ("not-a-sha", "A" * 40, "c" * 39, "c" * 41):
            self.assertFalse(self.allowed(env=dict(self.env, CIRCLE_SHA1=bad)))
            self.assertFalse(self.allowed(env=dict(
                self.env, CCPC_BILLING_APPROVED_SHA=bad,
            )))

    def test_only_new_disposable_site_is_allowed(self):
        for site in (
            "development.localhost", "ccpcerp.local", "production.localhost",
            "ci-finance-lifecycle.localhost", "ci-circleci.localhost",
            "", 0, False, "CI-BILLING-REVIEW.LOCALHOST",
        ):
            with self.subTest(site=site):
                self.assertFalse(self.allowed(site=site))
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaises(RuntimeError):
                guard.assert_billing_controls_disposable_site("ccpcerp.local")

    def test_new_workflow_is_opt_in_manual_and_branch_filtered(self):
        self.assertIn("run_billing_controls:\\n    type: boolean\\n    default: false".replace("\\n", "\n"), CONFIG)
        section = CONFIG.split("  opt-in-billing-controls:", 1)[1].split(
            "  opt-in-finance-bridge:", 1,
        )[0]
        self.assertIn("when: << pipeline.parameters.run_billing_controls >>", section)
        self.assertIn("billing-controls-manual-approval:", section)
        self.assertIn("type: approval", section)
        self.assertIn("- billing-controls-manual-approval", section)
        self.assertIn("only: review/billing-authorization-correction-2026-11-03", section)
        self.assertNotIn("schedule:", section)
        self.assertNotIn("context:", section)
        self.assertNotIn("ssh-add", section)
        self.assertNotIn("docker.sock", section)

    def test_stage_and_job_revalidate_sha_and_safety_budget(self):
        self.assertIn('test "$(git rev-parse HEAD)" = "${CCPC_BILLING_APPROVED_SHA:?}"', STAGE)
        self.assertIn('test "${CIRCLE_SHA1:-}" = "${CCPC_BILLING_APPROVED_SHA}"', STAGE)
        self.assertIn("assert_billing_controls_disposable_site", STAGE)
        self.assertIn("FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76", STAGE)
        self.assertIn("ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd", STAGE)
        self.assertIn("ci-billing-review.localhost", STAGE)
        self.assertIn("ccpcerp.ccpc_erp.test_finance_withdrawal_billing_bridge_disposable", STAGE)
        self.assertNotIn("bench --site ccpcerp.local", STAGE)
        self.assertNotIn("bench --site development.localhost", STAGE)
        self.assertNotIn("frappe.db.commit()", BRIDGE)
        self.assertIn("assert_finance_disposable_site(", BRIDGE)
        self.assertIn("assert_billing_controls_disposable_site(", BRIDGE)
        job = CONFIG.split("  billing-controls-isolated-integration:", 1)[1].split(
            "\nworkflows:", 1,
        )[0]
        self.assertIn("python .circleci/finance_baseline_deadline.py start", job)
        self.assertIn(
            "python .circleci/finance_baseline_deadline.py run bash "
            ".circleci/billing_controls_stage.sh tests", job,
        )
        self.assertNotIn("context:", job)
        self.assertNotIn("add_ssh_keys", job)

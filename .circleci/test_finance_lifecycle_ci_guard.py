"""Pure-stdlib negative tests; NO Frappe, DB, network or CircleCI job required."""
import importlib.util
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

repo = Path(__file__).resolve().parents[1]
target = repo / "ccpcerp" / "ccpc_erp" / "ci_finance_lifecycle_site_guard.py"
spec = importlib.util.spec_from_file_location("finance_ci_guard_target", target)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

old_source = repo / "ccpcerp" / "ccpc_erp" / "ci_disposable_site_guard.py"
old_spec = importlib.util.spec_from_file_location("original_ci_guard_target", old_source)
old_guard = importlib.util.module_from_spec(old_spec)
old_spec.loader.exec_module(old_guard)


class TestFinanceCiIsolation(TestCase):
    def setUp(self):
        self.sha = "9" * 40
        self.env = {
            "CIRCLECI": "true",
            "CIRCLE_PROJECT_USERNAME": "CCPCerp_2026",
            "CIRCLE_PROJECT_REPONAME": "ccpcerp",
            "CIRCLE_BRANCH": guard.FINANCE_BRANCH,
            "CIRCLE_JOB": guard.FINANCE_JOB,
            "CIRCLE_PIPELINE_ID": "test-only-pipeline",
            "CIRCLE_WORKFLOW_ID": "test-only-workflow",
            "CIRCLE_BUILD_NUM": "99",
            "CIRCLE_SHA1": self.sha,
            "CCPC_FINANCE_APPROVED_SHA": self.sha,
            "CCPC_ALLOW_FINANCE_LIFECYCLE_TESTS": "isolated-only",
        }

    def allowed(self, site=None, changed=None):
        env = dict(self.env, **(changed or {}))
        with patch.dict(os.environ, env, clear=True):
            return guard.is_reviewed_finance_lifecycle_disposable_site(
                guard.FINANCE_SITE if site is None else site
            )

    def test_requires_exact_disposable_identity_and_authorized_sha(self):
        self.assertTrue(self.allowed())
        with patch.dict(os.environ, self.env, clear=True):
            guard.assert_finance_disposable_site(guard.FINANCE_SITE)

    def test_all_missing_or_empty_identity_fields_fail_closed(self):
        for field in self.env:
            for value in ("", None):
                with self.subTest(field=field, value=value):
                    env = dict(self.env)
                    if value is None:
                        env.pop(field)
                    else:
                        env[field] = value
                    with patch.dict(os.environ, env, clear=True):
                        self.assertFalse(guard.is_reviewed_finance_lifecycle_disposable_site(
                            guard.FINANCE_SITE))
                        with self.assertRaises(RuntimeError):
                            guard.assert_finance_disposable_site(guard.FINANCE_SITE)

    def test_wrong_branch_runner_repo_job_and_sha_denied(self):
        for field, values in {
            "CIRCLECI": ("yes", "false"),
            "CIRCLE_PROJECT_USERNAME": ("CCPCerp", "ccpcerp", "other"),
            "CIRCLE_PROJECT_REPONAME": ("other", "ccpc-frappe-deployment"),
            "CIRCLE_BRANCH": ("main", "review/circleci-isolated-erpnext-phase2-2026-10-26"),
            "CIRCLE_JOB": ("isolated-erpnext-integration", "python-syntax"),
            "CIRCLE_SHA1": ("a" * 40, "ABC" * 13 + "A", "not-a-sha", "a" * 41),
            "CCPC_FINANCE_APPROVED_SHA": ("a" * 40, "ABC" * 13 + "A", "unapproved"),
            "CCPC_ALLOW_FINANCE_LIFECYCLE_TESTS": ("1", "true"),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertFalse(self.allowed(changed={field: value}))

    def test_every_existing_and_persistent_site_denied(self):
        for site in (
            "ci-circleci.localhost", "development.localhost",
            "ccpc-integration2.localhost", "ccpcerp.local",
            "production.localhost", "ci-withdrawal.localhost",
            "ci-intercompany.localhost", "CI-FINANCE-LIFECYCLE.LOCALHOST",
            "", False, 42,
        ):
            with self.subTest(site=site):
                self.assertFalse(self.allowed(site=site))

    def test_original_circleci_and_github_guards_remain_distinct(self):
        with patch.dict(os.environ, self.env, clear=True):
            self.assertFalse(old_guard.is_reviewed_circleci_disposable_site(
                guard.FINANCE_SITE))
            self.assertFalse(old_guard.is_reviewed_withdrawal_disposable_site(
                guard.FINANCE_SITE))
            self.assertFalse(old_guard.is_reviewed_intercompany_disposable_site(
                guard.FINANCE_SITE))
            self.assertFalse(old_guard.is_reviewed_circleci_disposable_site(
                "ci-circleci.localhost"))
        original = dict(self.env, **{
            "CIRCLE_BRANCH": "review/circleci-isolated-erpnext-phase2-2026-10-26",
            "CIRCLE_JOB": "isolated-erpnext-integration",
            "CCPC_ALLOW_COMMIT_PROBE": "isolated-only",
        })
        with patch.dict(os.environ, original, clear=True):
            self.assertTrue(old_guard.is_reviewed_circleci_disposable_site(
                "ci-circleci.localhost"))
            self.assertFalse(guard.is_reviewed_finance_lifecycle_disposable_site(
                "ci-circleci.localhost"))
            self.assertFalse(guard.is_reviewed_finance_lifecycle_disposable_site(
                guard.FINANCE_SITE))

    def test_no_fallback_from_arbitrary_ci_or_development_flags(self):
        env = {
            "CI": "true",
            "GITHUB_ACTIONS": "true",
            "CIRCLECI": "true",
            "CIRCLE_BRANCH": guard.FINANCE_BRANCH,
            "CIRCLE_SHA1": self.sha,
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(guard.is_reviewed_finance_lifecycle_disposable_site(
                guard.FINANCE_SITE))

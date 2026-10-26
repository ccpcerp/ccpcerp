"""Pure stdlib tests for the CI site-denial boundary; no Frappe/DB required."""
import importlib.util
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / "ccpcerp" / "ccpc_erp" / "ci_disposable_site_guard.py"
spec = importlib.util.spec_from_file_location("ccpc_ci_site_guard_test_target", source)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class TestDisposableCiBoundary(TestCase):
    def setUp(self):
        self.env = {
            "CIRCLECI": "true",
            "CIRCLE_PROJECT_USERNAME": "CCPCerp_2026",
            "CIRCLE_PROJECT_REPONAME": "ccpcerp",
            "CIRCLE_BRANCH": "review/circleci-isolated-erpnext-phase2-2026-10-26",
            "CIRCLE_JOB": "isolated-erpnext-integration",
            "CIRCLE_PIPELINE_ID": "synthetic-pipeline-id",
            "CIRCLE_WORKFLOW_ID": "synthetic-workflow-id",
            "CIRCLE_BUILD_NUM": "1",
            "CIRCLE_SHA1": "a" * 40,
            "CCPC_ALLOW_COMMIT_PROBE": "isolated-only",
        }

    def test_exact_circleci_identity_allows_only_disposable_site(self):
        with patch.dict(os.environ, self.env, clear=True):
            self.assertTrue(guard.is_reviewed_withdrawal_disposable_site("ci-circleci.localhost"))
            self.assertTrue(guard.is_reviewed_intercompany_disposable_site("ci-circleci.localhost"))
            for site in ("development.localhost", "production.localhost", "ci-withdrawal.localhost",
                         "ci-intercompany.localhost", None):
                self.assertFalse(guard.is_reviewed_withdrawal_disposable_site(site))
                self.assertFalse(guard.is_reviewed_intercompany_disposable_site(site))

    def test_any_missing_or_mismatched_identity_refuses_access(self):
        for field in self.env:
            with self.subTest(field=field):
                changed = dict(self.env)
                changed[field] = ""
                with patch.dict(os.environ, changed, clear=True):
                    self.assertFalse(guard.is_reviewed_circleci_disposable_site("ci-circleci.localhost"))
        for field, value in (("CIRCLE_PROJECT_REPONAME", "another"),
                             ("CIRCLE_BRANCH", "version-16"),
                             ("CIRCLE_JOB", "python-syntax"),
                             ("CIRCLE_PROJECT_USERNAME", "another"),
                             ("CIRCLE_PROJECT_USERNAME", "ccpcerp")):
            with self.subTest(field=field, value=value):
                changed = dict(self.env, **{field: value})
                with patch.dict(os.environ, changed, clear=True):
                    self.assertFalse(guard.is_reviewed_circleci_disposable_site("ci-circleci.localhost"))

    def test_original_github_actions_review_sites_remain_eligible(self):
        gh = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "ccpcerp/ccpcerp",
            "GITHUB_HEAD_REF": "review/withdrawal-issue-accounting-ci-2026-10-17",
            "CCPC_ALLOW_COMMIT_PROBE": "isolated-only",
        }
        with patch.dict(os.environ, gh, clear=True):
            self.assertTrue(guard.is_reviewed_withdrawal_disposable_site("ci-withdrawal.localhost"))
            self.assertFalse(guard.is_reviewed_withdrawal_disposable_site("ci-circleci.localhost"))
        gh["GITHUB_HEAD_REF"] = "review/intercompany-invoice-transaction-2026-10-20"
        with patch.dict(os.environ, gh, clear=True):
            self.assertTrue(guard.is_reviewed_intercompany_disposable_site("ci-intercompany.localhost"))
            self.assertFalse(guard.is_reviewed_intercompany_disposable_site("development.localhost"))

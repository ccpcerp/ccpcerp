"""GitHub PR45-only Finance disposable guard tests: no Frappe or DB import."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
GUARD = HERE / "ci_stockroom_center_pr45_site_guard.py"
FLOW = HERE.parents[1] / ".github/workflows/stockroom-center-pr45-disposable-db.yml"
FINANCE = HERE / "ci_finance_lifecycle_site_guard.py"
INTEGRATION = HERE / "test_finance_lifecycle_disposable_integration.py"

spec = importlib.util.spec_from_file_location("pr45_disposable_guard", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class TestStockroomPR45DisposableGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ccpc-pr45-guard-")
        self.addCleanup(self.tmp.cleanup)
        self.head = "d" * 40
        self.event_file = Path(self.tmp.name) / "event.json"
        self.event = {
            "action": "synchronize",
            "pull_request": {
                "number": 45, "draft": True,
                "head": {
                    "ref": guard.PR_HEAD_BRANCH, "sha": self.head,
                    "repo": {"full_name": guard.REPO},
                },
                "base": {
                    "ref": guard.PR_BASE_BRANCH,
                    "repo": {"full_name": guard.REPO},
                },
            },
        }
        self.event_file.write_text(json.dumps(self.event), encoding="utf-8")
        self.env = {
            "GITHUB_ACTIONS": "true", "CI": "true",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": guard.REPO,
            "GITHUB_HEAD_REF": guard.PR_HEAD_BRANCH,
            "GITHUB_BASE_REF": guard.PR_BASE_BRANCH,
            "GITHUB_REF": "refs/pull/45/merge",
            "GITHUB_WORKFLOW": guard.WORKFLOW,
            "GITHUB_JOB": "isolated-stockroom-center-db",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "591006",
            "GITHUB_EVENT_PATH": str(self.event_file),
            "CCPC_STOCKROOM_PR_HEAD": self.head,
            "CCPC_ALLOW_STOCKROOM_PR45_DB_TEST": "disposable-only",
        }

    _DEFAULT_SITE = object()

    def check(self, site=_DEFAULT_SITE, env=None):
        with patch.dict(os.environ, self.env if env is None else env, clear=True):
            return guard.is_reviewed_stockroom_pr45_disposable_site(
                guard.SITE if site is self._DEFAULT_SITE else site
            )

    def test_exact_synthetic_pr_event_and_site(self):
        self.assertTrue(self.check())
        with patch.dict(os.environ, self.env, clear=True):
            guard.assert_stockroom_pr45_disposable_site(guard.SITE)

    def test_other_site_is_rejected(self):
        for site in ("development.localhost", "ci-finance-lifecycle.localhost",
                     "ccpcerp.local", "", None):
            with self.subTest(site=site):
                self.assertFalse(self.check(site))
                with patch.dict(os.environ, self.env, clear=True):
                    with self.assertRaises(RuntimeError):
                        guard.assert_stockroom_pr45_disposable_site(site)

    def test_missing_wrong_or_spoofed_ci_values_rejected(self):
        for key, bad in (
            ("GITHUB_ACTIONS", "false"), ("CI", "false"),
            ("GITHUB_EVENT_NAME", "push"), ("GITHUB_REPOSITORY", "someone/other"),
            ("GITHUB_HEAD_REF", "main"), ("GITHUB_BASE_REF", "main"),
            ("GITHUB_REF", "refs/pull/46/merge"),
            ("GITHUB_WORKFLOW", "other"), ("GITHUB_JOB", "any-job"),
            ("GITHUB_RUN_ATTEMPT", "2"), ("GITHUB_RUN_ID", "0"),
            ("GITHUB_RUN_ID", "abc"), ("CCPC_STOCKROOM_PR_HEAD", "bad"),
            ("CCPC_ALLOW_STOCKROOM_PR45_DB_TEST", "yes"),
            ("GITHUB_EVENT_PATH", "/not-a-real-pr45-event"),
        ):
            with self.subTest(key=key, bad=bad):
                env = dict(self.env, **{key: bad})
                self.assertFalse(self.check(env=env))
        for key in self.env:
            with self.subTest(missing=key):
                env = dict(self.env)
                env.pop(key)
                self.assertFalse(self.check(env=env))

    def test_event_mismatch_rejected(self):
        variants = [
            ("action", "labeled"),
            ("pull_request.number", 44),
            ("pull_request.draft", False),
            ("pull_request.head.sha", "e" * 40),
            ("pull_request.head.ref", "other"),
            ("pull_request.head.repo.full_name", "outsider/repo"),
            ("pull_request.base.ref", "main"),
            ("pull_request.base.repo.full_name", "outsider/repo"),
        ]
        for path, value in variants:
            with self.subTest(path=path):
                data = json.loads(json.dumps(self.event))
                keys = path.split(".")
                dest = data
                for key in keys[:-1]:
                    dest = dest[key]
                dest[keys[-1]] = value
                self.event_file.write_text(json.dumps(data), encoding="utf-8")
                self.assertFalse(self.check())
                self.event_file.write_text(json.dumps(self.event), encoding="utf-8")

    def test_bad_event_file_fails_closed(self):
        self.event_file.write_text("{", encoding="utf-8")
        self.assertFalse(self.check())

    def test_original_circleci_gate_not_replaced(self):
        text = FINANCE.read_text()
        self.assertIn("CIRCLECI", text)
        self.assertIn("CCPC_FINANCE_APPROVED_SHA", text)
        self.assertNotIn("GITHUB_ACTIONS", text)
        integration = INTEGRATION.read_text()
        self.assertIn("assert_finance_disposable_site(site)", integration)
        self.assertIn("assert_stockroom_pr45_disposable_site(site)", integration)
        self.assertIn('== "test_i_stockroom_center_draft_only_and_independent_finance_approval"', integration)

    def test_workflow_has_narrow_pr_identity_and_disposable_only(self):
        text = FLOW.read_text()
        for required in (
            "types: [synchronize]", "github.event.pull_request.number == 45",
            "github.event.pull_request.draft",
            "github.run_attempt == 1",
            "runs-on: ubuntu-24.04", "mariadb:11.8",
            "redis:7-alpine", "persist-credentials: false",
            "ci-stockroom-center-pr45.localhost", "isolated-stockroom-center-db:",
            "CCPC_ALLOW_STOCKROOM_PR45_DB_TEST: disposable-only",
            "--test test_i_stockroom_center_draft_only_and_independent_finance_approval",
            "permissions:\n  contents: read",
        ):
            self.assertIn(required, text)
        for forbidden in (
            "\n  push:", "\n  schedule:", "\n  workflow_dispatch:",
            "self-hosted", "\u0024{{ secrets.", "development.localhost",
            "ccpcerp.local", "/home/gani/", "docker.sock",
            "bench --site production", "git push", "force-with-lease",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()

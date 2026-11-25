# Copyright (c) 2026, CCPC and contributors
"""Fail-closed source-only checks for integrated Phase 1 disposable CI gate."""
from pathlib import Path
import ast
import unittest
from unittest.mock import patch

from ccpcerp.ccpc_erp.ci_disposable_site_guard import (
    PHASE1_COMBINED_PR41_SITE,
    is_reviewed_phase1_combined_pr41_site,
    is_reviewed_withdrawal_disposable_site,
)

APP = Path(__file__).resolve().parents[1]
FLOW = APP.parent / ".github/workflows/phase1-maintenance-integrated-manual.yml"
REAL = Path(__file__).with_name("test_phase1_maintenance_integrated_real.py")
WORKER = Path(__file__).with_name("ci_phase1_integrated_concurrent_issue_worker.py")

class TestIntegratedManualGate(unittest.TestCase):
    def test_manual_pr_label_only(self):
        t = FLOW.read_text(encoding="utf-8")
        for required in ("types: [labeled]", "ccpc-run-phase1-combined-db", "github.event.pull_request.number == 44",
                         "github.event.pull_request.head.repo.full_name == 'ccpcerp/ccpcerp'",
                         "review/phase1-maintenance-integrated-tests-2026-11-24",
                         "review/phase1-maintenance-release-integration-2026-11-24",
                         "github.run_attempt == 1", "permissions:\\n  contents: read"):
            self.assertIn(required.replace("\\n", "\n"), t)
        for prohibited in ("\n  push:", "\n  schedule:", "\n  workflow_dispatch:", "runs-on: self-hosted", "${{ secrets."):
            self.assertNotIn(prohibited, t)

    def test_disposable_runner_and_no_live_site(self):
        t = FLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-24.04", t)
        self.assertIn("image: mariadb:11.8", t)
        self.assertIn("persist-credentials: false", t)
        self.assertIn("ci-phase1-integrated.localhost", t)
        self.assertIn("bench --site \"$TEST_SITE\" install-app ccpcerp", t)
        self.assertIn("bench --site \"$TEST_SITE\" migrate", t)
        for prohibited in ("development.localhost", "ccpcerp.local", "docker.sock",
                           "/home/gani/", "docker compose down", "bench --site production"):
            self.assertNotIn(prohibited, t)

    def test_real_suite_ten_methods_and_ledger(self):
        t = REAL.read_text(encoding="utf-8")
        a = ast.parse(t)
        methods = [m.name for c in a.body if isinstance(c, ast.ClassDef)
                   for m in c.body if isinstance(m, ast.FunctionDef)
                   and m.name.startswith("test_")]
        self.assertEqual(len(methods), 10)
        for required in ("Stock Ledger Entry", "GL Entry", "Routine Maintenance",
                         "Capital Project / Improvement", "Other / Unclassified",
                         "multiprocessing.get_context(\"spawn\")",
                         "_phase1_integrated_concurrent_worker", "ccpc_expense_mapping_revision",
                         "ccpc_center_mapping_revision"):
            self.assertIn(required, t)
        for forbidden in ('refs/pull/42/merge', "pr42-only"):
            self.assertNotIn(forbidden, t)

    def test_worker_restricted_to_disposable_ci(self):
        t = WORKER.read_text(encoding="utf-8")
        ast.parse(t)
        for required in ("ci-phase1-integrated.localhost",
                         "GITHUB_HEAD_REF", "GITHUB_BASE_REF",
                         "phase1-integrated-only", "CCPC_PHASE1_TEST_SHA",
                         "frappe.init(site=site", "frappe.db.commit()", "frappe.db.rollback()"):
            self.assertIn(required, t)
        for forbidden in ("development.localhost", "ccpcerp.local", "pr42-only"):
            self.assertNotIn(forbidden, t)

    def test_pr41_regressions_run_in_order_on_separate_site(self):
        workflow = FLOW.read_text(encoding="utf-8")
        self.assertIn(f"PR41_SITE: {PHASE1_COMBINED_PR41_SITE}", workflow)
        self.assertIn('bench new-site "$PR41_SITE" --db-host 127.0.0.1', workflow)
        self.assertIn('git merge-base --is-ancestor b599395f9c65cc6c6b8cc1b2750bc4559547161a HEAD', workflow)
        self.assertEqual(workflow.count('bench --site "$PR41_SITE" run-tests'), 2)
        self.assertEqual(workflow.count("steps.create_pr41_site.outcome == 'success'"), 2)
        self.assertEqual(workflow.count("grep -q '^Ran 1 test in '"), 2)
        self.assertEqual(workflow.count("grep -qx 'OK'"), 2)
        cancel = workflow.index("--test test_cancelled_issue_excluded_and_reissue_reported_once")
        http = workflow.index("--test test_zzz_real_wsgi_method_session_role_and_csrf_enforcement")
        self.assertLess(cancel, http)
        self.assertIn("CCPC_ALLOW_HTTP_PROBE: isolated-only", workflow[cancel:http])

    def test_pr41_guard_does_not_expand_existing_withdrawal_authority(self):
        valid = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "ccpcerp/ccpcerp",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_REF": "refs/pull/44/merge",
            "GITHUB_HEAD_REF": "review/phase1-maintenance-integrated-tests-2026-11-24",
            "GITHUB_BASE_REF": "review/phase1-maintenance-release-integration-2026-11-24",
            "GITHUB_WORKFLOW": "CCPC Phase1 - manual integrated Bearings MariaDB acceptance",
            "GITHUB_RUN_ID": "42",
            "CCPC_PHASE1_TEST_SHA": "b" * 40,
            "CCPC_ALLOW_COMBINED_PR41_PROBE": "isolated-only",
        }
        options = {"db_host": "127.0.0.1", "db_type": "mariadb"}
        with patch.dict("os.environ", valid, clear=True):
            self.assertTrue(is_reviewed_phase1_combined_pr41_site(PHASE1_COMBINED_PR41_SITE, **options))
            self.assertFalse(is_reviewed_withdrawal_disposable_site(PHASE1_COMBINED_PR41_SITE))
            self.assertFalse(is_reviewed_phase1_combined_pr41_site("ci-phase1-integrated.localhost", **options))
            self.assertFalse(is_reviewed_phase1_combined_pr41_site(
                PHASE1_COMBINED_PR41_SITE, db_host="db.invalid", db_type="mariadb",
            ))
            self.assertFalse(is_reviewed_phase1_combined_pr41_site(
                PHASE1_COMBINED_PR41_SITE, db_host="127.0.0.1", db_type="postgres",
            ))
        for key in valid:
            with self.subTest(missing=key), patch.dict(
                "os.environ", {k: v for k, v in valid.items() if k != key}, clear=True,
            ):
                self.assertFalse(is_reviewed_phase1_combined_pr41_site(PHASE1_COMBINED_PR41_SITE, **options))
        for key, value in (("GITHUB_REF", "refs/pull/43/merge"), ("GITHUB_RUN_ATTEMPT", "2"),
                           ("CCPC_PHASE1_TEST_SHA", "x" * 40)):
            with self.subTest(wrong=key), patch.dict("os.environ", dict(valid, **{key: value}), clear=True):
                self.assertFalse(is_reviewed_phase1_combined_pr41_site(PHASE1_COMBINED_PR41_SITE, **options))


if __name__ == "__main__":
    unittest.main()

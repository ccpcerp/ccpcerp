# Copyright (c) 2026, CCPC and contributors
"""Fail-closed source-only checks for integrated Phase 1 disposable CI gate."""
from pathlib import Path
import ast
import unittest

APP = Path(__file__).resolve().parents[1]
FLOW = APP.parent / ".github/workflows/phase1-maintenance-integrated-manual.yml"
REAL = Path(__file__).with_name("test_phase1_maintenance_integrated_real.py")
WORKER = Path(__file__).with_name("ci_phase1_integrated_concurrent_issue_worker.py")

class TestIntegratedManualGate(unittest.TestCase):
    def test_manual_pr_label_only(self):
        t = FLOW.read_text(encoding="utf-8")
        for required in ("types: [labeled]", "ccpc-run-phase1-integrated-db",
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

if __name__ == "__main__":
    unittest.main()

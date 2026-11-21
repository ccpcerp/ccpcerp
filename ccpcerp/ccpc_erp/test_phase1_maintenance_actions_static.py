# Copyright (c) 2026, CCPC and contributors
"""Site-free policy checks for the PR42 manually gated GitHub real-DB job."""
from pathlib import Path
import unittest

APP = Path(__file__).resolve().parents[1]
FLOW = APP.parent / ".github/workflows/phase1-maintenance-mariadb-manual.yml"
DB_SUITE = Path(__file__).with_name("test_phase1_maintenance_real_integration.py")


class TestPhase1MaintenanceActionsStatic(unittest.TestCase):
    def test_only_explicit_pr42_label_can_start_expensive_job(self):
        source = FLOW.read_text(encoding="utf-8")
        self.assertIn("types: [labeled]", source)
        self.assertIn("github.event.label.name == 'ccpc-run-pr42-db'", source)
        self.assertIn("github.event.pull_request.number == 42", source)
        self.assertIn("github.event.pull_request.head.repo.full_name == 'ccpcerp/ccpcerp'", source)
        self.assertIn("github.head_ref == 'feature/phase1-maintenance-only-2026-11-21'", source)
        for forbidden in ("\n  push:", "\n  schedule:", "\n  workflow_dispatch:"):
            self.assertNotIn(forbidden, source)

    def test_runner_cannot_use_persistent_credentials_or_sites(self):
        source = FLOW.read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-24.04", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("permissions:\n  contents: read", source)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha }}", source)
        self.assertIn("test \"$(git rev-parse HEAD)\" = \"$CCPC_PR42_TEST_SHA\"", source)
        self.assertIn("git merge-base --is-ancestor 034afb116cf3fe0e3f9315b932eceba6dc961ce6 HEAD", source)
        for forbidden in ("runs-on: self-hosted", "secrets.", "docker.sock", "development.localhost",
                          "ccpcerp.local", "/home/gani/", "docker compose down", "bench --site production"):
            self.assertNotIn(forbidden, source)

    def test_actual_disposable_mariadb_site_and_no_deploy_steps(self):
        source = FLOW.read_text(encoding="utf-8")
        self.assertIn("ci-pr42-maintenance.localhost", source)
        self.assertIn("image: mariadb:11.8", source)
        self.assertIn("bench --site \"$TEST_SITE\" install-app erpnext", source)
        self.assertIn("bench --site \"$TEST_SITE\" install-app ccpcerp", source)
        self.assertIn("bench --site \"$TEST_SITE\" migrate", source)
        self.assertIn("ccpcerp.ccpc_erp.test_phase1_maintenance_real_integration", source)
        self.assertNotIn("environment: production", source)

    def test_db_suite_checks_real_ledger_and_fails_closed_outside_ci(self):
        source = DB_SUITE.read_text(encoding="utf-8")
        for needed in ("GITHUB_ACTIONS", "GITHUB_HEAD_REF", "GITHUB_BASE_REF",
                       "GITHUB_WORKFLOW", "CCPC_ALLOW_MAINTENANCE_DB_PROBE",
                       'frappe.conf.get("db_host") == "127.0.0.1"',
                       "Stock Ledger Entry", "GL Entry",
                       "ccpc_expense_mapping_revision", "ccpc_center_mapping_revision",
                       "Routine Maintenance", "Capital Project / Improvement",
                       "Other / Unclassified", "Maintenance beneficiary",
                       "cannot mix", "Withdrawal Slip"):
            self.assertIn(needed, source)
        self.assertEqual(source.count("    def test_"), 6)


if __name__ == "__main__":
    unittest.main()

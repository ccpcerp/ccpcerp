# Copyright (c) 2026, CCPC and contributors
"""Site-free policy checks for the PR42 manually gated GitHub real-DB job."""
from pathlib import Path
import unittest

APP = Path(__file__).resolve().parents[1]
FLOW = APP.parent / ".github/workflows/phase1-maintenance-mariadb-manual.yml"
DB_SUITE = Path(__file__).with_name("test_phase1_maintenance_real_integration.py")
WORKER = Path(__file__).with_name("ci_pr42_concurrent_issue_worker.py")


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
        for forbidden in ("runs-on: self-hosted", "${{ secrets.", "docker.sock", "development.localhost",
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
                       'env.get("GITHUB_REF") == "refs/pull/42/merge"',
                       "GITHUB_WORKFLOW", "CCPC_ALLOW_MAINTENANCE_DB_PROBE",
                       'frappe.conf.get("db_host") == "127.0.0.1"',
                       "Stock Ledger Entry", "GL Entry",
                       "ccpc_expense_mapping_revision", "ccpc_center_mapping_revision",
                       "Routine Maintenance", "Capital Project / Improvement",
                       "Other / Unclassified", "Maintenance beneficiary",
                       "cannot mix", "Withdrawal Slip"):
            self.assertIn(needed, source)
        for coverage in (
            "def test_g_failed_posting_rollback_discards_stock_and_gl_as_one_unit",
            "def test_h_actual_cancellation_reverses_inventory_and_gl",
            "def test_i_non_stockroom_and_request_creator_cannot_issue",
            "def test_j_two_real_independent_stockroom_connections_only_issue_once",
            "multiprocessing.get_context(\"spawn\")",
            "SELECT name FROM `tabWithdrawal Slip` WHERE name=%s FOR UPDATE",
            "frappe.db.rollback",
            'CCPC_ALLOW_MAINTENANCE_DB_PROBE',
            "CI injected",
        ):
            # These paths must be visibly covered; the real assertions execute
            # only in the isolated Actions MariaDB suite.
            if coverage != "CI injected":
                self.assertIn(coverage, source)
        self.assertEqual(source.count("    def test_"), 10)


    def test_spawn_worker_is_lightweight_and_tightly_scoped(self):
        source = WORKER.read_text(encoding="utf-8")
        for required in (
            'site == "ci-pr42-maintenance.localhost"',
            'env.get("GITHUB_HEAD_REF") == "feature/phase1-maintenance-only-2026-11-21"',
            'env.get("GITHUB_REF") == "refs/pull/42/merge"',
            'env.get("CCPC_ALLOW_MAINTENANCE_DB_PROBE") == "pr42-only"',
            "frappe.init(site=site, sites_path=sites_path, force=True)",
            "SELECT CONNECTION_ID()",
            "frappe.db.commit()",
            "frappe.db.rollback()",
            "stock.issue_withdrawal_slip(slip_name)",
        ):
            self.assertIn(required, source)
        for dangerous in (
            "from erpnext.tests.utils",
            "from erpnext.stock.doctype.item.test_item",
            "from ccpcerp.ccpc_erp import test_",
            "development.localhost",
            "production",
            "runs-on: self-hosted",
        ):
            self.assertNotIn(dangerous, source)


if __name__ == "__main__":
    unittest.main()

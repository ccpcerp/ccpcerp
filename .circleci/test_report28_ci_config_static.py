# Copyright (c) 2026, CCPC and contributors
"""PR28 default-OFF CI policy checks. No network, Frappe or MariaDB."""
import ast
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ccpcerp.ccpc_erp.ci_report28_site_guard import (
    REPORT_BRANCH, REPORT_JOB, REPORT_SITE, is_reviewed_report28_disposable_site,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / ".circleci" / "config.yml").read_text(encoding="utf-8")
STAGE = (ROOT / ".circleci" / "report28_stage.sh").read_text(encoding="utf-8")
TEST = (ROOT / "ccpcerp" / "ccpc_erp" /
        "test_intercompany_report28_disposable_integration.py").read_text(encoding="utf-8")
REPORT = (ROOT / "ccpcerp" / "ccpc_erp" / "report" /
          "ccpc_intercompany_withdrawal_billing_status" /
          "ccpc_intercompany_withdrawal_billing_status.py").read_text(encoding="utf-8")


class TestReport28CiStatic(TestCase):
    def test_separate_default_off_workflow_has_manual_approval(self):
        self.assertIn(
            "run_intercompany_report28:\n    type: boolean\n    default: false",
            CONFIG,
        )
        flow = CONFIG.split("  opt-in-report28-acceptance:\n", 1)[1].split(
            "  opt-in-finance-bridge:", 1
        )[0]
        self.assertIn("when: << pipeline.parameters.run_intercompany_report28 >>", flow)
        self.assertIn("report28-manual-approval:", flow)
        self.assertIn("type: approval", flow)
        self.assertIn("intercompany-report28-isolated-integration:", flow)
        self.assertIn("- report28-manual-approval", flow)
        self.assertIn("only: " + REPORT_BRANCH, flow)
        self.assertNotIn("schedule:", flow)
        self.assertNotIn("context:", flow)

    def test_existing_finance_workflow_unchanged(self):
        self.assertIn(
            "run_finance_bridge:\n    type: boolean\n    default: false", CONFIG
        )
        self.assertIn("finance-bridge-manual-approval:", CONFIG)
        self.assertIn("finance-lifecycle-manual-approval:", CONFIG)
        self.assertIn("finance-lifecycle-isolated-integration:", CONFIG)
        self.assertIn("CCPC_FINANCE_APPROVED_SHA", CONFIG)

    def test_job_and_stage_reject_unapproved_sha_before_sidecars(self):
        job = CONFIG.split("  intercompany-report28-isolated-integration:\n", 1)[1].split(
            "\nworkflows:", 1
        )[0]
        self.assertIn("resource_class: large", job)
        self.assertIn("ci-intercompany-report28.localhost", job)
        self.assertIn("bash .circleci/report28_stage.sh identity", job)
        self.assertLess(job.index("report28_stage.sh identity"),
                        job.index("report28_stage.sh services"))
        self.assertLess(job.index("report28_stage.sh services"),
                        job.index("report28_stage.sh install"))
        self.assertIn("finance_baseline_deadline.py run bash", job)
        self.assertIn('[[ "$APPROVED" =~ ^[0-9a-f]{40}$ ]]', STAGE)
        self.assertIn('[[ "${CIRCLE_SHA1:-}" == "$APPROVED" ]]', STAGE)
        self.assertIn('[[ "$(git rev-parse HEAD)" == "$APPROVED" ]]', STAGE)
        self.assertIn('[[ "$(git -C apps/ccpcerp rev-parse HEAD)" == "$APPROVED" ]]', STAGE)
        self.assertIn('FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76', STAGE)
        self.assertIn('ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd', STAGE)
        self.assertNotIn("ssh-add", job + STAGE)
        self.assertNotIn("docker.sock", job + STAGE)

    def test_report_integration_does_not_execute_inherited_bridge_cases(self):
        module = ast.parse(TEST)
        classes = [node for node in module.body if isinstance(node, ast.ClassDef)]
        self.assertEqual(len(classes), 1)
        suite = classes[0]
        tests = [node.name for node in suite.body
                 if isinstance(node, ast.FunctionDef)
                 and node.name.startswith("test_report_")]
        self.assertEqual(len(tests), 5)
        masks = {
            target.id
            for node in suite.body if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant) and node.value.value is None
            for target in node.targets if isinstance(target, ast.Name)
        }
        self.assertTrue({
            "test_a_signed_issue_reaches_real_ledger_and_billing_source",
            "test_b_unsigned_issue_cannot_enter_billing_preview",
            "test_c_real_source_finalizes_to_actual_paired_invoice_drafts",
            "test_d_second_draft_insert_failure_rolls_back_real_first_invoice",
        }.issubset(masks))
        self.assertLess(
            TEST.index("assert_report28_disposable_site("),
            TEST.index("ERPNextTestSuite.setUp(self)"),
        )
        self.assertNotIn("frappe.db.commit(", TEST)
        self.assertNotIn(".submit(", TEST)
        self.assertIn("issue_withdrawal_slip(", TEST)
        self.assertIn("report.execute(", TEST)

    def test_positive_and_negative_exact_sha_guard_cases(self):
        identity = {
            "CIRCLECI": "true",
            "CIRCLE_PROJECT_USERNAME": "CCPCerp_2026",
            "CIRCLE_PROJECT_REPONAME": "ccpcerp",
            "CIRCLE_BRANCH": REPORT_BRANCH,
            "CIRCLE_JOB": REPORT_JOB,
            "CIRCLE_PIPELINE_ID": "test-id",
            "CIRCLE_WORKFLOW_ID": "test-workflow",
            "CIRCLE_BUILD_NUM": "123",
            "CIRCLE_SHA1": "a" * 40,
            "CCPC_REPORT28_APPROVED_SHA": "a" * 40,
            "CCPC_ALLOW_REPORT28_TESTS": "isolated-only",
        }
        with patch.dict("os.environ", identity, clear=True):
            self.assertTrue(is_reviewed_report28_disposable_site(REPORT_SITE))
            self.assertFalse(is_reviewed_report28_disposable_site("development.localhost"))
            self.assertFalse(is_reviewed_report28_disposable_site("ccpcerp.local"))
            for key, bad in (
                ("CIRCLE_SHA1", "b" * 40),
                ("CCPC_REPORT28_APPROVED_SHA", ""),
                ("CIRCLE_BRANCH", "feature/finance-mapping-lifecycle-2026-10-28"),
                ("CIRCLE_JOB", "finance-lifecycle-isolated-integration"),
                ("CIRCLECI", "false"),
                ("CCPC_ALLOW_REPORT28_TESTS", ""),
            ):
                with patch.dict("os.environ", {key: bad}):
                    self.assertFalse(is_reviewed_report28_disposable_site(REPORT_SITE))

    def test_report_still_read_only(self):
        self.assertIn("def _require_access():", REPORT)
        self.assertIn("WHERE se.docstatus = 1", REPORT)
        self.assertIn("AND ws.requesting_company = %(requesting_company)s", REPORT)
        self.assertNotIn("frappe.db.commit(", REPORT)
        self.assertNotIn("frappe.db.set_value(", REPORT)

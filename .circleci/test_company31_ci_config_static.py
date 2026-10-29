# Copyright (c) 2026, CCPC and contributors
"""Pure-source CI guard regression for manually approved PR31 only."""
import ast
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ccpcerp.ccpc_erp.ci_company31_site_guard import (
    BRANCH, JOB, SITE, is_approved_site,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / ".circleci" / "config.yml").read_text(encoding="utf8")
STAGE = (ROOT / ".circleci" / "company31_stage.sh").read_text(encoding="utf8")
INTEGRATION = (ROOT / "ccpcerp" / "ccpc_erp" /
               "test_company_gl_report31_disposable_integration.py").read_text(encoding="utf8")
REPORT = (ROOT / "ccpcerp" / "ccpc_erp" / "report" /
          "ccpc_company_stock_pnl_gl_reconciliation" / "ccpc_company_stock_pnl_gl_reconciliation.py").read_text(encoding="utf8")


class TestPR31Guard(TestCase):
    def test_a_default_off_scoped_manually_held_branch(self):
        self.assertIn("run_company_gl_report31:\n    type: boolean\n    default: false", CONFIG)
        flow = CONFIG.split("  opt-in-company31-acceptance:\n", 1)[1].split(
            "  opt-in-finance-bridge:", 1
        )[0]
        self.assertIn("when: << pipeline.parameters.run_company_gl_report31 >>", flow)
        self.assertIn("- company31-manual-approval:", flow)
        self.assertIn("type: approval", flow)
        self.assertIn("- company-gl-report31-isolated-integration:", flow)
        self.assertIn("requires:\n            - company31-manual-approval", flow)
        self.assertEqual(flow.count("only: " + BRANCH), 2)
        self.assertNotIn("schedule:", flow)
        self.assertNotIn("context:", flow)

    def test_b_existing_finance_controls_unchanged(self):
        for name in ("run_integration", "run_finance_lifecycle", "run_finance_bridge"):
            self.assertIn(name + ":\n    type: boolean\n    default: false", CONFIG)
        self.assertIn("finance-lifecycle-manual-approval", CONFIG)
        self.assertIn("finance-bridge-manual-approval", CONFIG)
        self.assertIn("only: feature/finance-mapping-lifecycle-2026-10-28", CONFIG)
        self.assertNotIn("run_department_report29", CONFIG)

    def test_c_exact_SHA_and_bench_identity_before_install(self):
        job = CONFIG.split("  company-gl-report31-isolated-integration:\n", 1)[1].split(
            "\nworkflows:", 1
        )[0]
        self.assertIn("resource_class: large", job)
        self.assertIn("TEST_SITE: " + SITE, job)
        self.assertIn("CCPC_ALLOW_COMPANY31_TESTS: isolated-only", job)
        self.assertIn("bash .circleci/company31_stage.sh identity", job)
        self.assertLess(
            job.index("company31_stage.sh identity"),
            job.index("company31_stage.sh install"),
        )
        for phrase in (
            '[[ "$APPROVED" =~ ^[0-9a-f]{40}$ ]]',
            '[[ "${CIRCLE_SHA1:-}" == "$APPROVED" ]]',
            '[[ "$(git rev-parse HEAD)" == "$APPROVED" ]]',
            '[[ "$(git -C apps/ccpcerp rev-parse HEAD)" == "$APPROVED" ]]',
            "CCPC_COMPANY31_APPROVED_SHA",
            "finance_baseline_deadline.py start",
            "finance_baseline_deadline.py run",
            "FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76",
            "ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd",
        ):
            self.assertIn(phrase, STAGE)
        self.assertNotIn("ssh-add", STAGE)
        self.assertNotIn("docker.sock", STAGE)

    def test_d_real_suite_is_guarded_not_inheriting_old_case_methods(self):
        classes = [x for x in ast.parse(INTEGRATION).body
                   if isinstance(x, ast.ClassDef)]
        self.assertEqual(len(classes), 1)
        case = classes[0]
        self.assertEqual([getattr(x, "id", None) for x in case.bases],
                         ["ERPNextTestSuite"])
        self.assertEqual(len([
            x for x in case.body if isinstance(x, ast.FunctionDef)
            and x.name.startswith("test_")
        ]), 5)
        self.assertLess(
            INTEGRATION.index("assert_approved_site(getattr(frappe.local"),
            INTEGRATION.index("ERPNextTestSuite.setUp(self)"),
        )
        self.assertNotIn("frappe.db.commit(", INTEGRATION)
        self.assertNotIn("frappe.db.sql(\"DELETE", INTEGRATION)
        self.assertIn("issue_withdrawal_slip(", INTEGRATION)
        self.assertIn("report.execute(", INTEGRATION)

    def test_e_fail_closed_wrong_branch_site_job_and_SHA(self):
        env = {
            "CIRCLECI": "true",
            "CIRCLE_PROJECT_USERNAME": "CCPCerp_2026",
            "CIRCLE_PROJECT_REPONAME": "ccpcerp",
            "CIRCLE_BRANCH": BRANCH,
            "CIRCLE_JOB": JOB,
            "CIRCLE_PIPELINE_ID": "test-pipeline",
            "CIRCLE_WORKFLOW_ID": "test-workflow",
            "CIRCLE_BUILD_NUM": "123",
            "CIRCLE_SHA1": "a" * 40,
            "CCPC_COMPANY31_APPROVED_SHA": "a" * 40,
            "CCPC_ALLOW_COMPANY31_TESTS": "isolated-only",
        }
        with patch.dict("os.environ", env, clear=True):
            self.assertTrue(is_approved_site(SITE))
            self.assertFalse(is_approved_site("development.localhost"))
            self.assertFalse(is_approved_site("ci-department-report29.localhost"))
            for key, bad in (
                ("CIRCLE_BRANCH", "version-16"),
                ("CIRCLE_JOB", "finance-lifecycle-isolated-integration"),
                ("CIRCLE_SHA1", "b" * 40),
                ("CCPC_COMPANY31_APPROVED_SHA", "x" * 40),
                ("CCPC_COMPANY31_APPROVED_SHA", ""),
                ("CCPC_ALLOW_COMPANY31_TESTS", ""),
                ("CIRCLE_PIPELINE_ID", ""),
            ):
                with patch.dict("os.environ", {key: bad}):
                    self.assertFalse(is_approved_site(SITE))

    def test_f_original_report_only_no_posting_or_unsafe_cross_source_join(self):
        self.assertIn("def _require_access():", REPORT)
        self.assertIn("se.docstatus = 1", REPORT)
        self.assertIn("sle.is_cancelled = 0", REPORT)
        self.assertIn("GROUP BY sle.company, sle.voucher_no, sle.voucher_detail_no", REPORT)
        for unsafe in ("frappe.db.commit(", "frappe.db.set_value(",
                       "tabIntercompany Material Billing Item"):
            self.assertNotIn(unsafe, REPORT)

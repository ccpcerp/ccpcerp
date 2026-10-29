# Copyright (c) 2026, CCPC and contributors
"""PR29 Department disposable CI guard regression: pure Python, no DB/network."""
import ast
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ccpcerp.ccpc_erp.ci_department29_site_guard import (
    DEPT_BRANCH, DEPT_JOB, DEPT_SITE,
    is_approved_department29_disposable_site,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / ".circleci" / "config.yml").read_text(encoding="utf-8")
STAGE = (ROOT / ".circleci" / "department29_stage.sh").read_text(encoding="utf-8")
TESTS = (ROOT / "ccpcerp" / "ccpc_erp" /
         "test_department_report29_disposable_integration.py").read_text(encoding="utf-8")
REPORT = (ROOT / "ccpcerp" / "ccpc_erp" / "report" /
          "ccpc_department_material_consumption" /
          "ccpc_department_material_consumption.py").read_text(encoding="utf-8")


class TestDepartment29CIConfigStatic(TestCase):
    def test_default_off_branch_scoped_manual_hold(self):
        self.assertIn(
            "run_department_report29:\n    type: boolean\n    default: false",
            CONFIG,
        )
        flow = CONFIG.split("  opt-in-department29-acceptance:\n", 1)[1].split(
            "  opt-in-finance-bridge:", 1
        )[0]
        self.assertIn(
            "when: << pipeline.parameters.run_department_report29 >>", flow,
        )
        self.assertIn("- department29-manual-approval:", flow)
        self.assertIn("type: approval", flow)
        self.assertIn("- department-report29-isolated-integration:", flow)
        self.assertIn("requires:\n            - department29-manual-approval", flow)
        self.assertEqual(flow.count("only: " + DEPT_BRANCH), 2)
        self.assertNotIn("context:", flow)
        self.assertNotIn("schedule:", flow)

    def test_preserve_prior_finance_default_off_jobs_and_branches(self):
        for parameter in (
            "run_finance_lifecycle", "run_finance_bridge", "run_integration",
        ):
            self.assertIn(
                parameter + ":\n    type: boolean\n    default: false", CONFIG,
            )
        self.assertIn("finance-lifecycle-isolated-integration:", CONFIG)
        self.assertIn("finance-lifecycle-manual-approval:", CONFIG)
        self.assertIn("finance-bridge-manual-approval:", CONFIG)
        self.assertIn("only: feature/finance-mapping-lifecycle-2026-10-28", CONFIG)
        self.assertNotIn("run_intercompany_report28", CONFIG)
        self.assertNotIn("CCPC_FINANCE_APPROVED_SHA: ", CONFIG)

    def test_exact_sha_and_disposable_site_gate_before_any_bench(self):
        job = CONFIG.split("  department-report29-isolated-integration:\n", 1)[1].split(
            "\nworkflows:", 1
        )[0]
        self.assertIn("resource_class: large", job)
        self.assertIn("TEST_SITE: " + DEPT_SITE, job)
        self.assertIn("CCPC_ALLOW_DEPT29_TESTS: isolated-only", job)
        self.assertIn("bash .circleci/department29_stage.sh identity", job)
        self.assertLess(
            job.index("department29_stage.sh identity"),
            job.index("department29_stage.sh install"),
        )
        self.assertIn('[[ "$APPROVED" =~ ^[0-9a-f]{40}$ ]]', STAGE)
        self.assertIn('[[ "${CIRCLE_SHA1:-}" == "$APPROVED" ]]', STAGE)
        self.assertIn('[[ "$(git rev-parse HEAD)" == "$APPROVED" ]]', STAGE)
        self.assertIn('[[ "$(git -C apps/ccpcerp rev-parse HEAD)" == "$APPROVED" ]]', STAGE)
        self.assertIn("CCPC_DEPT29_APPROVED_SHA", STAGE)
        self.assertIn("CIRCLE_JOB", STAGE)
        self.assertIn("finance_baseline_deadline.py start", STAGE)
        self.assertIn("finance_baseline_deadline.py run", STAGE)
        self.assertNotIn("ssh-add", job + STAGE)
        self.assertNotIn("docker.sock", job + STAGE)
        self.assertIn("FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76", STAGE)
        self.assertIn("ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd", STAGE)

    def test_new_integration_suite_borrows_helpers_not_parent_tests(self):
        tree = ast.parse(TESTS)
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertEqual(len(classes), 1)
        suite = classes[0]
        self.assertEqual(
            [getattr(v, "id", "") for v in suite.bases],
            ["ERPNextTestSuite"],
        )
        methods = [
            node.name for node in suite.body
            if isinstance(node, ast.FunctionDef)
            and node.name.startswith("test_")
        ]
        self.assertEqual(len(methods), 5)
        self.assertTrue(all(m.startswith("test_") for m in methods))
        self.assertLess(
            TESTS.index("assert_department29_disposable_site("),
            TESTS.index("ERPNextTestSuite.setUp(self)"),
        )
        self.assertIn("_verify_genuine_finance_workflow = True", TESTS)
        self.assertNotIn("frappe.db.commit(", TESTS)
        self.assertNotIn(".submit(", TESTS)
        self.assertIn("issue_withdrawal_slip(", TESTS)
        self.assertIn("report.execute(", TESTS)

    def test_company_branch_site_job_and_approved_sha_guard_fail_closed(self):
        env = {
            "CIRCLECI": "true",
            "CIRCLE_PROJECT_USERNAME": "CCPCerp_2026",
            "CIRCLE_PROJECT_REPONAME": "ccpcerp",
            "CIRCLE_BRANCH": DEPT_BRANCH,
            "CIRCLE_JOB": DEPT_JOB,
            "CIRCLE_PIPELINE_ID": "sample-pipeline",
            "CIRCLE_WORKFLOW_ID": "sample-workflow",
            "CIRCLE_BUILD_NUM": "123",
            "CIRCLE_SHA1": "a" * 40,
            "CCPC_DEPT29_APPROVED_SHA": "a" * 40,
            "CCPC_ALLOW_DEPT29_TESTS": "isolated-only",
        }
        with patch.dict("os.environ", env, clear=True):
            self.assertTrue(is_approved_department29_disposable_site(DEPT_SITE))
            self.assertFalse(is_approved_department29_disposable_site("development.localhost"))
            self.assertFalse(is_approved_department29_disposable_site(
                "ci-intercompany-report28.localhost"
            ))
            for key, bad in (
                ("CIRCLE_SHA1", "b" * 40),
                ("CCPC_DEPT29_APPROVED_SHA", ""),
                ("CCPC_DEPT29_APPROVED_SHA", "x" * 40),
                ("CIRCLE_BRANCH", "feature/finance-mapping-lifecycle-2026-10-28"),
                ("CIRCLE_JOB", "intercompany-report28-isolated-integration"),
                ("CIRCLECI", "false"),
                ("CCPC_ALLOW_DEPT29_TESTS", ""),
                ("CIRCLE_PIPELINE_ID", ""),
            ):
                with patch.dict("os.environ", {key: bad}):
                    self.assertFalse(
                        is_approved_department29_disposable_site(DEPT_SITE)
                    )

    def test_report_is_read_only_original_sle_not_gl_or_current_slip_attribution(self):
        self.assertIn("def _require_access():", REPORT)
        self.assertIn("WHERE se.docstatus = 1", REPORT)
        self.assertIn("GROUP BY sle.voucher_no, sle.voucher_detail_no, sle.company", REPORT)
        self.assertIn("NO_ORIGINAL_SECTION_SNAPSHOT", REPORT)
        for forbidden in (
            "frappe.db.commit(", "frappe.db.set_value(", "tabGL Entry",
            "ws.department", "ws.section", "ws.work_for_department",
        ):
            self.assertNotIn(forbidden, REPORT)

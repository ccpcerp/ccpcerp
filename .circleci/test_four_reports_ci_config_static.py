# Copyright (c) 2026, CCPC and contributors
"""Pure-source safeguards for the default-off combined report CI job."""
import ast
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ccpcerp.ccpc_erp.ci_four_reports_site_guard import (
    BRANCH, JOB, SITE, is_approved_site,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / ".circleci" / "config.yml").read_text(encoding="utf8")
STAGE = (ROOT / ".circleci" / "four_reports_stage.sh").read_text(encoding="utf8")
REPORT_DIR = ROOT / "ccpcerp" / "ccpc_erp" / "report"
FIXTURE_DIR = ROOT / "ccpcerp" / "ccpc_erp"

NAMES = (
    "ccpc_intercompany_withdrawal_billing_status",
    "ccpc_department_material_consumption",
    "ccpc_equipment_job_material_activity",
    "ccpc_company_stock_pnl_gl_reconciliation",
)
FIXTURES = (
    "test_four_reports_28_disposable_integration.py",
    "test_four_reports_29_disposable_integration.py",
    "test_four_reports_30_disposable_integration.py",
    "test_four_reports_31_disposable_integration.py",
)


class TestCombinedFourReportCI(TestCase):
    def test_a_single_default_off_held_approval_branch(self):
        self.assertIn(
            "run_four_reports_integrated:\n    type: boolean\n    default: false", CONFIG
        )
        flow = CONFIG.split("  opt-in-four-reports-acceptance:\n", 1)[1].split(
            "  opt-in-finance-bridge:", 1
        )[0]
        self.assertIn(
            "when: << pipeline.parameters.run_four_reports_integrated >>", flow
        )
        self.assertIn("- four-reports-manual-approval:", flow)
        self.assertIn("type: approval", flow)
        self.assertIn("- four-reports-isolated-integration:", flow)
        self.assertIn("requires:\n            - four-reports-manual-approval", flow)
        self.assertEqual(flow.count("only: " + BRANCH), 2)
        self.assertNotIn("schedule:", flow)
        self.assertNotIn("context:", flow)
        for old in ("run_integration", "run_finance_lifecycle", "run_finance_bridge"):
            self.assertIn(old + ":\n    type: boolean\n    default: false", CONFIG)
        self.assertIn("finance-lifecycle-manual-approval", CONFIG)
        self.assertIn("finance-bridge-manual-approval", CONFIG)

    def test_b_before_db_install_guard_and_framework_pins(self):
        job = CONFIG.split("  four-reports-isolated-integration:\n", 1)[1].split(
            "\nworkflows:", 1
        )[0]
        self.assertIn("resource_class: large", job)
        self.assertIn("TEST_SITE: " + SITE, job)
        self.assertIn("CCPC_ALLOW_FOUR_REPORTS_TESTS: isolated-only", job)
        self.assertIn("bash .circleci/four_reports_stage.sh identity", job)
        self.assertLess(
            job.index("four_reports_stage.sh identity"),
            job.index("four_reports_stage.sh install"),
        )
        for expected in (
            '[[ "$APPROVED" =~ ^[0-9a-f]{40}$ ]]',
            '[[ "${CIRCLE_SHA1:-}" == "$APPROVED" ]]',
            '[[ "$(git rev-parse HEAD)" == "$APPROVED" ]]',
            '[[ "$(git -C apps/ccpcerp rev-parse HEAD)" == "$APPROVED" ]]',
            "CCPC_FOUR_REPORTS_APPROVED_SHA",
            "ci_four_reports_site_guard",
            "finance_baseline_deadline.py start",
            "finance_baseline_deadline.py run",
            "FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76",
            "ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd",
        ):
            self.assertIn(expected, STAGE)
        self.assertNotIn("ssh-add", STAGE)
        self.assertNotIn("docker.sock", STAGE)

    def test_c_four_report_native_test_modules_are_guarded(self):
        expected = [8, 6, 5, 5]
        actual = []
        for fname in FIXTURES:
            source = (FIXTURE_DIR / fname).read_text(encoding="utf8")
            classes = [x for x in ast.parse(source).body
                       if isinstance(x, ast.ClassDef)]
            self.assertEqual(len(classes), 1)
            case = classes[0]
            methods = [node for node in case.body
                       if isinstance(node, ast.FunctionDef)
                       and node.name.startswith("test_")]
            actual.append(len(methods))
            self.assertIn("ci_four_reports_site_guard", source)
            self.assertLess(
                source.index('assert_approved_site(getattr(frappe.local, "site", None))'),
                source.index("ERPNextTestSuite.setUp(self)"),
            )
            self.assertNotIn("frappe.db.commit(", source)
            self.assertNotIn("frappe.db.sql(\"DELETE", source)
            self.assertIn("report.execute(", source)
            self.assertIn(fname.removesuffix(".py"), STAGE)
        self.assertEqual(actual, expected)

    def test_d_all_four_source_report_modules_and_seven_extra_unit_cases(self):
        expected = (17, 17, 15, 15)
        counts = []
        for name in NAMES:
            folder = REPORT_DIR / name
            report = (folder / (name + ".py")).read_text(encoding="utf8")
            tests = (folder / ("test_" + name + ".py")).read_text(encoding="utf8")
            classes = [x for x in ast.parse(tests).body if isinstance(x, ast.ClassDef)]
            self.assertEqual(len(classes), 1)
            count = sum(isinstance(x, ast.FunctionDef) and
                        x.name.startswith("test_") for x in classes[0].body)
            counts.append(count)
            self.assertIn("def _require_access():", report)
            self.assertNotIn("frappe.db.commit(", report)
            self.assertIn(name + ".test_" + name, STAGE)
        self.assertEqual(tuple(counts), expected)
        report28 = (REPORT_DIR / NAMES[0] / (NAMES[0] + ".py")).read_text()
        report29 = (REPORT_DIR / NAMES[1] / (NAMES[1] + ".py")).read_text()
        self.assertIn("LIMIT %(row_limit)s", report28)
        self.assertIn("if len(rows) > MAX_ROWS", report28)
        self.assertIn('("Sales Invoice", row.sales_invoice)', report28)
        self.assertIn('("Purchase Invoice", row.purchase_invoice)', report28)
        self.assertIn('("Stock Entry", row.stock_entry)', report29)
        self.assertIn('("Withdrawal Slip", row.withdrawal_slip)', report29)
        self.assertIn('("Item", row.item_code)', report29)

    def test_e_guard_denies_wrong_job_branch_site_sha_or_missing_approval(self):
        env = {
            "CIRCLECI": "true", "CIRCLE_PROJECT_USERNAME": "CCPCerp_2026",
            "CIRCLE_PROJECT_REPONAME": "ccpcerp",
            "CIRCLE_BRANCH": BRANCH, "CIRCLE_JOB": JOB,
            "CIRCLE_PIPELINE_ID": "ci-pipe", "CIRCLE_WORKFLOW_ID": "ci-flow",
            "CIRCLE_BUILD_NUM": "57", "CIRCLE_SHA1": "a" * 40,
            "CCPC_FOUR_REPORTS_APPROVED_SHA": "a" * 40,
            "CCPC_ALLOW_FOUR_REPORTS_TESTS": "isolated-only",
        }
        with patch.dict("os.environ", env, clear=True):
            self.assertTrue(is_approved_site(SITE))
            for other in ("development.localhost", "ccpc-integration2.localhost",
                          "ci-company-gl-report31.localhost", "ccpcerp-prod"):
                self.assertFalse(is_approved_site(other))
            for key, bad in (
                ("CIRCLE_BRANCH", "version-16"),
                ("CIRCLE_PROJECT_REPONAME", "ccpcerp-prod"),
                ("CIRCLE_JOB", "finance-lifecycle-isolated-integration"),
                ("CIRCLE_SHA1", "b" * 40),
                ("CCPC_FOUR_REPORTS_APPROVED_SHA", "b" * 40),
                ("CCPC_FOUR_REPORTS_APPROVED_SHA", ""),
                ("CCPC_ALLOW_FOUR_REPORTS_TESTS", ""),
                ("CIRCLE_WORKFLOW_ID", ""),
            ):
                with patch.dict("os.environ", {key: bad}):
                    self.assertFalse(is_approved_site(SITE))

    def test_f_cross_report_test_uses_same_original_voucher(self):
        fixture = (FIXTURE_DIR / FIXTURES[0]).read_text(encoding="utf8")
        self.assertIn("def test_report_f_same_signed_issue_all_four_read_only_reports", fixture)
        self.assertIn("source = self._row(detail)", fixture)
        self.assertIn("department_report.execute(common)", fixture)
        self.assertIn("equipment_report.execute(common)", fixture)
        self.assertIn("company_report.execute(common)", fixture)
        self.assertIn('self.assertIsNone(equip[0]["job_order"])', fixture)

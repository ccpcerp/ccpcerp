"""Source-only guard tests; never starts CircleCI, Frappe or a database.

The bridge is an explicitly selected second workflow, with its own manual
hold but reuses the established fail-closed Finance G2 job, exact SHA,
disposable site and 90-minute cumulative wall-clock runner.
"""
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / ".circleci" / "config.yml").read_text(encoding="utf-8")
STAGE = (ROOT / ".circleci" / "finance_baseline_stage.sh").read_text(encoding="utf-8")
BRIDGE = (
    ROOT / "ccpcerp" / "ccpc_erp" /
    "test_finance_withdrawal_billing_bridge_disposable.py"
).read_text(encoding="utf-8")
GUARD = (
    ROOT / "ccpcerp" / "ccpc_erp" / "ci_finance_lifecycle_site_guard.py"
).read_text(encoding="utf-8")


class TestBridgeCiStatic(TestCase):
    def test_bridge_is_default_off_and_manual(self):
        self.assertIn(
            "run_finance_bridge:\n    type: boolean\n    default: false", CONFIG
        )
        workflow = CONFIG.split("  opt-in-finance-bridge:", 1)[1]
        self.assertIn("when: << pipeline.parameters.run_finance_bridge >>", workflow)
        self.assertIn("finance-bridge-manual-approval:", workflow)
        self.assertIn("type: approval", workflow)
        self.assertIn("finance-lifecycle-isolated-integration:", workflow)
        self.assertIn("run_bridge: true", workflow)
        self.assertIn("- finance-bridge-manual-approval", workflow)
        self.assertIn("only: feature/finance-mapping-lifecycle-2026-10-28", workflow)

    def test_legacy_workflow_and_job_remain_explicit_opt_in(self):
        self.assertIn(
            "run_integration:\n    type: boolean\n    default: false", CONFIG
        )
        self.assertIn(
            "run_finance_lifecycle:\n    type: boolean\n    default: false", CONFIG
        )
        self.assertIn("when: << pipeline.parameters.run_integration >>", CONFIG)
        self.assertIn(
            "when: << pipeline.parameters.run_finance_lifecycle >>", CONFIG
        )
        finance_job = CONFIG.split(
            "  finance-lifecycle-isolated-integration:", 1
        )[1].split("\nworkflows:", 1)[0]
        self.assertIn(
            "run_bridge:\n        type: boolean\n        default: false",
            finance_job,
        )
        self.assertIn("condition: << parameters.run_bridge >>", finance_job)

    def test_bridge_inherits_all_identity_sha_and_deadline_guards(self):
        finance_job = CONFIG.split(
            "  finance-lifecycle-isolated-integration:", 1
        )[1].split("\nworkflows:", 1)[0]
        self.assertIn('test "${CIRCLE_SHA1}" = "${CCPC_FINANCE_APPROVED_SHA}"', finance_job)
        self.assertIn(
            "python .circleci/finance_baseline_deadline.py start", finance_job
        )
        self.assertIn(
            "python .circleci/finance_baseline_deadline.py run bash "
            ".circleci/finance_baseline_stage.sh bridge", finance_job
        )
        self.assertIn("bridge)", STAGE)
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "${CCPC_FINANCE_APPROVED_SHA:?}"',
            STAGE,
        )
        self.assertIn(
            'test "$(git -C apps/ccpcerp rev-parse HEAD)" = '
            '"${CCPC_FINANCE_APPROVED_SHA:?}"',
            STAGE,
        )
        self.assertIn(
            "bench --site \"$TEST_SITE\" run-tests \\\n"
            "      --module ccpcerp.ccpc_erp.test_finance_withdrawal_billing_bridge_disposable",
            STAGE,
        )
        self.assertIn(
            'FINANCE_JOB = "finance-lifecycle-isolated-integration"', GUARD
        )
        self.assertIn('FINANCE_SITE = "ci-finance-lifecycle.localhost"', GUARD)

    def test_no_autostart_or_production_access(self):
        bridge_workflow = CONFIG.split("  opt-in-finance-bridge:", 1)[1]
        self.assertNotIn("schedule:", bridge_workflow)
        self.assertNotIn("context:", bridge_workflow)
        self.assertNotIn("ssh-add", bridge_workflow)
        self.assertNotIn("docker.sock", bridge_workflow)
        self.assertNotIn("approve_and_create_invoices", BRIDGE)
        self.assertNotIn(".submit()", BRIDGE)
        self.assertIn("assert_finance_disposable_site(", BRIDGE)
        self.assertIn("sign_mapping_decision(", BRIDGE)
        self.assertIn("test_a_signed_issue_reaches_real_ledger_and_billing_source", BRIDGE)
        self.assertIn("test_b_unsigned_issue_cannot_enter_billing_preview", BRIDGE)

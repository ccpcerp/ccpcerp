# Copyright (c) 2026, CCPC and contributors
"""Cheap source-only safety checks for disabled-by-default Ricor SQL workflow."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / ".circleci" / "config.yml").read_text(encoding="utf-8")
STAGE = (ROOT / ".circleci" / "ricor_direct_stage.sh").read_text(encoding="utf-8")
TEST = (ROOT / "ccpcerp" / "ccpc_erp" /
        "test_ricor_direct_consumption_real_fixtures.py").read_text(encoding="utf-8")
BRANCH = "review/ricor-direct-consumption-billing-item-2026-11-17"


class TestRicorDirectCIStatic(unittest.TestCase):
    def test_opt_in_defaults_false_and_hold(self):
        self.assertRegex(CONFIG, r"(?m)^  run_ricor_direct:\n    type: boolean\n    default: false$")
        self.assertIn("when: << pipeline.parameters.run_ricor_direct >>", CONFIG)
        self.assertIn("- ricor-direct-manual-approval:", CONFIG)
        self.assertIn("type: approval", CONFIG)
        self.assertIn("requires:\n            - ricor-direct-manual-approval", CONFIG)
        self.assertIn("only: " + BRANCH, CONFIG)

    def test_exact_job_guard_and_site(self):
        for required in (
            'test "${CIRCLECI:-}" = "true"',
            'test "${CIRCLE_BRANCH:-}" = "' + BRANCH + '"',
            'test "${CIRCLE_JOB:-}" = "ricor-direct-consumption-integration"',
            'test "${TEST_SITE:-}" = "ci-ricor-direct.localhost"',
            'test "${CIRCLE_SHA1:-}" = "${CCPC_RICOR_APPROVED_SHA}"',
            'test "$(git rev-parse HEAD)" = "${CCPC_RICOR_APPROVED_SHA}"',
            'test "${CCPC_ALLOW_RICOR_DIRECT_FIXTURES:-}" = "isolated-only"',
            "  identity)", "  services)", "  install)", "  tests)",
        ):
            with self.subTest(required=required):
                self.assertIn(required, STAGE)

    def test_disposable_sources_and_no_persistent_host_commands(self):
        self.assertIn("ci-ricor-direct.localhost", TEST)
        self.assertIn("CCPC_RICOR_APPROVED_SHA", TEST)
        self.assertIn("test_native_nonstock_purchase_invoice", TEST)
        self.assertIn("frappe.db.get_value", TEST)
        self.assertIn("frappe.get_doc", TEST)
        self.assertNotIn("development.localhost", STAGE)
        self.assertNotIn("ccpcerp.local", STAGE)
        self.assertNotIn("docker exec", STAGE)
        self.assertNotIn("submit(", TEST)
        self.assertNotIn("frappe.db.commit(", TEST)
        self.assertNotIn("frappe.db.sql(\"COMMIT", TEST)
        self.assertFalse(re.search(r"(?m)^\s*run_ricor_direct:\s*true\b", CONFIG))


if __name__ == "__main__":
    unittest.main()

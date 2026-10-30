"""Source-only restore CI safety checks. Does not invoke CircleCI or decrypt anything."""
from pathlib import Path
import unittest


BRANCH = "review/pr32-integration2-isolated-restore-2026-10-30"
CONTEXT = "ccpc-pr32-integration2-restore-once"


class RestoreCIStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent
        cls.config = (cls.root / "config.yml").read_text(encoding="utf8")
        cls.stage = (cls.root / "pr32_restore_stage.sh").read_text(encoding="utf8")

    def test_default_off_and_manual_hold(self):
        self.assertIn("run_integration2_backup_restore:\n    type: boolean\n    default: false", self.config)
        self.assertIn("integration2-restore-manual-approval:", self.config)
        self.assertIn("type: approval", self.config)
        self.assertIn("requires:\n            - integration2-restore-manual-approval", self.config)

    def test_branch_excluded_from_starter(self):
        self.assertIn("ignore:", self.config)
        self.assertIn("- " + BRANCH, self.config)
        self.assertIn("only: " + BRANCH, self.config)

    def test_restricted_context_required(self):
        self.assertIn("context: " + CONTEXT, self.config)
        self.assertIn("CCPC_ALLOW_INTEGRATION2_RESTORE: approved-only", self.config)
        self.assertIn("CCPC_RESTORE_OBJECT_GET_URL", self.config)
        self.assertIn("CCPC_RESTORE_AGE_IDENTITY", self.config)

    def test_no_secrets_during_dependency_install(self):
        self.assertGreaterEqual(
            self.config.count("env -u CCPC_RESTORE_OBJECT_GET_URL -u CCPC_RESTORE_AGE_IDENTITY"), 3
        )

    def test_no_ci_artifacts_or_extra_network_deploy(self):
        restore_job = self.config.split("  integration2-backup-restore-isolated:", 1)[1].split("\nworkflows:", 1)[0]
        for bad in ("store_artifacts:", "store_test_results:", "save_cache:",
                    "persist_to_workspace:", "setup_remote_docker:", "ssh ",
                    "docker run", "docker cp", "checkout: production"):
            self.assertNotIn(bad, restore_job)

    def test_exact_original_sha_source_and_git_fsck(self):
        self.assertIn("ORIGINAL_APP_SHA=0c223d7cbe8b27c2392d8b2303c45a99dd4d8b41", self.stage)
        self.assertIn("git -C apps/ccpcerp fsck --full --strict --no-reflogs", self.stage)
        self.assertIn("status --porcelain=v1 --untracked-files=no", self.stage)


if __name__ == "__main__":
    unittest.main()

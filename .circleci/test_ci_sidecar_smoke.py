"""Synthetic unit tests only; no DB, Frappe, CI provider or external backup."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ci_sidecar_smoke as smoke
import ci_db_attestation as db


def approved():
    return {
        "CIRCLECI": "true", "CIRCLE_PROJECT_USERNAME": "CCPCerp_2026",
        "CIRCLE_PROJECT_REPONAME": "ccpcerp", "CIRCLE_BRANCH": smoke.BRANCH,
        "CIRCLE_JOB": smoke.JOB, "TEST_SITE": db.SITE,
        "CCPC_NO_BACKUP_SMOKE": "disposable-only",
        "CIRCLE_PIPELINE_ID": "synth-p", "CIRCLE_WORKFLOW_ID": "synth-w",
        "CIRCLE_BUILD_NUM": "1", "CIRCLE_SHA1": "f" * 40,
        "CCPC_SMOKE_APPROVED_SHA": "f" * 40,
        "CI_DB_ROOT_PASSWORD": "ccpc-smoke-disposable-root-only",
    }


class SmokeTests(unittest.TestCase):
    def test_no_data_egress_or_restore_consent_required(self):
        smoke.require_smoke_identity(approved(), "f" * 40)

    def test_rejects_wrong_branch_job_or_sha(self):
        for k, value in (("CIRCLE_BRANCH", "main"), ("CIRCLE_JOB", "restore"),
                         ("CIRCLE_SHA1", "e" * 40),
                         ("CCPC_SMOKE_APPROVED_SHA", "e" * 40)):
            with self.subTest(k=k):
                env = approved()
                env[k] = value
                with self.assertRaises(RuntimeError):
                    smoke.require_smoke_identity(env, "f" * 40)

    def test_rejects_debug_and_missing_workflow(self):
        for key, value in (("CIRCLE_SSH_ENABLED", "true"),
                           ("CIRCLE_WORKFLOW_ID", "")):
            env = approved()
            env[key] = value
            with self.assertRaises(RuntimeError):
                smoke.require_smoke_identity(env, "f" * 40)

    def test_option_file_0600_private_and_no_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            path = smoke._smoke_db_options(Path(d) / "private", "synthetic")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(RuntimeError):
                smoke._smoke_db_options(path.parent, "synthetic")

    def test_no_created_site_if_database_already_exists(self):
        with tempfile.TemporaryDirectory() as d, patch.object(
            smoke.benchguard, "attest_pinned_bench"
        ), patch.object(smoke.db, "_local_mariadb_probe", return_value=(("host", "MariaDB", "1"), 1)):
            with patch.object(smoke.subprocess, "run") as runner:
                with self.assertRaisesRegex(RuntimeError, "already present"):
                    smoke.smoke_create_and_attest(approved(), "f" * 40, Path(d), Path(d) / "bench")
                runner.assert_not_called()

    def test_no_site_if_wrong_synthetic_credential(self):
        env = approved()
        env["CI_DB_ROOT_PASSWORD"] = "different"
        with patch.object(smoke.benchguard, "attest_pinned_bench") as checker:
            with self.assertRaises(RuntimeError):
                smoke.smoke_create_and_attest(env, "f" * 40, Path("/tmp"), Path("/tmp/bench"))
            checker.assert_not_called()

    def test_no_backup_decryption_or_restore_code(self):
        source = Path(smoke.__file__).read_text(encoding="utf8")
        for forbidden in ("bench restore", "age -d", "requests.get(",
                          "urlopen(", "CREATE DATABASE", "DROP DATABASE",
                          "--force", "CCPC_RESTORE_OWNER_DATA_EGRESS_APPROVED"):
            self.assertNotIn(forbidden, source)

    def test_original_source_requires_independent_fetch_and_git_integrity(self):
        stage = Path(__file__).with_name("pr32_smoke_stage.sh").read_text(encoding="utf8")
        self.assertIn("git@github.com:ccpcerp/ccpcerp.git", stage)
        self.assertIn("fetch.fsckObjects=true", stage)
        self.assertIn("fsck --full --strict --no-reflogs", stage)
        self.assertIn("status --porcelain=v1 --untracked-files=no", stage)
        self.assertIn("Original app pinned worktree is incomplete or changed", stage)

    def test_pre_post_sql_probes_share_one_function(self):
        source = Path(smoke.__file__).read_text(encoding="utf8")
        self.assertIn("first, absent = db._local_mariadb_probe", source)
        self.assertIn("second, present = db._local_mariadb_probe", source)
        self.assertIn("if second != first or present != 1:", source)
        self.assertIn("benchguard.attest_pinned_bench(home, bench, site_created=True)", source)


if __name__ == "__main__":
    unittest.main()

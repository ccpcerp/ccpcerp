"""Source-only restore CI safety checks. Does not invoke CircleCI or decrypt anything."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from ci_original_source_probe import classify
from ci_source_hydration import hydrate_missing, missing_object_ids


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
        self.assertIn("CCPC_RESTORE_B2_KEY_ID", self.config)
        self.assertIn("CCPC_RESTORE_B2_APP_KEY", self.config)
        self.assertNotIn("CCPC_RESTORE_OBJECT_GET_URL", self.config)
        self.assertIn("CCPC_RESTORE_AGE_IDENTITY", self.config)

    def test_no_secrets_during_dependency_install(self):
        self.assertGreaterEqual(
            self.config.count("env -u CCPC_RESTORE_B2_KEY_ID -u CCPC_RESTORE_B2_APP_KEY -u CCPC_RESTORE_AGE_IDENTITY"), 7
        )

    def test_scoped_b2_prefetch_occurs_before_slow_dependency_install(self):
        restore_job = self.config.split(
            "  integration2-backup-restore-isolated:", 1
        )[1].split("\n  pr32-restore-source-preflight:", 1)[0]
        encrypted_get = restore_job.find("python .circleci/ci_restore_runtime.py --prefetch")
        slow_install = restore_job.find("bash .circleci/pr32_restore_stage.sh prerequisites")
        bench_setup = restore_job.find("bash .circleci/pr32_restore_stage.sh bench")
        final_restore = restore_job.find("python .circleci/ci_restore_runtime.py --restore")
        self.assertTrue(0 <= encrypted_get < slow_install < bench_setup < final_restore)
        self.assertIn("umask 077", restore_job)
        self.assertIn("CCPC_RESTORE_B2_KEY_ID", self.config)
        self.assertIn("CCPC_RESTORE_B2_APP_KEY", self.config)
        self.assertIn("test_ci_b2_native.py", self.config)
        self.assertNotIn("CCPC_RESTORE_OBJECT_GET_URL", restore_job)
        self.assertNotIn("store_artifacts:", restore_job)
        self.assertNotIn("save_cache:", restore_job)

    def test_no_ci_artifacts_or_extra_network_deploy(self):
        restore_job = self.config.split("  integration2-backup-restore-isolated:", 1)[1].split("\nworkflows:", 1)[0]
        for bad in ("store_artifacts:", "store_test_results:", "save_cache:",
                    "persist_to_workspace:", "setup_remote_docker:", "ssh ",
                    "docker run", "docker cp", "checkout: production"):
            self.assertNotIn(bad, restore_job)

    def test_pinned_original_fetch_uses_existing_https_checkout_noninteractive(self):
        # GitHub App checkout has HTTPS auth, no SSH deploy key. Fetch the
        # original ancestor through that credential helper, then copy locally.
        self.assertIn('git -C "$APP_CHECKOUT" remote get-url origin', self.stage)
        self.assertIn("https://github.com/ccpcerp/ccpcerp.git", self.stage)
        self.assertIn("GIT_TERMINAL_PROMPT=0", self.stage)
        self.assertIn("GCM_INTERACTIVE=Never", self.stage)
        self.assertIn("timeout --signal=TERM --kill-after=5s 120", self.stage)
        self.assertIn("timeout --signal=TERM --kill-after=5s 60", self.stage)
        self.assertIn("fetch --no-tags --no-filter --depth 1 origin", self.stage)
        self.assertIn("ci_source_hydration.py", self.stage)
        self.assertIn('"$APP_CHECKOUT" "$ORIGINAL_APP_SHA"', self.stage)
        self.assertIn(">/dev/null 2>&1", self.stage)
        self.assertNotIn("git@github.com:", self.stage)
        self.assertNotIn("StrictHostKeyChecking=no", self.stage)
        self.assertNotIn("ssh-keyscan", self.stage)
        self.assertNotIn("credential.helper store", self.stage)
        self.assertNotIn("git -C apps/ccpcerp fetch --unshallow", self.stage)

    def test_blobless_checkout_hydrates_original_commit_before_local_copy(self):
        # Synthetic file:// Git transfer reproduces Build #82's eight missing
        # historical blobs even after fetching the old commit. No external
        # network, CI credentials, Frappe, or databases are accessed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original"
            checkout = root / "checkout"
            bench = root / "bench"
            original.mkdir()
            env = dict(os.environ)
            env.update({
                "GIT_ALLOW_PROTOCOL": "file",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_COUNT": "0",
            })

            def git(*argv):
                run = subprocess.run(
                    ["git", *map(str, argv)],
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, check=True, timeout=15,
                )
                return run.stdout.strip()

            git("init", "--quiet", original)
            git("-C", original, "config", "user.name", "Source Test")
            git("-C", original, "config", "user.email", "ci@example.invalid")
            for i in range(8):
                (original / f"tracked{i}.txt").write_text(f"original-{i}\n")
            git("-C", original, "add", ".")
            git("-C", original, "commit", "--quiet", "-m", "pinned original")
            old_sha = git("-C", original, "rev-parse", "HEAD")
            for i in range(8):
                (original / f"tracked{i}.txt").write_text(f"new-{i}\n")
            git("-C", original, "commit", "--quiet", "-am", "review")
            git("-C", original, "config", "uploadpack.allowFilter", "true")
            git("clone", "--quiet", "--filter=blob:none", "--depth", "1",
                original.as_uri(), checkout)
            self.assertEqual(
                git("-C", checkout, "config", "--get", "remote.origin.promisor"),
                "true",
            )
            git("clone", "--quiet", checkout, bench)
            git("-C", checkout, "-c", "fetch.fsckObjects=true", "fetch",
                "--quiet", "--no-tags", "--filter=blob:none", "--depth", "1",
                "origin", old_sha)
            self.assertEqual(len(missing_object_ids(checkout, old_sha)), 8)
            blocked = subprocess.run(
                ["git", "-C", str(bench), "-c", "fetch.fsckObjects=true",
                 "fetch", "--quiet", "--no-tags", "--depth", "1",
                 str(checkout), old_sha],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=15,
            )
            self.assertNotEqual(blocked.returncode, 0)
            # The helper must explicitly enable Git promisor lazy fetching,
            # unlike the no-network object scan, then close the missing set.
            with mock.patch.dict(os.environ, env):
                self.assertEqual(hydrate_missing(checkout, old_sha), 8)
            self.assertEqual(len(missing_object_ids(checkout, old_sha)), 0)
            git("-C", bench, "-c", "fetch.fsckObjects=true", "fetch",
                "--quiet", "--no-tags", "--depth", "1", checkout, old_sha)
            git("-C", bench, "checkout", "--quiet", "--detach", old_sha)
            # Build #76 demonstrated a Git-version-specific full-fsck exit 1.
            # This fixture contains only generated data and file:// remotes;
            # expose *sanitized* Git diagnostics for that fixture ONLY.
            # Never do this with the real credential-bearing restore runner.
            full_check = subprocess.run(
                ["git", "-C", str(bench), "fsck",
                 "--full", "--strict", "--no-reflogs"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=15,
            )
            if full_check.returncode:
                pinned_check = subprocess.run(
                    ["git", "-C", str(bench), "fsck",
                     "--full", "--strict", "--no-reflogs", old_sha],
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, timeout=15,
                )
                git_version = git("--version")
                def safe_excerpt(value):
                    # No project contents, URLs, remote credentials or
                    # real backup metadata are read by this synthetic test.
                    return value.replace(str(root), "[synthetic-temp]").strip()[:600]
                self.fail(
                    "Synthetic Git full-fsck failed "
                    f"(exit={full_check.returncode}, git={git_version}); "
                    f"full stderr={safe_excerpt(full_check.stderr)!r}; "
                    f"pinned-fsck exit={pinned_check.returncode}, "
                    f"stderr={safe_excerpt(pinned_check.stderr)!r}"
                )
            self.assertEqual(git("-C", bench, "rev-parse", "HEAD"), old_sha)
            self.assertEqual((bench / "tracked7.txt").read_text(), "original-7\n")

    def test_github_source_probe_is_default_off_held_without_secret_context(self):
        self.assertIn(
            "run_integration2_git_source_probe:\n    type: boolean\n    default: false",
            self.config,
        )
        self.assertIn("opt-in-integration2-git-source-probe:", self.config)
        self.assertIn("pr32-git-source-probe-manual-approval:", self.config)
        self.assertIn("requires:\n            - pr32-git-source-probe-manual-approval", self.config)
        job = self.config.split("  pr32-git-source-probe:\n", 1)[1].split(
            "\nworkflows:", 1
        )[0]
        for unsafe in ("context:", "mariadb:", "redis:", "ssh ", "setup_remote_docker:",
                       "CCPC_RESTORE_B2_APP_KEY", "CCPC_RESTORE_AGE_IDENTITY",
                       "store_artifacts:", "persist_to_workspace:"):
            self.assertNotIn(unsafe, job)
        self.assertIn("python .circleci/ci_original_source_probe.py", job)
        self.assertIn("ci_source_hydration.py", self.stage)
        self.assertIn("hydrate_missing(checkout, ORIGINAL_SHA)",
                      (self.root / "ci_original_source_probe.py").read_text(encoding="utf8"))

    def test_github_source_probe_failure_classifiers_never_reflect_credentials(self):
        cases = (
            ("remote: warning: lazy fetching disabled; some objects may not be available", "promisor-missing-objects"),
            ("fatal: could not fetch abc from promisor remote", "promisor-missing-objects"),
            ("fatal: not our ref abc", "unadvertised-object"),
            ("server does not allow request for unadvertised object", "unadvertised-object"),
            ("fatal: shallow update not allowed", "shallow-boundary"),
            ("fatal: missing blob abc", "missing-objects"),
            ("fatal: Authentication failed for https://example.invalid/TOKEN", "authentication"),
            ("fatal: repository corruption on the remote side", "git-integrity"),
            ("fatal: some other problem with https://example.invalid/TOKEN", "other"),
        )
        for diagnostic, expected in cases:
            category = classify(diagnostic)
            self.assertEqual(category, expected)
            self.assertNotIn("TOKEN", category)
            self.assertNotIn("http", category)
            self.assertNotIn("abc", category)

    def test_exact_original_sha_source_and_git_fsck(self):
        self.assertIn("ORIGINAL_APP_SHA=0c223d7cbe8b27c2392d8b2303c45a99dd4d8b41", self.stage)
        self.assertIn("git -C apps/ccpcerp fsck --full --strict --no-reflogs", self.stage)
        self.assertIn("status --porcelain=v1 --untracked-files=no", self.stage)


if __name__ == "__main__":
    unittest.main()

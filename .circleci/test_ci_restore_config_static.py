"""Source-only restore CI safety checks. Does not invoke CircleCI or decrypt anything."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from ci_original_source_probe import classify
from ci_fresh_pinned_source import SourceFailure, prepare_fresh_source
from ci_source_hydration import missing_object_ids


BRANCH = "review/pr32-integration2-isolated-restore-2026-10-30"
CONTEXT = "ccpc-pr32-integration2-restore-once"


class RestoreCIStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent
        cls.config = (cls.root / "config.yml").read_text(encoding="utf8")
        cls.stage = (cls.root / "pr32_restore_stage.sh").read_text(encoding="utf8")
        cls.bench_smoke = (cls.root / "pr32_bench_version_smoke.sh").read_text(encoding="utf8")

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

    def test_final_restore_keeps_age_identity_only_in_python_and_not_children(self):
        # Build #92 failed in the final restore step: CI command had removed
        # the age identity that restore_once needs for decryption. The Python
        # process must receive it, while all B2 credentials stay absent.
        restore_lines = [
            line.strip() for line in self.config.splitlines()
            if "python .circleci/ci_restore_runtime.py --restore" in line
        ]
        self.assertEqual(len(restore_lines), 1)
        command = restore_lines[0]
        self.assertIn(
            "env -u CCPC_RESTORE_B2_KEY_ID -u CCPC_RESTORE_B2_APP_KEY "
            "timeout --signal=TERM --kill-after=10s 2400 "
            "python .circleci/ci_restore_runtime.py --restore",
            command,
        )
        self.assertNotIn("-u CCPC_RESTORE_AGE_IDENTITY", command)
        # Other installed dependency / bench stages remain age-identity free.
        self.assertGreaterEqual(
            self.config.count(
                "env -u CCPC_RESTORE_B2_KEY_ID -u CCPC_RESTORE_B2_APP_KEY "
                "-u CCPC_RESTORE_AGE_IDENTITY"
            ), 7,
        )
        runtime = (self.root / "ci_restore_runtime.py").read_text(encoding="utf8")
        self.assertIn('if not env.get("CCPC_RESTORE_AGE_IDENTITY"):', runtime)
        self.assertIn('payload, env["CCPC_RESTORE_AGE_IDENTITY"], scratch', runtime)
        self.assertIn('child_env.pop(secret_name, None)', runtime)

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

    def test_pinned_original_fetch_uses_fresh_scratch_and_strict_checks(self):
        helper = (self.root / "ci_fresh_pinned_source.py").read_text(encoding="utf8")
        # The inherited blobless checkout is never used as a Git fetch source
        # OR destination for historical content; only a disposable scratch
        # repository is used and fully checked before its bench swap.
        self.assertIn('FRESH_SOURCE_DIR="$(mktemp -d', self.stage)
        self.assertIn("ci_fresh_pinned_source.py", self.stage)
        self.assertIn('mv "$BENCH_DIR/apps/ccpcerp" "$BENCH_DIR/.ccpc-inherited-app-clone"', self.stage)
        self.assertIn('mv "$FRESH_SOURCE_DIR" "$BENCH_DIR/apps/ccpcerp"', self.stage)
        self.assertIn("timeout --signal=TERM --kill-after=5s 420", self.stage)
        self.assertIn("https://github.com/ccpcerp/ccpcerp.git", helper)
        self.assertIn('env["GIT_TERMINAL_PROMPT"] = "0"', helper)
        self.assertIn('env["GCM_INTERACTIVE"] = "Never"', helper)
        self.assertIn('"fetch.fsckObjects=true"', helper)
        self.assertIn('"--no-filter", "--depth", "1"', helper)
        self.assertIn('"--full", "--strict", "--no-reflogs"', helper)
        self.assertIn('"--missing=print"', helper)
        self.assertIn('"--untracked-files=no"', helper)
        self.assertIn('env.get("CIRCLE_JOB") != "integration2-backup-restore-isolated"', helper)
        self.assertNotIn('"$APP_CHECKOUT" "$ORIGINAL_APP_SHA"', self.stage)
        self.assertNotIn("git@github.com:", self.stage)
        self.assertNotIn("StrictHostKeyChecking=no", self.stage)
        self.assertNotIn("ssh-keyscan", self.stage)
        self.assertNotIn("credential.helper store", self.stage)
        self.assertNotIn("git -C apps/ccpcerp fetch --unshallow", self.stage)

    def test_bench_version_smoke_is_opt_in_held_and_no_secret_context(self):
        self.assertIn(
            "run_integration2_bench_version_smoke:\n    type: boolean\n    default: false",
            self.config,
        )
        workflow = self.config.split(
            "  opt-in-integration2-bench-version-smoke:\n", 1
        )[1].split("\n  # Hosted Git source-copy probe:", 1)[0]
        self.assertIn("when: << pipeline.parameters.run_integration2_bench_version_smoke >>", workflow)
        self.assertIn("pr32-bench-smoke-manual-approval:\n          type: approval", workflow)
        self.assertIn("requires:\n            - pr32-bench-smoke-manual-approval", workflow)
        self.assertEqual(
            workflow.count("only: " + BRANCH), 2,
            "Both approval and Bench job must be restricted to the review branch",
        )
        job = self.config.split("  pr32-bench-version-smoke:\n", 1)[1].split(
            "\n  pr32-git-source-probe:\n", 1
        )[0]
        self.assertIn("image: cimg/python:3.14-node", job)
        self.assertIn("resource_class: large", job)
        self.assertIn(
            "timeout --signal=TERM --kill-after=10s 1800 bash "
            ".circleci/pr32_bench_version_smoke.sh", job,
        )
        for forbidden in (
            "context:", "mariadb:", "redis:", "store_artifacts:",
            "store_test_results:", "persist_to_workspace:", "save_cache:",
            "setup_remote_docker:", "docker run", "ssh ", "CCPC_RESTORE_AGE_IDENTITY",
            "CCPC_RESTORE_B2_APP_KEY", "CCPC_RESTORE_B2_KEY_ID",
        ):
            self.assertNotIn(forbidden, job)

    def test_bench_version_smoke_pins_original_pair_and_never_uses_backup(self):
        script = self.bench_smoke
        for marker in (
            'CIRCLECI:-', 'CIRCLE_PROJECT_USERNAME:-', 'CIRCLE_PROJECT_REPONAME:-',
            'CIRCLE_BRANCH:-', 'CIRCLE_JOB:-', 'CIRCLE_SHA1:-',
            'CIRCLE_PIPELINE_ID:-', 'CIRCLE_WORKFLOW_ID:-', 'CIRCLE_BUILD_NUM:-',
            'CCPC_RESTORE_AGE_IDENTITY', 'CCPC_RESTORE_B2_KEY_ID',
            'CCPC_RESTORE_B2_APP_KEY', 'CCPC_ALLOW_INTEGRATION2_RESTORE',
            'TEST_SITE:-', 'CI_DB_ROOT_PASSWORD:-',
            'BENCH_DIR="$HOME/ccpc-pr32-version-smoke-bench"',
            '[[ ! -e "$BENCH_DIR" && ! -L "$BENCH_DIR" ]]',
            'umask 077', 'GIT_TERMINAL_PROMPT=0',
            'bench validate-dependencies',
            'grep -Fq "does not satisfy required version"',
            'NO TRANSIENT VERSION WARNING PASS',
            'CI-ONLY NO SITE NO BACKUP NO PROD/DEV',
        ):
            self.assertIn(marker, script)
        self.assertIn(
            'FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76', script
        )
        self.assertIn(
            'ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd', script
        )
        self.assertIn(
            "bench init --frappe-branch v16.33.1 --skip-assets", script
        )
        self.assertIn(
            "bench get-app --branch v16.34.2 --skip-assets erpnext", script
        )
        self.assertIn(
            '[[ "$(git -C "$BENCH_DIR/apps/frappe" rev-parse HEAD 2>/dev/null)" == "$FRAPPE_SHA" ]]',
            script,
        )
        self.assertIn(
            '[[ "$(git -C "$BENCH_DIR/apps/erpnext" rev-parse HEAD 2>/dev/null)" == "$ERPNEXT_SHA" ]]',
            script,
        )
        for forbidden in (
            "bench new-site", "bench restore", "bench migrate",
            "install-app ccpcerp", "mysql ", "mariadb ", "age --decrypt",
            "b2_authorize", "curl ", "wget ", "ssh ", "sudo docker",
        ):
            self.assertNotIn(forbidden, script)
        # The smoke must exercise the SAME pinned release tags as the actual
        # isolated restore Bench install, not another moving upstream branch.
        self.assertIn(
            "bench init --frappe-branch v16.33.1", self.stage
        )
        self.assertIn(
            "bench get-app --branch v16.34.2 erpnext", self.stage
        )

    def test_frappe_init_uses_exact_release_tag_before_dependency_install(self):
        # The Frappe v16.33.1 tag is confirmed upstream at FRAPPE_SHA;
        # never temporarily install a moving newer version-16 revision.
        self.assertIn(
            "FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76",
            self.stage,
        )
        self.assertIn(
            "bench init --frappe-branch v16.33.1 --skip-assets",
            self.stage,
        )
        self.assertNotIn(
            "bench init --frappe-branch version-16",
            self.stage,
        )
        init_at = self.stage.index("bench init --frappe-branch v16.33.1")
        tag_check_at = self.stage.index(
            '[[ "$(git -C apps/frappe rev-parse HEAD)" == "$FRAPPE_SHA" ]]'
        )
        fetch_at = self.stage.index(
            'git -C apps/frappe fetch --depth 1 upstream "$FRAPPE_SHA"'
        )
        checkout_at = self.stage.index(
            'git -C apps/frappe checkout --detach "$FRAPPE_SHA"'
        )
        final_verify_at = self.stage.index(
            '[[ "$(git -C apps/frappe rev-parse HEAD)" == "$FRAPPE_SHA" ]]',
            checkout_at,
        )
        self.assertLess(init_at, tag_check_at)
        self.assertLess(tag_check_at, fetch_at)
        self.assertLess(fetch_at, checkout_at)
        self.assertLess(checkout_at, final_verify_at)
        self.assertLess(final_verify_at, self.stage.index("bench setup requirements --dev"))

    def test_erpnext_installs_exact_compatible_tag_before_dependency_check(self):
        # Build #108 succeeded, but the initial floating version-16 get-app
        # temporarily read a newer ERPNext requirement (Frappe >=16.50.0).
        # GitHub verifies ERPNext v16.34.2 is exactly the pinned ERPNEXT_SHA
        # and its pyproject.toml requires Frappe >=16.21.0,<17.0.0.
        # Keep the original Frappe 16.33.1 / ERPNext 16.34.2 pair.
        self.assertIn(
            "FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76",
            self.stage,
        )
        self.assertIn(
            "ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd",
            self.stage,
        )
        self.assertIn(
            "bench get-app --branch v16.34.2 erpnext https://github.com/frappe/erpnext",
            self.stage,
        )
        self.assertNotIn(
            "bench get-app --branch version-16 erpnext",
            self.stage,
        )
        # Reject retargeted upstream tag, rather than hiding incompatibility
        # by resetting it to the expected pinned SHA after dependency install.
        clone_at = self.stage.index("bench get-app --branch v16.34.2 erpnext")
        tag_check_at = self.stage.index(
            '[[ "$(git -C apps/erpnext rev-parse HEAD)" == "$ERPNEXT_SHA" ]]'
        )
        fetch_at = self.stage.index(
            'git -C apps/erpnext fetch --depth 1 upstream "$ERPNEXT_SHA"'
        )
        checkout_at = self.stage.index(
            'git -C apps/erpnext checkout --detach "$ERPNEXT_SHA"'
        )
        final_verify_at = self.stage.index(
            '[[ "$(git -C apps/erpnext rev-parse HEAD)" == "$ERPNEXT_SHA" ]]',
            checkout_at,
        )
        self.assertLess(clone_at, tag_check_at)
        self.assertLess(tag_check_at, fetch_at)
        self.assertLess(fetch_at, checkout_at)
        self.assertLess(checkout_at, final_verify_at)
        self.assertLess(final_verify_at, self.stage.index("bench setup requirements --dev"))

    def test_fresh_repo_bypasses_inherited_partial_clone_missing_blobs(self):
        # Synthetic file:// test reproduces eight historical missing blobs,
        # then validates fresh repository fetch + bench swap (not remote CI).
        # No external network, CI credentials, Frappe, or DB operations.
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
            # Fresh repo has no inherited partial-clone/promisor state.
            fresh = root / "fresh"
            prior = root / "inherited-partial-clone"
            fresh.mkdir()
            with mock.patch.dict(os.environ, env):
                prepare_fresh_source(fresh, original.as_uri(), old_sha)
            bench.rename(prior)
            fresh.rename(bench)
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

    def test_fresh_source_refuses_nonempty_scratch(self):
        # No preexisting directory may be overwritten, even in a fixture.
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory) / "reserved"
            scratch.mkdir()
            (scratch / "keep.txt").write_text("must survive")
            with self.assertRaises(SourceFailure):
                prepare_fresh_source(scratch, "file:///not-contacted")
            self.assertEqual((scratch / "keep.txt").read_text(), "must survive")

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
        probe = (self.root / "ci_original_source_probe.py").read_text(encoding="utf8")
        self.assertIn("prepare_fresh_source(fresh, origin, ORIGINAL_SHA)", probe)
        self.assertIn("bench.rename(prior)", probe)
        self.assertIn("fresh.rename(bench)", probe)
        self.assertNotIn("hydrate_missing(checkout, ORIGINAL_SHA)", probe)

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

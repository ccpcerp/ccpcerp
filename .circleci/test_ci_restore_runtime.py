"""Offline synthetic-only tests. Never download ciphertext or restore a site."""
from __future__ import annotations

import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import ci_restore_runtime as r


def fixture(scope="public", name="example.txt", body=b"synthetic"):
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w:") as tf:
        base = f"sites/ccpc-integration2.localhost/{scope}/files"
        directory = tarfile.TarInfo(base)
        directory.type = tarfile.DIRTYPE
        tf.addfile(directory)
        file = tarfile.TarInfo(base + "/" + name)
        file.size = len(body)
        tf.addfile(file, io.BytesIO(body))
    return bio.getvalue()


class RestoreRuntimeTests(unittest.TestCase):
    def test_restore_phase_reports_safe_fixed_label_without_exception(self):
        # Synthetic-only: simulate a stage failure, never touch DB, B2 or age.
        from contextlib import redirect_stderr

        def synthetic_failure(*args, **kwargs):
            kwargs["diagnostic_phase"][0] = "SITE_CREATE"
            raise RuntimeError("SENSITIVE_EXCEPTION_SENTINEL")

        with patch.object(r.sys, "argv", ["ci_restore_runtime.py", "--restore"]), \
             patch.object(r, "_run", return_value=b"f" * 40), \
             patch.object(r, "restore_once", side_effect=synthetic_failure), \
             io.StringIO() as error_output, redirect_stderr(error_output):
            self.assertEqual(r.main(), 1)
            output = error_output.getvalue()
        self.assertIn("INTEGRATION2 DISPOSABLE RESTORE: FAIL (sanitized)", output)
        self.assertIn("INTEGRATION2 RESTORE FAILURE PHASE: SITE_CREATE", output)
        self.assertNotIn("SENSITIVE_EXCEPTION_SENTINEL", output)

    def test_decrypt_rejects_identity_format_before_disk_or_age(self):
        phases = []
        with tempfile.TemporaryDirectory() as t, \
             patch.object(r, "_run") as child, \
             patch.object(r.guard, "verify_ciphertext") as recheck:
            with self.assertRaises(RuntimeError):
                r.decrypt_and_validate(
                    b"synthetic-ciphertext", "not-an-age-identity", Path(t),
                    report_phase=phases.append,
                )
            self.assertFalse((Path(t) / "age-identity").exists())
            child.assert_not_called()
            recheck.assert_not_called()
        self.assertEqual(phases, ["AGE_IDENTITY"])

    def test_decrypt_identity_file_error_before_age(self):
        phases = []
        with tempfile.TemporaryDirectory() as t, \
             patch.object(r, "_write_secret", side_effect=PermissionError("secret")) as write, \
             patch.object(r, "_run") as child:
            with self.assertRaises(PermissionError):
                r.decrypt_and_validate(
                    b"synthetic", "AGE-SECRET-KEY-1SYNTHETIC", Path(t),
                    report_phase=phases.append,
                )
            write.assert_called_once()
            child.assert_not_called()
        self.assertEqual(phases, ["AGE_IDENTITY"])

    def test_decrypt_ciphertext_recheck_fails_closed_and_removes_identity(self):
        phases = []
        with tempfile.TemporaryDirectory() as t, \
             patch.object(r.guard, "verify_ciphertext",
                          side_effect=RuntimeError("synthetic-hash-reject")), \
             patch.object(r, "_run") as child:
            with self.assertRaises(RuntimeError):
                r.decrypt_and_validate(
                    b"synthetic", "AGE-SECRET-KEY-1SYNTHETIC", Path(t),
                    report_phase=phases.append,
                )
            self.assertFalse((Path(t) / "age-identity").exists())
            child.assert_not_called()
        self.assertEqual(phases, ["AGE_IDENTITY", "CIPHERTEXT_RECHECK"])

    def test_decrypt_age_tool_failure_removes_identity_without_archive_validation(self):
        phases = []
        with tempfile.TemporaryDirectory() as t, \
             patch.object(r.guard, "verify_ciphertext"), \
             patch.object(r, "_run", side_effect=RuntimeError("synthetic-age-error")) as child, \
             patch.object(r.guard, "validate_decrypted_tar") as validator:
            with self.assertRaises(RuntimeError):
                r.decrypt_and_validate(
                    b"synthetic", "AGE-SECRET-KEY-1SYNTHETIC", Path(t),
                    report_phase=phases.append,
                )
            self.assertFalse((Path(t) / "age-identity").exists())
            child.assert_called_once()
            validator.assert_not_called()
        self.assertEqual(
            phases, ["AGE_IDENTITY", "CIPHERTEXT_RECHECK", "AGE_TOOL_DECRYPT"]
        )

    def test_decrypt_archive_validation_failure_is_distinguished_from_age(self):
        phases = []
        with tempfile.TemporaryDirectory() as t, \
             patch.object(r.guard, "verify_ciphertext"), \
             patch.object(r, "_run", return_value=b"synthetic-not-a-tar"), \
             patch.object(r.guard, "validate_decrypted_tar",
                          side_effect=RuntimeError("synthetic-manifest-reject")) as validator:
            with self.assertRaises(RuntimeError):
                r.decrypt_and_validate(
                    b"synthetic", "AGE-SECRET-KEY-1SYNTHETIC", Path(t),
                    report_phase=phases.append,
                )
            self.assertFalse((Path(t) / "age-identity").exists())
            validator.assert_called_once_with(b"synthetic-not-a-tar")
        self.assertEqual(
            phases, ["AGE_IDENTITY", "CIPHERTEXT_RECHECK",
                     "AGE_TOOL_DECRYPT", "ARCHIVE_VALIDATE"]
        )

    def test_decrypt_synthetic_success_preserves_return_and_erases_identity(self):
        phases = []
        expected = ({"synthetic-sql": b"synthetic-content"}, "synthetic-crypto-key")
        with tempfile.TemporaryDirectory() as t, \
             patch.object(r.guard, "verify_ciphertext"), \
             patch.object(r, "_run", return_value=b"synthetic-tar-bytes") as child, \
             patch.object(r.guard, "validate_decrypted_tar", return_value=expected):
            result = r.decrypt_and_validate(
                b"synthetic", "AGE-SECRET-KEY-1SYNTHETIC", Path(t),
                report_phase=phases.append,
            )
            self.assertFalse((Path(t) / "age-identity").exists())
            self.assertEqual(result, expected)
            child.assert_called_once()
        self.assertEqual(
            phases, ["AGE_IDENTITY", "CIPHERTEXT_RECHECK",
                     "AGE_TOOL_DECRYPT", "ARCHIVE_VALIDATE"]
        )

    def test_restore_forwards_allowlisted_decrypt_phase_and_does_no_site_create(self):
        phases = ["ENTRY"]
        env = {
            "CI_DB_ROOT_PASSWORD": "ccpc-restore-disposable-db-only",
            "CCPC_RESTORE_AGE_IDENTITY": "AGE-SECRET-KEY-1SYNTHETIC",
        }

        def synthetic_failure(payload, identity, scratch, *, report_phase):
            report_phase("ARCHIVE_VALIDATE")
            raise RuntimeError("synthetic-archive-reject")

        with tempfile.TemporaryDirectory() as t, \
             patch.object(r.guard, "require_ci_identity"), \
             patch.object(r.benchguard, "attest_pinned_bench"), \
             patch.object(r, "_setup_db_client", return_value=Path(t) / "client"), \
             patch.object(r.dbguard, "attest_before_new_site"), \
             patch.object(r, "read_prefetched_ciphertext", return_value=b"synthetic"), \
             patch.object(r, "_new_private_dir"), \
             patch.object(r, "decrypt_and_validate", side_effect=synthetic_failure), \
             patch.object(r, "_run") as child:
            with self.assertRaises(RuntimeError):
                r.restore_once(
                    env, "f" * 40, Path(t), Path(t) / "bench",
                    diagnostic_phase=phases,
                )
            child.assert_not_called()
        self.assertEqual(phases, ["ARCHIVE_VALIDATE"])

    def test_restore_phase_rejects_unlisted_label_to_prevent_secret_leak(self):
        from contextlib import redirect_stderr

        def synthetic_failure(*args, **kwargs):
            kwargs["diagnostic_phase"][0] = "SECRET_VALUE_MUST_NOT_APPEAR"
            raise RuntimeError("SECRET_EXCEPTION_MUST_NOT_APPEAR")

        with patch.object(r.sys, "argv", ["ci_restore_runtime.py", "--restore"]), \
             patch.object(r, "_run", return_value=b"f" * 40), \
             patch.object(r, "restore_once", side_effect=synthetic_failure), \
             io.StringIO() as error_output, redirect_stderr(error_output):
            self.assertEqual(r.main(), 1)
            output = error_output.getvalue()
        self.assertIn("INTEGRATION2 RESTORE FAILURE PHASE: UNKNOWN", output)
        self.assertNotIn("SECRET_VALUE_MUST_NOT_APPEAR", output)
        self.assertNotIn("SECRET_EXCEPTION_MUST_NOT_APPEAR", output)

    def test_phase_attestation_fails_closed_at_ci_policy_with_no_io(self):
        phase = ["ENTRY"]
        with patch.object(r.guard, "require_ci_identity", side_effect=RuntimeError("private")):
            with self.assertRaises(RuntimeError):
                r.restore_once({}, "f" * 40, Path("/nonexistent"),
                               Path("/nonexistent/bench"), diagnostic_phase=phase)
        self.assertEqual(phase, ["CI_POLICY"])

    def test_subprocess_drops_secret_environment_without_removing_from_python(self):
        # Build #92 correction: the restore Python process needs the temporary
        # age identity, but no spawned child command may inherit it or B2 keys.
        import subprocess
        sentinel = {
            "CCPC_RESTORE_B2_KEY_ID": "synthetic-readonly-key",
            "CCPC_RESTORE_B2_APP_KEY": "synthetic-app-secret",
            "CCPC_RESTORE_AGE_IDENTITY": "synthetic-age-identity",
            "CCPC_TEST_KEEP": "expected",
        }
        with patch.dict(os.environ, sentinel), patch.object(
            r.subprocess, "run",
            return_value=subprocess.CompletedProcess(["git"], 0, b"ok"),
        ) as child:
            self.assertEqual(r._run(["git", "--version"]), b"ok")
            passed = child.call_args.kwargs["env"]
            for secret in (
                "CCPC_RESTORE_B2_KEY_ID", "CCPC_RESTORE_B2_APP_KEY",
                "CCPC_RESTORE_AGE_IDENTITY",
            ):
                self.assertNotIn(secret, passed)
                self.assertEqual(os.environ[secret], sentinel[secret])
            self.assertEqual(passed["CCPC_TEST_KEEP"], "expected")

    def test_four_part_selector_distinguishes_public_and_private(self):
        parts = {
            "prefix-files.tar": b"public",
            "prefix-private-files.tar": b"private",
        }
        self.assertEqual(r._file_component(parts, "-files.tar"), b"public")
        self.assertEqual(r._file_component(parts, "-private-files.tar"), b"private")

    def test_one_regular_public_file(self):
        entries = r._safe_tar_entries(fixture(), "public")
        self.assertEqual(entries[-1], (("example.txt",), b"synthetic"))

    def test_bench_source_prefix_is_accepted_but_wrong_site_rejected(self):
        # GNU tar archives may include the sanitized leading source path.
        original = fixture()
        old = b"sites/ccpc-integration2.localhost/public/files"
        expected = (("example.txt",), b"synthetic")
        for prefix in (b"workspace/development/frappe-bench/sites/",
                       b"ccpc-integration2.localhost/../sites/"):
            if b".." in prefix:
                continue
            with self.subTest(prefix=prefix):
                renamed = original.replace(b"sites/", prefix)
                # Only test a new synthetic tar generated with exact benign prefix.
                bio = io.BytesIO()
                with tarfile.open(fileobj=bio, mode="w:") as tf:
                    d = tarfile.TarInfo(prefix.decode() + "ccpc-integration2.localhost/public/files")
                    d.type = tarfile.DIRTYPE
                    tf.addfile(d)
                    entry = tarfile.TarInfo(d.name + "/example.txt")
                    entry.size = len(expected[1])
                    tf.addfile(entry, io.BytesIO(expected[1]))
                self.assertEqual(r._safe_tar_entries(bio.getvalue(), "public")[-1], expected)

        with self.assertRaises(RuntimeError):
            r._safe_tar_entries(fixture().replace(
                b"sites/ccpc-integration2.localhost",
                b"sites/other-site.localhost"), "public")

    def test_private_file_has_correct_scope(self):
        entries = r._safe_tar_entries(fixture("private"), "private")
        self.assertEqual(entries[-1][1], b"synthetic")
        with self.assertRaises(RuntimeError):
            r._safe_tar_entries(fixture("private"), "public")

    def test_traversal_and_wrong_origin_denied(self):
        for file in ("../secrets", "folder/../../secrets", "x\\\\y", "x/../y"):
            with self.subTest(file=file):
                with self.assertRaises(RuntimeError):
                    r._safe_tar_entries(fixture(name=file), "public")

    def test_wrong_archive_name_denied(self):
        with self.assertRaises(RuntimeError):
            r._safe_tar_entries(fixture(name="ok.txt"), "anything")

    def test_symlink_member_denied(self):
        bio = io.BytesIO()
        with tarfile.open(fileobj=bio, mode="w:") as tf:
            i = tarfile.TarInfo("sites/ccpc-integration2.localhost/public/files/x")
            i.type = tarfile.SYMTYPE
            i.linkname = "/etc/passwd"
            tf.addfile(i)
        with self.assertRaises(RuntimeError):
            r._safe_tar_entries(bio.getvalue(), "public")

    def test_exclusive_private_file(self):
        with tempfile.TemporaryDirectory() as t:
            dir = Path(t) / "scratch"
            r._new_private_dir(dir)
            file = dir / "secret"
            r._write_secret(file, b"synthetic")
            self.assertEqual(file.read_bytes(), b"synthetic")
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                r._write_secret(file, b"new")

    def test_private_attachment_copy_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as t:
            site = Path(t)
            (site / "private" / "files").mkdir(parents=True)
            entries = r._safe_tar_entries(fixture("private", "folder/dummy.txt"), "private")
            count = r._install_files(site, "private", entries)
            self.assertEqual(count, 1)
            self.assertEqual((site / "private/files/folder/dummy.txt").read_bytes(), b"synthetic")
            with self.assertRaises(RuntimeError):
                r._install_files(site, "private", entries)

    def test_bad_credential_denied_pre_setup(self):
        with patch.object(r.benchguard, "attest_pinned_bench") as bench:
            with self.assertRaises(RuntimeError):
                r.restore_once({}, "x" * 40, Path("/tmp"), Path("/tmp/test"))
            bench.assert_not_called()

    def test_early_prefetch_stores_encrypted_bytes_only_with_private_mode(self):
        with tempfile.TemporaryDirectory() as t:
            home = Path(t)
            env = {
                "CCPC_RESTORE_B2_KEY_ID": "synthetic-key-id",
                "CCPC_RESTORE_B2_APP_KEY": "synthetic-application-key",
                "CCPC_RESTORE_AGE_IDENTITY": "synthetic-no-private-key",
            }
            # No real network, DB, age or SQL operation. Hash gate is mocked
            # solely because this unit fixture is not the confidential backup.
            with patch.object(r.guard, "require_ci_identity") as identity, \
                 patch.object(r.guard, "verify_ciphertext") as verify, \
                 patch.object(r.b2, "fetch_exact_ciphertext", return_value=b"fake-age-ciphertext") as fetch, \
                 patch.object(r.guard, "CIPHERTEXT_BYTES", len(b"fake-age-ciphertext")):
                r.prefetch_ciphertext(env, "f" * 40, home)
                identity.assert_called_once()
                fetch.assert_called_once_with(
                    env["CCPC_RESTORE_B2_KEY_ID"], env["CCPC_RESTORE_B2_APP_KEY"]
                )
                self.assertGreaterEqual(verify.call_count, 1)
                stored = home / ".ccpc-restore-ciphertext" / "payload.age"
                self.assertEqual(stored.read_bytes(), b"fake-age-ciphertext")
                self.assertEqual(stored.stat().st_mode & 0o777, 0o600)
                self.assertEqual(stored.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(r.read_prefetched_ciphertext(home), b"fake-age-ciphertext")
                with self.assertRaises(RuntimeError):
                    r.prefetch_ciphertext(env, "f" * 40, home)

    def test_restore_refuses_missing_prefetched_ciphertext(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(RuntimeError):
                r.read_prefetched_ciphertext(Path(t))

    def test_missing_readonly_key_denied_before_http_and_scratch_creation(self):
        with tempfile.TemporaryDirectory() as t:
            home = Path(t)
            env = {"CCPC_RESTORE_AGE_IDENTITY": "synthetic-no-private-key"}
            with patch.object(r.guard, "require_ci_identity"), \
                 patch.object(r.b2, "fetch_exact_ciphertext") as fetch, \
                 self.assertRaisesRegex(RuntimeError, "credentials missing"):
                r.prefetch_ciphertext(env, "f" * 40, home)
            fetch.assert_not_called()
            self.assertFalse((home / ".ccpc-restore-ciphertext").exists())

    def test_no_unauthorized_invocation(self):
        with patch.object(r.sys, "argv", ["ci_restore_runtime.py"]):
            self.assertEqual(r.main(), 1)

    def test_source_has_no_bulk_file_extract_or_sensitive_logs(self):
        source = Path(r.__file__).read_text(encoding="utf8")
        for bad in ("extractall(", "tar xvf", "shell=True",
                    "store_artifacts", "print(verified)", "print(plaintext)"):
            self.assertNotIn(bad, source)


if __name__ == "__main__":
    unittest.main()

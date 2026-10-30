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
                "CCPC_RESTORE_OBJECT_GET_URL": "synthetic-not-a-real-url",
                "CCPC_RESTORE_AGE_IDENTITY": "synthetic-no-private-key",
            }
            # No real network, DB, age or SQL operation. Hash gate is mocked
            # solely because this unit fixture is not the confidential backup.
            with patch.object(r.guard, "require_ci_identity") as identity, \
                 patch.object(r.guard, "verify_ciphertext") as verify, \
                 patch.object(r, "fetch_exact_ciphertext", return_value=b"fake-age-ciphertext") as fetch, \
                 patch.object(r.guard, "CIPHERTEXT_BYTES", len(b"fake-age-ciphertext")):
                r.prefetch_ciphertext(env, "f" * 40, home)
                identity.assert_called_once()
                fetch.assert_called_once_with(env["CCPC_RESTORE_OBJECT_GET_URL"])
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

    def test_presigned_url_rejected_before_http(self):
        with patch.object(r, "build_opener") as opener:
            with self.assertRaises(RuntimeError):
                r.fetch_exact_ciphertext("https://public.example.org/secret.tar.age")
            opener.assert_not_called()

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

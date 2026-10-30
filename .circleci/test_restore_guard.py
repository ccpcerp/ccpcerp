# Copyright (c) 2026, CCPC and contributors
"""Source-only tests. No CircleCI, cloud access, secrets, site or DB needed."""
import hashlib
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import unittest

SRC = Path(__file__).with_name("restore_guard.py")
spec = importlib.util.spec_from_file_location("restore_guard", SRC)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

def good_env():
    return {
        "CIRCLECI": "true",
        "CIRCLE_PROJECT_USERNAME": "CCPCerp_2026",
        "CIRCLE_PROJECT_REPONAME": "ccpcerp",
        "CIRCLE_BRANCH": guard.BRANCH,
        "CIRCLE_JOB": guard.JOB,
        "TEST_SITE": guard.SITE,
        "CIRCLE_PIPELINE_ID": "pipeline-synthetic",
        "CIRCLE_WORKFLOW_ID": "workflow-synthetic",
        "CIRCLE_BUILD_NUM": "123",
        "CIRCLE_SHA1": "f" * 40,
        "CCPC_RESTORE_APPROVED_SHA": "f" * 40,
        "CCPC_ALLOW_INTEGRATION2_RESTORE": "approved-only",
        "CCPC_RESTORE_OWNER_DATA_EGRESS_APPROVED": "yes",
        "CCPC_RESTORE_SSH_DEBUGGING_DISABLED": "yes",
        "CI_DB_HOST": "127.0.0.1",
        "CI_DB_NAME": "ci_pr32_restore_20261030",
    }

def synthetic_tar(tamper=False):
    stem = "20260101_000000-ccpc-integration2_localhost"
    parts = {
        stem + "-database.sql.gz": b"synthetic SQL bytes",
        stem + "-site_config_backup.json": b'{"encryption_key":"synthetic-test-only"}',
        stem + "-files.tar": b"synthetic file TAR bytes",
        stem + "-private-files.tar": b"synthetic private TAR bytes",
    }
    rows = {
        name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in parts.items()
    }
    if tamper:
        rows[stem + "-files.tar"]["sha256"] = "0" * 64
    manifest = json.dumps({
        "site": "ccpc-integration2.localhost",
        "app_sha": guard.SOURCE_APP_SHA,
        "files": rows,
    }).encode("utf8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in [*parts.items(), ("SHA256-MANIFEST.json", manifest)]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()

class ReviewOnlyRestoreGuardTests(unittest.TestCase):
    def test_identity_happy_path_is_only_for_mocked_environment(self):
        guard.require_ci_identity(good_env(), "f" * 40)

    def test_rejects_missing_hosted_decryption_consent_and_ssh_policy(self):
        for var, bad in (
            ("CCPC_RESTORE_OWNER_DATA_EGRESS_APPROVED", ""),
            ("CCPC_RESTORE_SSH_DEBUGGING_DISABLED", ""),
            ("CIRCLE_SSH_ENABLED", "true"),
            ("CCPC_RESTORE_APPROVED_SHA", "0" * 40),
            ("CIRCLE_BRANCH", "review/four-day-one-reports-integration-2026-10-29"),
            ("CIRCLE_JOB", "four-reports-isolated-integration"),
            ("CIRCLE_PROJECT_REPONAME", "other"),
            ("CIRCLE_PIPELINE_ID", ""),
        ):
            with self.subTest(var=var):
                env = good_env()
                env[var] = bad
                with self.assertRaises(RuntimeError):
                    guard.require_ci_identity(env, "f" * 40)

    def test_rejects_checkout_sha_mismatch(self):
        with self.assertRaises(RuntimeError):
            guard.require_ci_identity(good_env(), "e" * 40)

    def test_rejects_real_dev_prod_and_unapproved_db_targets(self):
        guard.require_local_disposable_target(
            guard.SITE, "127.0.0.1", "ci_pr32_restore_20261030"
        )
        for site, host, db in (
            ("development.localhost", "127.0.0.1", "ci_pr32_restore_20261030"),
            ("ccpc-integration2.localhost", "127.0.0.1", "ci_pr32_restore_20261030"),
            (guard.SITE, "192.168.50.166", "ci_pr32_restore_20261030"),
            (guard.SITE, "127.0.0.1", "ccpc_production"),
        ):
            with self.subTest(site=site, host=host, db=db), self.assertRaises(RuntimeError):
                guard.require_local_disposable_target(site, host, db)

    def test_rejects_untrusted_or_public_download_paths(self):
        signed_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        accepted = (
            "https://" + guard.B2_HOST + guard.B2_OBJECT_PATH +
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Date=" + signed_date + "&X-Amz-Expires=300"
            "&X-Amz-Credential=synthetic%2F20261030%2Fus-west-004%2Fs3%2Faws4_request"
            "&X-Amz-SignedHeaders=host"
            "&X-Amz-Signature=" + "a" * 64
        )
        guard.validate_presigned_url(accepted)
        for bad in (
            accepted.replace("https://", "http://"),
            accepted.replace(guard.B2_HOST, "evil.example"),
            accepted.replace(".tar.age", ".sql.gz"),
            accepted.replace("?X-", "#fragment?X-"),
            "https://evil.example@" + guard.B2_HOST + guard.B2_OBJECT_PATH + "?token=x",
            accepted.replace("X-Amz-Expires=300", "X-Amz-Expires=3600"),
            accepted.replace("X-Amz-Signature=" + "a" * 64, "X-Amz-Signature=no"),
            accepted.replace("X-Amz-Date=" + signed_date, "X-Amz-Date=x"),
            accepted.replace("X-Amz-Date=" + signed_date, "X-Amz-Date=20200101T000000Z"),
            accepted.replace("X-Amz-SignedHeaders=host", "X-Amz-SignedHeaders=host%3Bx-extra"),
        ):
            with self.subTest(url=bad), self.assertRaises(RuntimeError):
                guard.validate_presigned_url(bad)

    def test_ciphertext_requires_exact_fixed_hash_and_size(self):
        for payload in (b"", b"age-encryption.org/v1\n", b"a" * guard.CIPHERTEXT_BYTES):
            with self.subTest(length=len(payload)), self.assertRaises(RuntimeError):
                guard.verify_ciphertext(payload)

    def test_validates_decrypted_five_member_tar_and_component_hashes(self):
        restored, key = guard.validate_decrypted_tar(synthetic_tar())
        self.assertEqual(len(restored), 4)
        self.assertEqual(key, "synthetic-test-only")

    def test_rejects_component_mismatch(self):
        with self.assertRaisesRegex(RuntimeError, "Decrypted archive validation failed"):
            guard.validate_decrypted_tar(synthetic_tar(tamper=True))

    def test_rejects_bad_tar_and_oversized_payload(self):
        for bad in (b"not a tar", b"x" * (32 * 1024 * 1024 + 1)):
            with self.assertRaises(RuntimeError):
                guard.validate_decrypted_tar(bad)

    def test_proposed_workflow_is_inert_even_if_accidentally_enabled(self):
        config = SRC.with_name("config-proposal.yml").read_text(encoding="utf8")
        self.assertIn("default: false", config)
        self.assertIn("type: approval", config)
        self.assertIn("exit 86", config)
        self.assertIn("requires:", config)
        self.assertIn("only: " + guard.BRANCH, config)
        self.assertNotIn("bench restore", config)
        self.assertNotIn("CCPC_RESTORE_AGE_IDENTITY_B64:", config)
        self.assertNotIn("save_cache:", config)
        self.assertNotIn("store_artifacts:", config)

    def test_no_network_database_or_restore_function_in_preflight_module(self):
        content = SRC.read_text(encoding="utf8")
        self.assertNotIn("bench restore", content)
        self.assertNotIn("requests.get(", content)
        self.assertNotIn("urllib.request.urlopen(", content)
        self.assertNotIn("subprocess.run([\"bench\"", content)
        self.assertNotIn("ssh-add", content)
        self.assertNotIn("docker.sock", content)

if __name__ == "__main__":
    unittest.main()

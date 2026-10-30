# Copyright (c) 2026, CCPC and contributors
"""REVIEW-ONLY proposed CircleCI isolated Integration2 backup-restore preflight.

This file is staged in the *documentation repository*, not the executable app CI
configuration. Import/test is safe; nothing performs data transfer or restore.
Never supply real secrets until owner separately approves hosted decryption.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
from urllib.parse import parse_qs, urlsplit

BRANCH = "review/pr32-integration2-isolated-restore-2026-10-30"
JOB = "integration2-backup-restore-isolated"
SITE = "ci-ccpc-pr32-restore.localhost"
SOURCE_APP_SHA = "0c223d7cbe8b27c2392d8b2303c45a99dd4d8b41"
FRAPPE_SHA = "988e54f3c4c291e2077a83809663f123731abe76"
ERPNEXT_SHA = "4048fb70e14d1843956fcdabb7c3cca75a1cbcdd"
CIPHERTEXT_SHA256 = "92fe1ba22fa6f1ae53828cf6da5276c6d7a7310f8b99be74a33bca0939911560"
CIPHERTEXT_BYTES = 1_137_112
B2_HOST = "s3.us-west-004.backblazeb2.com"
B2_OBJECT_PATH = (
    "/ccpc-ci-restore-20261030/integration2-restore-20261030/"
    "integration2-restore-20261030-85ff2c424e4cb5cf.tar.age"
)
SHA40 = re.compile(r"[0-9a-f]{40}\Z")

def require_ci_identity(env: dict[str, str], checked_out_sha: str) -> None:
    """Fail before package installs, downloads, database or scratch operations."""
    expected = {
        "CIRCLECI": "true",
        "CIRCLE_PROJECT_USERNAME": "CCPCerp_2026",
        "CIRCLE_PROJECT_REPONAME": "ccpcerp",
        "CIRCLE_BRANCH": BRANCH,
        "CIRCLE_JOB": JOB,
        "TEST_SITE": SITE,
        "CCPC_ALLOW_INTEGRATION2_RESTORE": "approved-only",
    }
    if any(env.get(k) != v for k, v in expected.items()):
        raise RuntimeError("CI project/branch/job/site identity mismatch")
    if not all(env.get(k) for k in ("CIRCLE_PIPELINE_ID", "CIRCLE_WORKFLOW_ID", "CIRCLE_BUILD_NUM")):
        raise RuntimeError("CircleCI workflow identity absent")
    approved = env.get("CCPC_RESTORE_APPROVED_SHA", "")
    actual = env.get("CIRCLE_SHA1", "")
    if (not SHA40.fullmatch(approved) or not SHA40.fullmatch(actual)
            or approved != actual or checked_out_sha != actual):
        raise RuntimeError("Reviewed exact-SHA approval required")
    # Context restrictions must *also* deny SSH job debugging at CircleCI org level;
    # these flags are an additional fail-closed check, not the primary control.
    if env.get("CCPC_RESTORE_OWNER_DATA_EGRESS_APPROVED") != "yes":
        raise RuntimeError("Third-party hosted decryption not authorized")
    if env.get("CCPC_RESTORE_SSH_DEBUGGING_DISABLED") != "yes":
        raise RuntimeError("SSH debugging policy gate not acknowledged")
    if env.get("CIRCLE_SSH_ENABLED", "").lower() in ("true", "1", "yes"):
        raise RuntimeError("CircleCI SSH debug session not allowed")

def checked_out_sha(repo: Path = Path(".")) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, text=True, capture_output=True,
    )
    return result.stdout.strip()

def require_local_disposable_target(site: str, db_host: str, db_name: str) -> None:
    if site != SITE or db_host != "127.0.0.1":
        raise RuntimeError("Only the isolated CI site and loopback DB host are allowed")
    if db_name != "ci_pr32_restore_20261030":
        raise RuntimeError("Unexpected disposable MariaDB database name")
    forbidden = ("development", "production", "ccpc-integration2")
    if any(word in db_name.lower() for word in forbidden):
        raise RuntimeError("CCPC existing database name forbidden")

def validate_presigned_url(url: str) -> None:
    """Require the exact single private object and short-lived AWS SigV4 GET.

    URL is a bearer secret: do not print it or include it in exceptions.
    """
    try:
        p = urlsplit(url)
        valid_origin = (
            p.scheme == "https"
            and p.hostname == B2_HOST
            and p.netloc == B2_HOST
            and p.path == B2_OBJECT_PATH
            and bool(p.query)
            and not p.fragment
            and not p.username
            and not p.password
            and p.port is None
        )
        q = parse_qs(p.query, keep_blank_values=True)
        expiry = q.get("X-Amz-Expires", [])
        algorithm = q.get("X-Amz-Algorithm", [])
        signature = q.get("X-Amz-Signature", [])
        date = q.get("X-Amz-Date", [])
        credential = q.get("X-Amz-Credential", [])
        headers = q.get("X-Amz-SignedHeaders", [])
        allowed = {
            "X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Date",
            "X-Amz-Expires", "X-Amz-SignedHeaders", "X-Amz-Signature",
            "X-Amz-Security-Token", "X-Amz-Content-Sha256",
        }
        timestamp = (datetime.strptime(date[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                     if len(date) == 1 and re.fullmatch(r"\d{8}T\d{6}Z", date[0])
                     else None)
        now = datetime.now(timezone.utc)
        ttl = int(expiry[0]) if len(expiry) == 1 and expiry[0].isdecimal() else -1
        signed = (
            valid_origin
            and set(q).issubset(allowed)
            and algorithm == ["AWS4-HMAC-SHA256"]
            and 0 < ttl <= 900
            and timestamp is not None
            and now <= timestamp + timedelta(seconds=ttl)
            and timestamp <= now + timedelta(seconds=30)
            and len(signature) == 1
            and bool(re.fullmatch(r"[0-9a-fA-F]{64}", signature[0]))
            and len(credential) == 1
            and bool(credential[0])
            and headers == ["host"]
        )
    except (TypeError, ValueError, AttributeError):
        signed = False
    if not signed:
        raise RuntimeError("Untrusted, unsigned or overlong encrypted-backup URL")

def verify_ciphertext(payload: bytes) -> None:
    if len(payload) != CIPHERTEXT_BYTES:
        raise RuntimeError("Unexpected encrypted-backup length")
    if hashlib.sha256(payload).hexdigest() != CIPHERTEXT_SHA256:
        raise RuntimeError("Encrypted-backup checksum mismatch")
    if not payload.startswith(b"age-encryption.org/v1\n"):
        raise RuntimeError("Expected age ciphertext header")

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def validate_decrypted_tar(data: bytes) -> tuple[dict[str, bytes], str]:
    """Validate 4 component bytes and embedded manifest; do not extract paths.

    Caller must decrypt into ephemeral protected memory first. Returned bytes
    remain confidential and MUST NOT be printed/artifacted/cached/uploaded.
    """
    if len(data) > 32 * 1024 * 1024:
        raise RuntimeError("Oversized decrypted backup")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            members = archive.getmembers()
            names = [m.name for m in members]
            if len(members) != 5 or len(set(names)) != 5:
                raise ValueError("Unexpected or duplicate archive members")
            if any(not m.isfile() or "/" in m.name or "\\" in m.name
                   or m.name.startswith(".") for m in members):
                raise ValueError("Nonregular or path-containing archive member")
            manifest_member = archive.getmember("SHA256-MANIFEST.json")
            manifest = json.loads(archive.extractfile(manifest_member).read())
            if manifest.get("site") != "ccpc-integration2.localhost":
                raise ValueError("Incorrect backup site identity")
            if manifest.get("app_sha") != SOURCE_APP_SHA:
                raise ValueError("Incorrect original app source commit")
            records = manifest.get("files")
            if not isinstance(records, dict) or len(records) != 4:
                raise ValueError("Four-file manifest required")
            expected_stems = [
                n.removesuffix("-database.sql.gz") for n in records
                if n.endswith("-database.sql.gz")
            ]
            if len(expected_stems) != 1 or not expected_stems[0].endswith(
                "-ccpc-integration2_localhost"
            ):
                raise ValueError("Incorrect backed-up source site")
            stem = expected_stems[0]
            expected = {
                stem + "-database.sql.gz",
                stem + "-site_config_backup.json",
                stem + "-files.tar",
                stem + "-private-files.tar",
            }
            if set(records) != expected or set(names) != expected | {"SHA256-MANIFEST.json"}:
                raise ValueError("Archive/manifest member mismatch")
            validated = {}
            for name in expected:
                record = records[name]
                if not isinstance(record, dict) or not isinstance(record.get("size"), int):
                    raise ValueError("Invalid component size field")
                if not isinstance(record.get("sha256"), str) or not re.fullmatch(
                    r"[0-9a-f]{64}", record["sha256"]
                ):
                    raise ValueError("Invalid component checksum")
                component = archive.extractfile(archive.getmember(name)).read()
                if len(component) != record["size"] or _sha256(component) != record["sha256"]:
                    raise ValueError("Original backup component checksum mismatch")
                validated[name] = component
            # Only the encryption key may be selectively used from original
            # site_config; do not install original DB/network configuration.
            config_name = stem + "-site_config_backup.json"
            config = json.loads(validated[config_name])
            key = config.get("encryption_key")
            if not isinstance(key, str) or not key:
                raise ValueError("Required site encryption key unavailable")
            return validated, key
    except (OSError, KeyError, TypeError, ValueError, tarfile.TarError) as exc:
        # Never log manifest, SQL, site config, file names or secrets.
        raise RuntimeError("Decrypted archive validation failed") from None

def fixed_preflight_status() -> str:
    return "CI IDENTITIES / ISOLATED TARGET / ENCRYPTED BACKUP INTEGRITY: REVIEW GATES"

if __name__ == "__main__":
    # Source-only check intentionally cannot initiate any network/SQL operation.
    require_ci_identity(os.environ, checked_out_sha())
    require_local_disposable_target(
        os.environ.get("TEST_SITE", ""), os.environ.get("CI_DB_HOST", ""),
        os.environ.get("CI_DB_NAME", ""),
    )
    print(fixed_preflight_status())

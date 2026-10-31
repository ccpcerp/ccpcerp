# Copyright (c) 2026, CCPC and contributors
"""Fail-closed CI identity, immutable backup and source archive guards.

Imported by the manually held disposable Integration2 restore; this module
itself performs no downloads, decryption or database operations. The protected
CircleCI Context must still be restricted by repo, branch and no-SSH policy.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import zlib

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

def verify_ciphertext(payload: bytes) -> None:
    if len(payload) != CIPHERTEXT_BYTES:
        raise RuntimeError("Unexpected encrypted-backup length")
    if hashlib.sha256(payload).hexdigest() != CIPHERTEXT_SHA256:
        raise RuntimeError("Encrypted-backup checksum mismatch")
    if not payload.startswith(b"age-encryption.org/v1\n"):
        raise RuntimeError("Expected age ciphertext header")

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _auth_encrypted_flags(values: bytes) -> bool:
    """Parse only the fifth column of each standard MariaDB __Auth tuple.

    Do not decode or emit any password field. Any unknown grammar fails closed.
    """
    values = values.strip()
    if not values.endswith(b";"):
        raise ValueError("Unrecognized auth rows")
    values = values[:-1].strip()
    if not values:
        raise ValueError("Empty auth insert")
    i = 0
    encrypted = False
    while i < len(values):
        if values[i:i + 1] != b"(":
            raise ValueError("Unrecognized auth tuple")
        i += 1
        commas = 0
        last_field = i
        quote = 0
        closed = False
        while i < len(values):
            c = values[i]
            if quote:
                if c == 92:  # MySQL backslash-escaped character inside a string.
                    i += 2
                    if i > len(values):
                        raise ValueError("Invalid SQL escape")
                    continue
                if c == quote:
                    if i + 1 < len(values) and values[i + 1] == quote:
                        i += 2
                        continue
                    quote = 0
            elif c in (34, 39):
                quote = c
            elif c == 44:
                commas += 1
                if commas > 4:
                    raise ValueError("Too many Auth columns")
                if commas == 4:
                    last_field = i + 1
            elif c == 41:
                if commas != 4:
                    raise ValueError("Unexpected Auth column count")
                flag = values[last_field:i].strip()
                if flag not in (b"0", b"1"):
                    raise ValueError("Invalid encrypted Auth flag")
                encrypted |= flag == b"1"
                i += 1
                closed = True
                break
            elif c == 40:
                raise ValueError("Unquoted Auth parenthesis")
            i += 1
        if not closed:
            raise ValueError("Unclosed Auth tuple")
        while i < len(values) and values[i] in b" \t\r\n":
            i += 1
        if i < len(values):
            if values[i:i + 1] != b",":
                raise ValueError("Unrecognized Auth separator")
            i += 1
            while i < len(values) and values[i] in b" \t\r\n":
                i += 1
            if i >= len(values):
                raise ValueError("Trailing Auth separator")
    return encrypted


def no_encrypted_auth_entries(sql_gz: bytes) -> bool:
    """Fail closed unless the complete original SQL proves no encrypted __Auth.

    Used only for the original, manifest-verified backup with no site key.
    It does NOT attest arbitrary third-party/custom encryption.
    The SQL is read in bounded memory, never written or logged.
    """
    total = 0
    seen_schema = False
    schema_complete = False
    collecting = False
    columns = []
    encrypted = False
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(sql_gz)) as source:
            while line := source.readline(32 * 1024 * 1024 + 1):
                total += len(line)
                if len(line) > 32 * 1024 * 1024 or total > 256 * 1024 * 1024:
                    raise ValueError("Oversized SQL dump")
                clean = line.lstrip()
                if clean.startswith(b"CREATE TABLE `__Auth`"):
                    if seen_schema or clean.rstrip() != b"CREATE TABLE `__Auth` (":
                        raise ValueError("Unrecognized Auth schema")
                    seen_schema = True
                    collecting = True
                    continue
                if collecting:
                    if clean.startswith(b")"):
                        collecting = False
                        schema_complete = True
                    elif match := re.match(rb"`([a-zA-Z_]+)`\s+", clean):
                        columns.append(match.group(1))
                    continue
                action_prefix = clean[:160].upper()
                if (action_prefix.startswith((b"INSERT ", b"REPLACE ", b"UPDATE ",
                                              b"LOAD DATA"))
                        and (b"__AUTH" in action_prefix
                             or action_prefix.startswith(b"LOAD DATA"))):
                    prefix = b"INSERT INTO `__Auth` VALUES "
                    if (not schema_complete or columns != [
                        b"doctype", b"name", b"fieldname", b"password", b"encrypted"
                    ] or not clean.startswith(prefix)):
                        raise ValueError("Unrecognized Auth SQL")
                    encrypted |= _auth_encrypted_flags(clean[len(prefix):])
    except (OSError, EOFError, zlib.error):
        raise ValueError("Invalid SQL gzip") from None
    if not schema_complete or columns != [
        b"doctype", b"name", b"fieldname", b"password", b"encrypted"
    ]:
        raise ValueError("Missing or unknown Auth schema")
    return not encrypted


def validate_decrypted_tar(data: bytes) -> tuple[dict[str, bytes], str | None]:
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
            if "encryption_key" in config:
                key = config["encryption_key"]
                if not isinstance(key, str) or not key:
                    raise ValueError("Invalid original site encryption key")
            else:
                # No original key: only a standard, parseable SQL dump with
                # zero encrypted Frappe __Auth rows can use the CI-local key.
                db_name = stem + "-database.sql.gz"
                if not no_encrypted_auth_entries(validated[db_name]):
                    raise ValueError("Original encrypted credentials require original key")
                key = None
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

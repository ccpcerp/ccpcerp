# Copyright (c) 2026, CCPC and contributors
"""REVIEW-ONLY local MariaDB provenance gates. No automatic CI or restore action.

The only subprocess is a read-only, fixed SQL query via a pinned local MariaDB
client; no site restore, DDL, data import, or external network calls.

A real hosted rehearsal additionally requires approved CircleCI sidecar/process
isolation; localhost, server fingerprint, and job env are not cryptographic
proof against a compromised runner. Unit tests mock the client.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess

import restore_guard as guard

DB_NAME = "ci_pr32_restore_20261030"
DB_USER = "ci_pr32_restore_user"
DB_HOST = "127.0.0.1"
DB_PORT = 3306
SITE = guard.SITE

# Constant SQL -- no user-supplied identifiers or credentials enter SQL.
# db name matches the pre-approved, isolated CI database literal only.
PROBE_SQL = (
    "SELECT @@hostname, @@version, @@port, @@global.server_id, "
    "@@global.read_only, CURRENT_USER(), "
    "(SELECT COUNT(*) FROM information_schema.SCHEMATA "
    "WHERE SCHEMA_NAME='ci_pr32_restore_20261030')"
)

@dataclass(frozen=True)
class AbsentDatabaseProof:
    """Ephemeral in-process proof; never persist to artifacts or logs."""
    server_fingerprint: tuple[str, str, str]
    circle_workflow_id: str
    approved_checkout_sha: str


def _owner_file(path: Path, *, strict_parent: bool = True) -> None:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Expected isolated owner-only file")
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError("Unsafe isolated file ownership or permissions")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise RuntimeError("Unsafe isolated parent directory")
    folder = path.parent.stat()
    mode = stat.S_IMODE(folder.st_mode)
    if folder.st_uid != os.geteuid() or (strict_parent and mode != 0o700) or mode & 0o022:
        raise RuntimeError("Unsafe isolated parent directory permissions")


def _same_isolated_site(bench: Path) -> None:
    if bench.is_symlink() or not bench.is_dir() or bench.stat().st_uid != os.geteuid():
        raise RuntimeError("Isolated bench does not exist or has wrong owner")
    sites = bench / "sites"
    site = sites / SITE
    if (sites.is_symlink() or not sites.is_dir() or
        site.is_symlink() or not site.is_dir() or
        sites.stat().st_uid != os.geteuid() or site.stat().st_uid != os.geteuid()):
        raise RuntimeError("Unexpected or linked isolated site path")
    conf = site / "site_config.json"
    _owner_file(conf, strict_parent=False)
    try:
        settings = json.loads(conf.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError("Expected site config mapping")
        if (settings.get("db_name") != DB_NAME or
            settings.get("db_user") != DB_USER or
            settings.get("db_host") != DB_HOST or
            settings.get("db_port") != DB_PORT or
            not isinstance(settings.get("db_password"), str) or
            not settings["db_password"]):
            raise ValueError("Unexpected site DB destination")
        if settings.get("db_type", "mariadb") != "mariadb":
            raise ValueError("Unexpected database backend")
        if any(key in settings for key in (
            "redis_cache", "redis_queue", "redis_socketio", "host_name", "mail_server",
            "db_socket", "mariadb_socket", "mysql_socket", "db_driver"
        )):
            # Source config should never be copied wholesale onto the new site.
            raise ValueError("Unexpected site-specific external integration settings")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError, KeyError):
        # Never print the source config or DB password.
        raise RuntimeError("Disposable site configuration not proven") from None


def _local_mariadb_probe(option_file: Path, mariadb: Path) -> tuple[tuple[str, str, str], int]:
    """Actually query local 3306 using a protected disposable CI-only client file.

    `--defaults-file` must be the FIRST client argument to exclude the host's
    other default option files. Call only inside an approved CircleCI job.
    """
    _owner_file(option_file)
    if (mariadb.is_symlink() or not mariadb.is_file() or
        str(mariadb) != "/usr/bin/mariadb"):
        raise RuntimeError("Unexpected MariaDB client executable")
    command = [
        str(mariadb), f"--defaults-file={option_file}", "--protocol=TCP",
        "--host=127.0.0.1", "--port=3306", "--connect-timeout=5",
        "--batch", "--raw", "--skip-column-names", "--silent",
        "--execute", PROBE_SQL,
    ]
    try:
        proc = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=12, check=True,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"},
        )
        if len(proc.stdout) > 1024:
            raise ValueError("Unexpected output length")
        lines = proc.stdout.strip().splitlines()
        if len(lines) != 1:
            raise ValueError("Expected exactly one MariaDB probe row")
        fields = lines[0].split("\t")
        if len(fields) != 7:
            raise ValueError("Unexpected MariaDB probe shape")
        hostname, version, port, server_id, read_only, account, count = fields
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", hostname) or
            any(x in hostname.lower() for x in ("ccpcerpserver", "production", "development")) or
            "MariaDB" not in version or
            port != "3306" or not server_id.isdecimal() or
            read_only != "0" or
            not account.startswith("root@") or
            count not in ("0", "1")):
            raise ValueError("Unexpected MariaDB instance identity")
        return (hostname, version, server_id), int(count)
    except (OSError, subprocess.SubprocessError, TypeError, ValueError, UnicodeError):
        # Client stderr can contain hostnames/usernames; never emit original.
        raise RuntimeError("Isolated MariaDB sidecar query or identity check failed") from None


def attest_before_new_site(env: dict[str, str], checkout_sha: str, *,
                           option_file: Path, mariadb: Path = Path("/usr/bin/mariadb")) -> AbsentDatabaseProof:
    """Read-only baseline: exact CI identity + never-before-used scratch DB."""
    guard.require_ci_identity(env, checkout_sha)
    guard.require_local_disposable_target(SITE, DB_HOST, DB_NAME)
    fingerprint, count = _local_mariadb_probe(option_file, mariadb)
    if count != 0:
        raise RuntimeError("Disposable database already exists before new-site")
    return AbsentDatabaseProof(fingerprint, env["CIRCLE_WORKFLOW_ID"], checkout_sha)


def attest_before_restore(env: dict[str, str], checkout_sha: str, proof: AbsentDatabaseProof,
                          *, option_file: Path, bench: Path,
                          mariadb: Path = Path("/usr/bin/mariadb")) -> None:
    """Read-only second gate; never runs a destructive restore itself."""
    guard.require_ci_identity(env, checkout_sha)
    guard.require_local_disposable_target(SITE, DB_HOST, DB_NAME)
    if (type(proof) is not AbsentDatabaseProof or
        proof.circle_workflow_id != env["CIRCLE_WORKFLOW_ID"] or
        proof.approved_checkout_sha != checkout_sha):
        raise RuntimeError("Fresh isolated job preflight proof absent")
    _same_isolated_site(bench)
    fingerprint, count = _local_mariadb_probe(option_file, mariadb)
    if fingerprint != proof.server_fingerprint or count != 1:
        raise RuntimeError("Isolated MariaDB server changed or scratch DB missing")

# Deliberately no `__main__`, no call to bench, no SQL writes, no CI config activation.

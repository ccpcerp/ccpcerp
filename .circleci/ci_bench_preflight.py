# Copyright (c) 2026, CCPC and contributors
"""Read-only pinned Frappe bench verification for the PR32 restore review.

Review artifact only; it never creates a site, imports SQL or connects to DB.
Compose with restore_guard.require_ci_identity and the two-phase MariaDB
attestations in one separately approved, disposable CircleCI job.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess

BENCH_NAME = "ccpc-pr32-restore-bench"
SITE = "ci-ccpc-pr32-restore.localhost"
APP_REVISIONS = {
    "frappe": "988e54f3c4c291e2077a83809663f123731abe76",
    "erpnext": "4048fb70e14d1843956fcdabb7c3cca75a1cbcdd",
    # Original Integration2 snapshot's application source, NOT PR32 HEAD.
    "ccpcerp": "0c223d7cbe8b27c2392d8b2303c45a99dd4d8b41",
}
LOCAL_SERVICES = {
    "redis_cache": "redis://127.0.0.1:13000",
    "redis_queue": "redis://127.0.0.1:11000",
    "redis_socketio": "redis://127.0.0.1:11000",
}


def _owned_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("Disposable bench directory missing or linked")
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise RuntimeError("Disposable bench directory ownership/mode rejected")


def _read_git_head(path: Path) -> str:
    """Exact commit identity without shell, network or credential-bearing logs."""
    try:
        proc = subprocess.run(
            ["/usr/bin/git", "-C", str(path), "rev-parse", "--verify", "HEAD"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, text=True, timeout=8,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
                 "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
        )
        value = proc.stdout.strip()
        if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("not a full revision")
        return value
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        raise RuntimeError("Pinned bench Git identity could not be proven") from None


def attest_pinned_bench(home: Path, bench: Path, *, site_created: bool) -> None:
    """Verify exact source revisions, local services and site presence.

    This function does NOT attest network/container isolation or allow restore.
    Invoke before new-site with site_created=False, and again only after a
    separate controlled new-site operation with site_created=True.
    """
    if type(site_created) is not bool:
        raise RuntimeError("Explicit bench phase is required")
    if not home.is_absolute() or home.is_symlink() or not home.is_dir():
        raise RuntimeError("Unexpected CircleCI home")
    if bench != home / BENCH_NAME or not bench.is_absolute():
        raise RuntimeError("Bench is outside designated disposable home")
    _owned_directory(home)
    _owned_directory(bench)
    apps = bench / "apps"
    sites = bench / "sites"
    _owned_directory(apps)
    _owned_directory(sites)

    for name, expected_sha in APP_REVISIONS.items():
        app = apps / name
        _owned_directory(app)
        if _read_git_head(app) != expected_sha:
            raise RuntimeError("Pinned Frappe/ERPNext/CCPC source revision mismatch")

    common = sites / "common_site_config.json"
    if common.is_symlink() or not common.is_file():
        raise RuntimeError("Isolated global bench config absent or linked")
    st = common.stat()
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o022:
        raise RuntimeError("Global bench config permissions rejected")
    try:
        config = json.loads(common.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("Invalid global config mapping")
        if any(config.get(k) != v for k, v in LOCAL_SERVICES.items()):
            raise ValueError("Redis must use only the isolated local sidecars")
        if config.get("db_host", "127.0.0.1") != "127.0.0.1":
            raise ValueError("Unexpected global DB host")
        if config.get("db_port", 3306) != 3306:
            raise ValueError("Unexpected global DB port")
        if config.get("db_type", "mariadb") != "mariadb":
            raise ValueError("Unexpected global DB backend")
        if any(k in config for k in ("db_name", "db_user", "db_password", "db_socket", "mariadb_socket", "mysql_socket", "host_name", "mail_server")):
            raise ValueError("Global config contains site/remote integration details")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        # Do not print configuration values, filenames from the backup or secrets.
        raise RuntimeError("Isolated local bench configuration not proven") from None

    site = sites / SITE
    if site.is_symlink() or site.exists() != site_created:
        raise RuntimeError("Scratch site presence does not match expected phase")
    if site_created:
        _owned_directory(site)

# No __main__, no side-effect on import, no downloads or writes.

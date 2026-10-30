# Copyright (c) 2026, CCPC and contributors
"""Synthetic-only one-job CircleCI sidecar smoke; NO backup/restore/decryption.

Runs ONLY inside explicitly held, disposable CI. Both real MariaDB reads occur
in one process around creation of a fresh scratch Frappe site. This is not a
production or DEVELOPMENT script.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

import ci_bench_preflight as benchguard
import ci_db_attestation as db
import restore_guard as restore

BRANCH = "review/pr32-integration2-smoke-2026-10-30"
JOB = "integration2-sidecar-smoke-isolated"
CHECKOUT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


def require_smoke_identity(env: dict[str, str], head: str) -> None:
    expected = {
        "CIRCLECI": "true",
        "CIRCLE_PROJECT_USERNAME": "CCPCerp_2026",
        "CIRCLE_PROJECT_REPONAME": "ccpcerp",
        "CIRCLE_BRANCH": BRANCH,
        "CIRCLE_JOB": JOB,
        "TEST_SITE": db.SITE,
        "CCPC_NO_BACKUP_SMOKE": "disposable-only",
    }
    if any(env.get(k) != value for k, value in expected.items()):
        raise RuntimeError("Disposable smoke CI identity mismatch")
    if not all(env.get(k) for k in ("CIRCLE_PIPELINE_ID", "CIRCLE_WORKFLOW_ID", "CIRCLE_BUILD_NUM")):
        raise RuntimeError("Disposable smoke workflow identity absent")
    approved = env.get("CCPC_SMOKE_APPROVED_SHA", "")
    if (not CHECKOUT_SHA_PATTERN.fullmatch(approved) or
        env.get("CIRCLE_SHA1") != approved or head != approved):
        raise RuntimeError("Exact reviewed smoke SHA not approved")
    if env.get("CIRCLE_SSH_ENABLED", "").lower() in ("true", "yes", "1"):
        raise RuntimeError("SSH debug mode forbidden for disposable smoke")


def _head() -> str:
    result = subprocess.run(
        ["/usr/bin/git", "rev-parse", "--verify", "HEAD"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, text=True, timeout=8,
    )
    return result.stdout.strip()


def _smoke_db_options(folder: Path, password: str) -> Path:
    # Never accept a credential file supplied by outside callers.
    if folder.exists() or folder.is_symlink():
        raise RuntimeError("Fresh smoke credential folder must not already exist")
    folder.mkdir(mode=0o700)
    option_file = folder / "db-client.cnf"
    with option_file.open("x", encoding="utf8") as handle:
        handle.write("[client]\nuser=root\npassword=" + password + "\n")
    option_file.chmod(0o600)
    db._owner_file(option_file)
    return option_file


def smoke_create_and_attest(env: dict[str, str], checkout_sha: str,
                            home: Path, bench: Path) -> None:
    require_smoke_identity(env, checkout_sha)
    if env.get("CI_DB_ROOT_PASSWORD") != "ccpc-smoke-disposable-root-only":
        raise RuntimeError("Unexpected synthetic sidecar root credential")
    benchguard.attest_pinned_bench(home, bench, site_created=False)
    restore.require_local_disposable_target(db.SITE, db.DB_HOST, db.DB_NAME)
    options = _smoke_db_options(home / ".ccpc-pr32-smoke-db", env["CI_DB_ROOT_PASSWORD"])
    first, absent = db._local_mariadb_probe(options, Path("/usr/bin/mariadb"))
    if absent != 0:
        raise RuntimeError("Scratch database already present before new-site")

    # Site credentials are random ephemeral synthetic-only strings. Never print
    # subprocess argv, Frappe output or the site configuration.
    command = [
        "bench", "new-site", db.SITE, "--db-type", "mariadb",
        "--db-name", db.DB_NAME, "--db-user", db.DB_USER,
        "--db-host", db.DB_HOST, "--db-port", str(db.DB_PORT),
        "--db-root-username", "root",
        "--db-root-password", env["CI_DB_ROOT_PASSWORD"],
        "--db-password", secrets.token_urlsafe(24),
        "--admin-password", secrets.token_urlsafe(24),
    ]
    try:
        subprocess.run(
            command, cwd=bench, check=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=900, env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Scratch site creation failed") from None
    conf = bench / "sites" / db.SITE / "site_config.json"
    if conf.is_symlink() or not conf.is_file() or conf.stat().st_uid != os.geteuid():
        raise RuntimeError("Scratch site config invalid after creation")
    conf.chmod(0o600)
    benchguard.attest_pinned_bench(home, bench, site_created=True)
    db._same_isolated_site(bench)
    second, present = db._local_mariadb_probe(options, Path("/usr/bin/mariadb"))
    if second != first or present != 1:
        raise RuntimeError("Scratch MariaDB identity/presence mismatch")
    print("DISPOSABLE CI MARIADB PRE/POST + PINNED FRAPPE BENCH: PASS")


def main() -> int:
    try:
        phase = sys.argv[1] if len(sys.argv) == 2 else ""
        head = _head()
        require_smoke_identity(dict(os.environ), head)
        if phase == "identity":
            print("EXACT-SHA NO-BACKUP SMOKE IDENTITY: PASS")
        elif phase == "site":
            home = Path.home()
            smoke_create_and_attest(
                dict(os.environ), head, home, home / benchguard.BENCH_NAME
            )
        else:
            raise RuntimeError("Unsupported smoke phase")
        return 0
    except Exception:
        # Do not print Frappe stdout/stderr, option file, passwords or site data.
        print("DISPOSABLE CI SMOKE FAILED (sanitized)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

# Copyright (c) 2026, CCPC and contributors
"""One-shot, fail-closed real Integration2 restore rehearsal for hosted CI only.

No import-time side effects. NEVER run in DEVELOPMENT or PRODUCTION. The only
permitted target is the exact, fresh isolated CircleCI MariaDB sidecar and site.
All output is constant/sanitized; the original archive is confidential.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import tarfile
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener

import ci_bench_preflight as benchguard
import ci_db_attestation as dbguard
import restore_guard as guard

MAX_ARCHIVE = 32 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_FILES_BYTES = 64 * 1024 * 1024
B2_TIMEOUT = 20


def _run(command: list[str], *, cwd: Path | None = None, input_data: bytes | None = None,
         seconds: int = 60) -> bytes:
    try:
        p = subprocess.run(command, cwd=cwd, input=input_data,
                           stdin=subprocess.DEVNULL if input_data is None else None,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           check=True, timeout=seconds, env=os.environ.copy())
        if len(p.stdout) > MAX_ARCHIVE:
            raise ValueError("Oversized tool output")
        return p.stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        raise RuntimeError("Isolated restore stage failed (redacted)") from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("Encrypted object redirect forbidden")


def fetch_exact_ciphertext(url: str) -> bytes:
    """Exact private B2 ciphertext only; no redirects and bounded memory."""
    guard.validate_presigned_url(url)
    try:
        response = build_opener(_NoRedirect(), ProxyHandler({})).open(
            Request(url, headers={"Accept-Encoding": "identity"}), timeout=B2_TIMEOUT
        )
        with response:
            if response.getcode() != 200:
                raise ValueError("Unexpected encrypted object HTTP status")
            length = response.headers.get("Content-Length")
            if length is not None and length != str(guard.CIPHERTEXT_BYTES):
                raise ValueError("Unexpected encrypted object Content-Length")
            payload = response.read(guard.CIPHERTEXT_BYTES + 1)
        guard.verify_ciphertext(payload)
        return payload
    except Exception:
        raise RuntimeError("Private ciphertext fetch/integrity failed (redacted)") from None


def _new_private_dir(root: Path) -> None:
    if root.exists() or root.is_symlink() or root.parent.is_symlink():
        raise RuntimeError("Existing/linked protected scratch path forbidden")
    root.mkdir(mode=0o700)
    if root.stat().st_uid != os.geteuid() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise RuntimeError("Protected scratch permissions invalid")


def _write_secret(path: Path, data: bytes, *, strict_parent: bool = True) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    dbguard._owner_file(path, strict_parent=strict_parent)


def prefetch_ciphertext(env: dict[str, str], checkout_sha: str, home: Path) -> None:
    """Fetch the 15-minute signed GET before slow dependency/bench installation.

    Store only the exact hash-pinned age ciphertext in a private scratch folder.
    Never decrypt, print the URL, invoke bench, or touch MariaDB in this phase.
    """
    guard.require_ci_identity(env, checkout_sha)
    if not env.get("CCPC_RESTORE_OBJECT_GET_URL") or not env.get("CCPC_RESTORE_AGE_IDENTITY"):
        raise RuntimeError("Restricted CI restore secrets missing")
    target_dir = home / ".ccpc-restore-ciphertext"
    _new_private_dir(target_dir)
    payload = fetch_exact_ciphertext(env["CCPC_RESTORE_OBJECT_GET_URL"])
    guard.verify_ciphertext(payload)
    _write_secret(target_dir / "payload.age", payload)
    print("ENCRYPTED BACKUP: EARLY EXACT CIPHERTEXT GET PASS")


def read_prefetched_ciphertext(home: Path) -> bytes:
    """Accept only the same job's owner-only hash-pinned encrypted object."""
    file_path = home / ".ccpc-restore-ciphertext" / "payload.age"
    dbguard._owner_file(file_path)
    if file_path.stat().st_size != guard.CIPHERTEXT_BYTES:
        raise RuntimeError("Unexpected prefetched ciphertext size")
    data = file_path.read_bytes()
    guard.verify_ciphertext(data)
    return data


def decrypt_and_validate(payload: bytes, identity: str, scratch: Path) -> tuple[dict[str, bytes], str]:
    if not identity.startswith("AGE-SECRET-KEY-1") or "\n" in identity.strip():
        raise RuntimeError("Invalid ephemeral age identity delivery")
    _write_secret(scratch / "age-identity", (identity.strip() + "\n").encode("ascii"))
    # Ciphertext is verified BEFORE handing it to age; never print age output.
    guard.verify_ciphertext(payload)
    try:
        plaintext = _run(
            ["/usr/bin/age", "--decrypt", "--identity", str(scratch / "age-identity")],
            input_data=payload, seconds=30
        )
    finally:
        (scratch / "age-identity").unlink(missing_ok=True)
    if len(plaintext) > MAX_ARCHIVE:
        raise RuntimeError("Oversized decrypted payload")
    return guard.validate_decrypted_tar(plaintext)


def _file_component(components: dict[str, bytes], suffix: str) -> bytes:
    hits = [v for k, v in components.items() if k.endswith(suffix)
            and not (suffix == "-files.tar" and k.endswith("-private-files.tar"))]
    if len(hits) != 1:
        raise RuntimeError("Exact backup component absent")
    return hits[0]


def _safe_tar_entries(data: bytes, scope: str) -> list[tuple[tuple[str, ...], bytes | None]]:
    """Frappe tar path sites/ORIGINAL_SITE/{public|private}/files/..., stripped safely.

    Rejects all symlinks, hardlinks, absolute/traversal paths, special files,
    duplicate paths, and overly large entries, BEFORE modifying the site.
    """
    if scope not in ("public", "private"):
        raise RuntimeError("Invalid archive scope")
    # Frappe BackupGenerator passes frappe.get_site_path() to tar -cf.
    # The source site path can be relative to sites/ or an absolute host path;
    # GNU tar strips a leading "/" when recording an absolute path. Confine
    # extraction to the exact original site/scope anchor, without assuming
    # how many benign parent directories preceded that anchor.
    site_anchor = ("ccpc-integration2.localhost", scope, "files")
    entries = []
    seen = set()
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
            members = tf.getmembers()
            if not members or len(members) > 2000:
                raise ValueError("Unexpected attachment archive count")
            for m in members:
                raw = m.name
                if not raw or raw.startswith("/") or "\\" in raw or "\x00" in raw:
                    raise ValueError("Invalid attachment path")
                path = PurePosixPath(raw)
                parts = tuple(x for x in path.parts if x != ".")
                if any(x in ("..", "", ".") for x in parts):
                    raise ValueError("Traversal attachment")
                anchors = [i for i in range(len(parts) - 2)
                           if parts[i:i+3] == site_anchor]
                if len(anchors) != 1:
                    raise ValueError("Archive not owned by original site/scope")
                anchor = anchors[0]
                if anchor and parts[anchor - 1] != "sites":
                    raise ValueError("Site anchor must follow sites directory")
                relative = parts[anchor + 3:]
                if relative in seen:
                    raise ValueError("Duplicate attachment")
                seen.add(relative)
                if not (m.isdir() or m.isfile()) or m.issym() or m.islnk():
                    raise ValueError("Link or special attachment forbidden")
                if not relative and not m.isdir():
                    raise ValueError("Files root must be directory")
                if m.isdir():
                    entries.append((relative, None))
                else:
                    if m.size < 0 or m.size > MAX_FILE:
                        raise ValueError("Oversize attachment")
                    total += m.size
                    if total > MAX_FILES_BYTES:
                        raise ValueError("Attachment archive exceeds size limit")
                    stream = tf.extractfile(m)
                    if stream is None:
                        raise ValueError("Missing attachment file data")
                    data_part = stream.read(MAX_FILE + 1)
                    if len(data_part) != m.size:
                        raise ValueError("Attachment size mismatch")
                    entries.append((relative, data_part))
        return entries
    except (OSError, ValueError, tarfile.TarError, EOFError, TypeError):
        raise RuntimeError("Confidential attachment validation failed (redacted)") from None


def _install_files(site: Path, scope: str, entries: list[tuple[tuple[str, ...], bytes | None]]) -> int:
    base = site / scope / "files"
    if site.is_symlink() or base.is_symlink() or not base.is_dir():
        raise RuntimeError("Protected attachment destination missing")
    written = 0
    for rel, content in entries:
        target = base.joinpath(*rel)
        if not rel:
            continue
        # Race-free symlink handling is provided by the isolated single-tenant
        # CI job, plus per-entry component checks and O_NOFOLLOW on file creation.
        cur = base
        for part in rel[:-1]:
            cur = cur / part
            if cur.is_symlink():
                raise RuntimeError("Linked attachment destination forbidden")
            if not cur.exists():
                cur.mkdir(mode=0o700)
            if not cur.is_dir():
                raise RuntimeError("Attachment parent is not a directory")
        if content is None:
            if target.is_symlink():
                raise RuntimeError("Linked attachment directory forbidden")
            if not target.exists():
                target.mkdir(mode=0o700)
            if not target.is_dir():
                raise RuntimeError("Attachment directory type mismatch")
        else:
            if target.exists() or target.is_symlink():
                raise RuntimeError("Refuse overwrite of existing attachment")
            _write_secret(target, content, strict_parent=False)
            written += 1
    return written


def _setup_db_client(home: Path, password: str) -> Path:
    folder = home / ".ccpc-restore-db-client"
    _new_private_dir(folder)
    cfg = folder / "client.cnf"
    if not password or "\n" in password or "\r" in password:
        raise RuntimeError("Untrusted CI-only database password")
    _write_secret(cfg, ("[client]\nuser=root\npassword=" + password + "\n").encode())
    return cfg


def _check_companies(options: Path) -> None:
    # Aggregate only, no company names or row details ever printed in CI.
    query = (
        "SELECT COUNT(*) FROM `tabCompany` "
        "WHERE name IN ('Integration Test Issuer','Integration Test Requester')"
    )
    command = [
        "/usr/bin/mariadb", f"--defaults-file={options}", "--protocol=TCP",
        "--host=127.0.0.1", "--port=3306", f"--database={dbguard.DB_NAME}",
        "--batch", "--skip-column-names", "--silent", "--execute", query,
    ]
    count = _run(command, seconds=15).decode("ascii", errors="strict").strip()
    if count != "2":
        raise RuntimeError("Expected two synthetic restored companies absent")


def _set_local_site_crypto(bench: Path, old_key: str) -> None:
    path = bench / "sites" / guard.SITE / "site_config.json"
    dbguard._owner_file(path, strict_parent=False)
    if not re.fullmatch(r"[A-Za-z0-9_=-]{16,256}", old_key):
        raise RuntimeError("Invalid original site cryptographic key")
    config = json.loads(path.read_text(encoding="utf8"))
    # Preserve only CI-generated DB credentials, replace encryption key only.
    config["encryption_key"] = old_key
    config["disable_scheduler"] = 1
    config["maintenance_mode"] = 1
    config["mute_emails"] = 1
    path.write_text(json.dumps(config, separators=(",", ":")), encoding="utf8")
    path.chmod(0o600)
    dbguard._same_isolated_site(bench)


def restore_once(env: dict[str, str], checkout_sha: str, home: Path, bench: Path) -> None:
    guard.require_ci_identity(env, checkout_sha)
    if env.get("CI_DB_ROOT_PASSWORD") != "ccpc-restore-disposable-db-only":
        raise RuntimeError("Disposable CI sidecar credential mismatch")
    if not env.get("CCPC_RESTORE_AGE_IDENTITY"):
        raise RuntimeError("Restricted CI age identity missing")
    benchguard.attest_pinned_bench(home, bench, site_created=False)
    option_file = _setup_db_client(home, env["CI_DB_ROOT_PASSWORD"])
    proof = dbguard.attest_before_new_site(env, checkout_sha, option_file=option_file)

    # First validate real ciphertext and all four source components before the
    # first potentially destructive DB/site operation.
    # The short-lived signed URL has already expired by the time the pinned
    # Frappe/ERPNext bench is ready. Read the exact encrypted object prefetched
    # in THIS same isolated CircleCI job; never perform a second network GET.
    payload = read_prefetched_ciphertext(home)
    scratch = home / ".ccpc-restore-payload"
    _new_private_dir(scratch)
    try:
        verified, key = decrypt_and_validate(
            payload, env["CCPC_RESTORE_AGE_IDENTITY"], scratch
        )
    finally:
        # Prevent later Frappe/bench child processes inheriting bearer URL/age key.
        # These secrets are needed for only the early authenticated decrypt stage.
        os.environ.pop("CCPC_RESTORE_OBJECT_GET_URL", None)
        os.environ.pop("CCPC_RESTORE_AGE_IDENTITY", None)
        env.pop("CCPC_RESTORE_OBJECT_GET_URL", None)
        env.pop("CCPC_RESTORE_AGE_IDENTITY", None)
        # The sealed object is no longer needed after decrypt validation.
        (home / ".ccpc-restore-ciphertext" / "payload.age").unlink(missing_ok=True)
    public = _safe_tar_entries(_file_component(verified, "-files.tar"), "public")
    private = _safe_tar_entries(_file_component(verified, "-private-files.tar"), "private")
    sql = scratch / "verified.sql.gz"
    _write_secret(sql, _file_component(verified, "-database.sql.gz"))

    new_site = [
        "bench", "new-site", guard.SITE, "--db-type", "mariadb",
        "--db-name", dbguard.DB_NAME, "--db-user", dbguard.DB_USER,
        "--db-host", dbguard.DB_HOST, "--db-port", str(dbguard.DB_PORT),
        "--db-root-username", "root",
        "--db-root-password", env["CI_DB_ROOT_PASSWORD"],
        "--db-password", secrets.token_urlsafe(24),
        "--admin-password", secrets.token_urlsafe(24),
    ]
    _run(new_site, cwd=bench, seconds=900)
    conf = bench / "sites" / guard.SITE / "site_config.json"
    if conf.is_symlink() or not conf.is_file() or conf.stat().st_uid != os.geteuid():
        raise RuntimeError("Disposable restored-site config not proven")
    conf.chmod(0o600)
    benchguard.attest_pinned_bench(home, bench, site_created=True)
    dbguard.attest_before_restore(env, checkout_sha, proof,
                                  option_file=option_file, bench=bench)
    _set_local_site_crypto(bench, key)

    # Pinned Frappe restore force-recreates only this pre-attested disposable
    # destination DB. Shell stdout and stderr are not allowed into CI logs.
    _run([
        "bench", "--site", guard.SITE, "restore", str(sql),
        "--db-root-username", "root",
        "--db-root-password", env["CI_DB_ROOT_PASSWORD"],
        "--admin-password", secrets.token_urlsafe(24),
    ], cwd=bench, seconds=1200)

    dbguard._same_isolated_site(bench)
    _check_companies(option_file)
    site_path = bench / "sites" / guard.SITE
    pub_count = _install_files(site_path, "public", public)
    priv_count = _install_files(site_path, "private", private)

    # Only sanitized aggregate outcomes, never a restored row, password, path,
    # original attachment name, original config, or presigned URL.
    print("ENCRYPTED INTEGRATION2 BACKUP: EXACT CIPHERTEXT SHA PASS")
    print("BACKUP FOUR-PART MANIFEST + SQL/FILE SAFETY: PASS")
    print("DISPOSABLE MARIADB SAME-SERVER PRE/POST: PASS")
    print("RESTORED ORIGINAL TISS/TREQ COMPANIES: 2/2 PASS")
    print("PUBLIC ATTACHMENT ARCHIVE: PASS" if pub_count else "PUBLIC ARCHIVE: EMPTY")
    print("PRIVATE ATTACHMENT ARCHIVE: PASS" if priv_count else "PRIVATE ARCHIVE: EMPTY")
    print("RESTORATION: CI-ONLY, NO WORKERS, NO EMAIL, NO PROD/DEV")


def main() -> int:
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ("--prefetch", "--restore"):
            raise RuntimeError("Explicit isolated stage invocation required")
        head = _run(["/usr/bin/git", "rev-parse", "--verify", "HEAD"], seconds=8).decode().strip()
        home = Path.home()
        if sys.argv[1] == "--prefetch":
            prefetch_ciphertext(dict(os.environ), head, home)
        else:
            restore_once(dict(os.environ), head, home, home / benchguard.BENCH_NAME)
        return 0
    except Exception:
        print("INTEGRATION2 DISPOSABLE RESTORE: FAIL (sanitized)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

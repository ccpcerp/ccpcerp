# Copyright (c) 2026, CCPC and contributors
"""Read one exact age-encrypted B2 object using a restricted B2 Native API key.

Only invoked after CircleCI manual approval and exact checkout attestation.
No AWS SDK, presigned URL, local credentials file, redirects or sensitive logs.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import re
from urllib.parse import quote, urlsplit
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener

import restore_guard as guard

AUTH_URL = "https://api.backblazeb2.com/b2api/v4/b2_authorize_account"
# The exact bucket/key is pinned once in restore_guard.py.
BUCKET, OBJECT = guard.B2_OBJECT_PATH.lstrip("/").split("/", 1)
BUCKET_ID = "00e7494466f980a0a8120e16"  # Owner-verified private bucket ID
PREFIX = "integration2-restore-20261030/"
S3_ENDPOINT = "https://" + guard.B2_HOST
# Trust B2's authoritative bucket/prefix scope and require readFiles; don't
# try to enumerate every present/future harmless read-only metadata permission.
# Still reject granted write/delete/admin powers and bucket/account-wide access.
# This denies the dangerous known B2 grants and future verb-named mutations.
FORBIDDEN_CAPABILITIES = frozenset({
    "bypassGovernance", "listKeys", "listAllBucketNames", "all", "*",
})
MUTATING_CAPABILITY_PREFIXES = (
    "write", "delete", "create", "update", "modify", "remove", "manage",
    "admin", "bypass",
)


def _scoped_read_only(capabilities: object) -> bool:
    if not isinstance(capabilities, list) or not capabilities:
        return False
    if any(not isinstance(cap, str) or not 1 <= len(cap) <= 128
           or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", cap)
           for cap in capabilities):
        return False
    if "readFiles" not in capabilities or len(capabilities) != len(set(capabilities)):
        return False
    return not any(
        cap in FORBIDDEN_CAPABILITIES
        or cap.casefold().startswith(MUTATING_CAPABILITY_PREFIXES)
        for cap in capabilities
    )
MAX_AUTH_BYTES = 64 * 1024
TIMEOUT_SECONDS = 20


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("B2 redirect prohibited")


def _open(request: Request):
    # A B2-issued token must never follow a redirect or use ambient proxy creds.
    return build_opener(_NoRedirect(), ProxyHandler({})).open(request, timeout=TIMEOUT_SECONDS)


def _credentials_ok(key_id: str, app_key: str) -> bool:
    """Bound and validate real B2 key strings without excluding + or /.

    Backblaze application key secrets may contain base64 characters + / and =;
    IDs have a narrower alphabet. Reject whitespace, controls, Unicode and
    colon-delimiters before constructing Basic id:secret authorization.
    """
    return (
        isinstance(key_id, str) and 10 <= len(key_id) <= 256
        and re.fullmatch(r"[A-Za-z0-9_-]+", key_id) is not None
        and isinstance(app_key, str) and 10 <= len(app_key) <= 256
        and re.fullmatch(r"[A-Za-z0-9_+/=-]+", app_key) is not None
    )


def _download_origin(download_url: str) -> str:
    p = urlsplit(download_url)
    if not (
        p.scheme == "https" and re.fullmatch(r"f[0-9]+\.backblazeb2\.com", p.netloc)
        and not p.path and not p.query and not p.fragment
        and not p.username and not p.password and p.port is None
    ):
        raise RuntimeError("Unexpected B2 file origin")
    return download_url


def _restricted_authorization(response: dict) -> tuple[str, str]:
    storage = response["apiInfo"]["storageApi"]
    allowed = storage["allowed"]
    buckets = allowed["buckets"]
    capabilities = allowed["capabilities"]
    if (
        storage["s3ApiUrl"] != S3_ENDPOINT
        or not isinstance(buckets, list) or len(buckets) != 1
        or not isinstance(buckets[0], dict) or buckets[0].get("id") != BUCKET_ID
        # B2 may return name=null for scoped keys without bucket-list access.
        or buckets[0].get("name") not in (None, BUCKET)
        or allowed.get("namePrefix") != PREFIX
        or not _scoped_read_only(capabilities)
    ):
        raise RuntimeError("B2 key restriction mismatch")
    expiry = response.get("applicationKeyExpirationTimestamp")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if not isinstance(expiry, int) or not 300_000 < expiry - now_ms <= 7 * 86400_000:
        raise RuntimeError("Short-lived B2 key missing or expired")
    token = response["authorizationToken"]
    if not isinstance(token, str) or not token or any(c in token for c in "\r\n"):
        raise RuntimeError("Missing B2 authorization token")
    return _download_origin(storage["downloadUrl"]), token


def fetch_exact_ciphertext(key_id: str, app_key: str) -> bytes:
    """Authorize read-only key and GET pinned ciphertext; return bytes only."""
    try:
        if not _credentials_ok(key_id, app_key):
            raise RuntimeError("Invalid B2 credential form")
        basic = base64.b64encode(f"{key_id}:{app_key}".encode("ascii")).decode("ascii")
        auth_request = Request(
            AUTH_URL,
            headers={"Authorization": "Basic " + basic, "Accept": "application/json",
                     "Accept-Encoding": "identity"},
        )
        with _open(auth_request) as result:
            if result.getcode() != 200:
                raise RuntimeError("B2 native authorization HTTP failure")
            raw = result.read(MAX_AUTH_BYTES + 1)
        if len(raw) > MAX_AUTH_BYTES:
            raise RuntimeError("Oversized B2 auth response")
        origin, token = _restricted_authorization(json.loads(raw))
        path = "/file/" + quote(BUCKET, safe="") + "/" + quote(OBJECT, safe="/")
        download_request = Request(
            origin + path,
            headers={"Authorization": token, "Accept-Encoding": "identity"},
        )
        with _open(download_request) as result:
            if result.getcode() != 200:
                raise RuntimeError("B2 download HTTP failure")
            size = result.headers.get("Content-Length")
            if size is not None and size != str(guard.CIPHERTEXT_BYTES):
                raise RuntimeError("B2 ciphertext length disagreement")
            encrypted = result.read(guard.CIPHERTEXT_BYTES + 1)
        guard.verify_ciphertext(encrypted)
        return encrypted
    except Exception:
        # Neither provider responses nor request/authorization details may enter logs.
        raise RuntimeError("Restricted B2 ciphertext authentication/fetch failed (redacted)") from None

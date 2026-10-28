# Copyright (c) 2026, CCPC and contributors
"""Manual, disposable-only authenticated HTTP/CSRF boundary probe.

NOT part of CircleCI automatic jobs. Requires a separately approved CI run,
a running local Frappe web server, and a pre-provisioned fictional checker.
Never supply a real user's password, or run against persistent DEV/PROD.
The only business endpoint called with POST is a read-only review method.
"""
import json
import os
import socket
import subprocess
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import (
    HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener,
)

from ccpcerp.ccpc_erp.ci_finance_lifecycle_site_guard import (
    assert_finance_disposable_site,
)

SITE = "ci-finance-lifecycle.localhost"
BASE = "http://" + SITE + ":8000"
CHECKER = "ccpc-finance-ci-checker@invalid.test"
REVIEW = "/api/method/ccpcerp.ccpc_erp.bc_mapping_workflow.get_mapping_review"
IDENTITY = "/api/method/frappe.auth.get_logged_user"


class RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("HTTP redirect denied for isolated Finance probe.")


def preflight():
    # Run every guard before DNS lookup, credential use or HTTP requests.
    assert_finance_disposable_site(SITE)
    if os.environ.get("CCPC_ALLOW_FINANCE_HTTP_PROBE") != "isolated-only":
        raise RuntimeError("Separate disposable HTTP probe approval is required.")
    if os.environ.get("CCPC_FINANCE_HTTP_BASE_URL") != BASE:
        raise RuntimeError("Finance HTTP target must be the exact disposable site.")
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    ).strip()
    if sha != os.environ.get("CCPC_FINANCE_APPROVED_SHA"):
        raise RuntimeError("HTTP probe repository checkout differs from approved SHA.")
    password = os.environ.get("CCPC_FINANCE_HTTP_CHECKER_PASSWORD")
    if not password:
        raise RuntimeError("Synthetic checker password not supplied securely.")
    ips = {entry[4][0] for entry in socket.getaddrinfo(SITE, 8000)}
    if not ips or not all(ip in ("127.0.0.1", "::1") for ip in ips):
        raise RuntimeError("Disposable site must resolve exclusively to loopback.")
    return password


def call(opener, path, *, method="GET", fields=None):
    data = json.dumps(fields).encode() if fields is not None else None
    request = Request(BASE + path, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with opener.open(request, timeout=8) as response:
            return response.status, response.read(8192)
    except HTTPError as error:
        # Never print response body: server errors may include user/session data.
        error.read(8192)
        return error.code, b""


def main():
    password = preflight()
    jar = CookieJar()
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar), RejectRedirect())

    status, _ = call(
        opener, "/api/method/login", method="POST",
        fields={"usr": CHECKER, "pwd": password},
    )
    password = None
    if status != 200 or not any(cookie.name == "sid" for cookie in jar):
        raise RuntimeError("Synthetic checker session login did not succeed.")

    status, body = call(opener, IDENTITY)
    if status != 200 or json.loads(body).get("message") != CHECKER:
        raise RuntimeError("Session did not authenticate as exact checker identity.")

    # All Finance RPCs are POST-only, including the read-only review method.
    status, _ = call(opener, REVIEW + "?kind=Expense&revision=nonexistent")
    # Frappe v16 handler raises PermissionError (HTTP 403), not HTTP 405.
    if status != 403:
        raise RuntimeError("Finance review GET was not denied by the method guard.")

    # Do not include a CSRF header. The endpoint is read-only even if CSRF
    # configuration is defective, so no business transaction can be posted.
    status, _ = call(
        opener, REVIEW, method="POST",
        fields={"kind": "Expense", "revision": "nonexistent"},
    )
    # In pinned Frappe v16, CSRFTokenError.http_status_code is 400.
    if status != 400:
        raise RuntimeError("Authenticated cookie POST without CSRF was not rejected.")

    print("Finance disposable authenticated HTTP identity/GET/CSRF: PASS")
    print("No mapping approval, invoice, stock or GL endpoint was invoked.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Avoid printing exception strings from password/session/network handlers.
        print("Finance disposable authenticated HTTP probe: FAIL", file=sys.stderr)
        sys.exit(1)

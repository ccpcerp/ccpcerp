# Copyright (c) 2026, CCPC and contributors
"""Dormant, manually gated real-DB Finance approval / retirement race probe.

This is NOT an HTTP authentication test. It executes the real Finance service
functions under two separate Frappe v16 thread-local MariaDB connections and
synthetic Checker identities. It must run only inside a separately approved,
disposable CircleCI Finance site with *committed synthetic fixtures* prepared
before calling this function. It is NOT wired into any CircleCI job.

No fixtures, credentials, roles, Company permissions or account/GL/stock records
are created here. The winning request intentionally commits synthetic mapping
state in the disposable test database. Never execute on DEV/PRODUCTION.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError
import os
from pathlib import Path
import subprocess

import frappe

from ccpcerp.ccpc_erp.bc_finance_lifecycle_events import (
    decide_mapping_retirement,
)
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
from ccpcerp.ccpc_erp.bc_mapping_workflow import approve_mapping
from ccpcerp.ccpc_erp.ci_finance_lifecycle_site_guard import (
    assert_finance_disposable_site,
)

SITE = "ci-finance-lifecycle.localhost"
COMPANY = "_Test Company"
CHECKER = "ccpc-finance-ci-checker@invalid.test"
REV = "CCPC Expense Mapping Revision"
SCOPE = "CCPC Expense Mapping Scope"
DECISION = "CCPC Accounting Mapping Decision"
REQUEST = "CCPC Mapping Retirement Request"
USE = "CCPC Mapping Decision Use"
RETIRE_EVENT = "CCPC Mapping Retirement Event"


def _preflight(action, target, expected_modified, decision_id):
    # Called before any DB reads, starting threads, or financial state writes.
    assert_finance_disposable_site(getattr(frappe.local, "site", None))
    if os.environ.get("CCPC_ALLOW_FINANCE_RACE_PROBE") != "isolated-only":
        raise RuntimeError("Separate disposable Finance race probe approval missing.")
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    ).strip()
    if sha != os.environ.get("CCPC_FINANCE_APPROVED_SHA"):
        raise RuntimeError("Finance race probe checkout is not owner-approved.")
    if frappe.conf.db_type != "mariadb":
        raise RuntimeError("Real Finance concurrency probe requires MariaDB.")
    if action not in ("Approve", "Retire"):
        raise RuntimeError("Only Approval and Retirement races are supported.")
    if not all(type(x) is str and 0 < len(x) <= 140 for x in
               (target, expected_modified, decision_id)):
        raise RuntimeError("Explicit canonical test fixture IDs are required.")

    if action == "Approve":
        revision = target
        row = frappe.db.get_value(
            REV, revision, ["mapping_scope", "approval_state", "modified"],
            as_dict=True,
        )
        if not row or row.approval_state != "Draft":
            raise RuntimeError("Expected one committed synthetic Draft mapping.")
        version = str(row.modified)
    else:
        request = frappe.db.get_value(
            REQUEST, target, ["revision", "company", "request_state", "modified"],
            as_dict=True,
        )
        if not request or request.company != COMPANY or request.request_state != "Pending":
            raise RuntimeError("Expected one committed synthetic Pending retirement.")
        revision = request.revision
        row = frappe.db.get_value(
            REV, revision, ["mapping_scope", "approval_state", "effective_until"],
            as_dict=True,
        )
        if not row or row.approval_state != "Approved" or row.effective_until is not None:
            raise RuntimeError("Retirement target is not open and Approved.")
        version = str(request.modified)

    scope_company = frappe.db.get_value(SCOPE, row.mapping_scope, "company")
    if scope_company != COMPANY or version != expected_modified:
        raise RuntimeError("Fixture Company or reviewed version mismatch.")
    signed = frappe.db.get_value(
        DECISION, decision_id,
        ["status", "action", "revision", "company", "request_name", "signed_by"],
        as_dict=True,
    )
    if not signed or signed.status != "Signed" or signed.action != action:
        raise RuntimeError("Expected a separately signed synthetic decision.")
    if (signed.revision != revision or signed.company != COMPANY
            or signed.signed_by != CHECKER):
        raise RuntimeError("Signed decision identity does not match test fixture.")
    if (signed.request_name or "") != (target if action == "Retire" else ""):
        raise RuntimeError("Signed retirement request binding is incorrect.")
    if frappe.db.exists(USE, {"decision": decision_id}):
        raise RuntimeError("Decision is already consumed before the race.")
    if not frappe.db.exists("User", CHECKER):
        raise RuntimeError("Synthetic Checker fixture is missing.")
    return revision


def _worker(action, target, expected_modified, decision_id, sites_path, barrier):
    initialized = False
    try:
        # Each thread has its own Frappe local and a distinct database session.
        frappe.init(site=SITE, sites_path=sites_path)
        initialized = True
        frappe.connect(set_admin_as_user=False)
        assert_finance_disposable_site(getattr(frappe.local, "site", None))
        frappe.set_user(CHECKER)
        frappe.db.sql("SET SESSION innodb_lock_wait_timeout = 8")
        barrier.wait(timeout=10)
        try:
            if action == "Approve":
                result = approve_mapping(
                    "Expense", target, expected_modified, decision_id,
                )
                if result["state"] != "Approved":
                    raise RuntimeError("Approval did not reach Approved state.")
            else:
                result = decide_mapping_retirement(
                    "Expense", target, expected_modified,
                    "Accept", decision_id=decision_id,
                )
                if result["state"] != "Approved":
                    raise RuntimeError("Retirement acceptance did not succeed.")
            frappe.db.commit()
            return "committed"
        except MappingPolicyError:
            frappe.db.rollback()
            return "business_denied"
        except Exception:
            frappe.db.rollback()
            raise
    finally:
        if initialized:
            frappe.destroy()


def verify_finance_business_race(action, target, expected_modified, decision_id):
    """Require exactly one committed operation and one business denial.

    IMPORTANT: fixture creation/commit must have been independently approved
    and completed in the disposable CI database before this entrypoint runs.
    The actual two attempts do commit/rollback; only a disposable site is safe.
    """
    revision = _preflight(action, target, expected_modified, decision_id)
    # Force a fresh transaction. Seed fixtures must already be committed:
    # speculative changes in the caller are explicitly discarded.
    frappe.db.rollback()
    sites_path = frappe.local.sites_path
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ccpc-finance-ci") as pool:
        futures = [
            pool.submit(
                _worker, action, target, expected_modified, decision_id,
                sites_path, barrier,
            )
            for _ in range(2)
        ]
        outcomes = [future.result(timeout=40) for future in futures]
    if sorted(outcomes) != ["business_denied", "committed"]:
        raise RuntimeError("Concurrent Finance result did not serialize once-only.")
    frappe.db.rollback()
    if frappe.db.count(USE, {"decision": decision_id}) != 1:
        raise RuntimeError("Decision use count is not exactly one.")
    if action == "Approve":
        if frappe.db.get_value(REV, revision, "approval_state") != "Approved":
            raise RuntimeError("Concurrent approval did not persist as Approved.")
    else:
        if frappe.db.get_value(REQUEST, target, "request_state") != "Accepted":
            raise RuntimeError("Concurrent retirement did not persist as Accepted.")
        if frappe.db.count(RETIRE_EVENT, {"revision": revision}) != 1:
            raise RuntimeError("Exactly one retirement cutoff event is required.")
        row = frappe.db.get_value(
            REV, revision, ["approval_state", "effective_until"], as_dict=True,
        )
        if row.approval_state != "Approved" or row.effective_until is not None:
            raise RuntimeError("Retirement mutated historical Approved revision.")
    frappe.db.rollback()
    print("Disposable Finance " + action + " concurrent once-only: PASS")
    print("One committed synthetic action, one denied; no GL/stock posting.")

# Copyright (c) 2026, CCPC and contributors
"""Fail-closed company-scoped Maker/Checker Draft rejection and retirement events."""
import frappe
from frappe.utils import now_datetime

from ccpcerp.ccpc_erp.bc_issue_runtime import _canonical, _require, _utc
from ccpcerp.ccpc_erp.bc_mapping_lifecycle import _locate
from ccpcerp.ccpc_erp.bc_mapping_policy import assert_independent_review
from ccpcerp.ccpc_erp.bc_finance_evidence import (
    audit, approved_hash, consume_decision, digest, event_insert, verify_decision,
)

REJECT = "CCPC Mapping Rejection Event"
REQUEST = "CCPC Mapping Retirement Request"
RETIRE = "CCPC Mapping Retirement Event"


def reason_text(value):
    _require(isinstance(value, str) and value == value.strip()
             and 0 < len(value) <= 500,
             "A nonempty canonical reason of at most 500 characters is required.")
    return value


def request_row(name):
    name = _canonical(name, "Retirement request")
    table = chr(96) + "tab" + REQUEST + chr(96)
    rows = frappe.db.sql(
        "SELECT name,kind,revision,mapping_scope,company,financial_value,"
        "maker,reason,requested_at,target_hash,request_state,modified "
        "FROM " + table + " WHERE name=%s FOR UPDATE", (name,), as_dict=True,
    )
    _require(len(rows) == 1, "Unknown retirement request.")
    return rows[0]


@frappe.whitelist(methods=["POST"])
def reject_mapping(kind, revision, expected_modified, reason):
    """Reject an unchanged Draft; never create a financial approval."""
    expected_modified = _canonical(expected_modified, "Reviewed Draft token")
    reason = reason_text(reason)
    rev_dt, field, _, company, scope, row, checker = _locate(
        kind, revision, "CCPC Accounting Mapping Approver",
    )
    _require(row.approval_state == "Draft" and not row.approved_by
             and not row.approved_at and str(row.modified) == expected_modified,
             "Draft was already finalized or changed.")
    assert_independent_review(row.owner, checker)
    key = digest(kind, row.name)
    _require(not frappe.db.exists(REJECT, {"target_key": key}),
             "Draft already has a rejection event.")
    marker = "ccpc_draft_rejection"
    frappe.db.savepoint(marker)
    try:
        now = now_datetime()
        event = event_insert(REJECT, {
            "kind": kind, "revision": row.name, "mapping_scope": scope.name,
            "company": company, "maker": row.owner, "checker": checker,
            "reason": reason, "rejected_at": now, "target_key": key,
            "event_hash": digest(kind, row.name, checker, now, reason),
        })
        table = chr(96) + "tab" + rev_dt + chr(96)
        frappe.db.sql(
            "UPDATE " + table + " SET approval_state='Rejected',"
            "rejected_by=%s,rejected_at=%s,rejection_reason=%s,"
            "rejection_event=%s,modified=%s WHERE name=%s AND "
            "approval_state='Draft' AND modified=%s",
            (checker, now, reason, event.name, now, row.name, row.modified),
        )
        _require(frappe.db.sql("SELECT ROW_COUNT()")[0][0] == 1,
                 "Concurrent Draft rejection conflict.")
        version = audit(rev_dt, row.name, "rejected",
                        {"checker": checker, "company": company,
                         "event": event.name, "reason": reason})
        return {"name": row.name, "state": "Rejected",
                "event": event.name, "version": version}
    except Exception:
        frappe.db.rollback(save_point=marker)
        raise


@frappe.whitelist(methods=["POST"])
def request_mapping_retirement(kind, revision, reason):
    """Maker submits a non-effective request against one open Approved revision."""
    reason = reason_text(reason)
    rev_dt, field, _, company, scope, row, maker = _locate(
        kind, revision, "CCPC Mapping Preparer",
    )
    _require(row.approval_state == "Approved" and row.approved_by
             and row.approved_at and row.approval_reference
             and row.effective_until is None and
             _utc(row.effective_from) <= _utc(now_datetime()),
             "Retirement can only be requested for an active open Approved mapping.")
    key = digest(kind, row.name)
    _require(not frappe.db.exists(RETIRE, {"target_key": key}),
             "Mapping already has an accepted retirement cutoff.")
    # The Company row and Scope are locked by _locate, serializing requests.
    ref = approved_hash(kind, company, scope, row, field)
    request_key = digest(kind, row.name, maker, ref, reason)
    existing = frappe.db.sql(
        "SELECT name, company, mapping_scope, financial_value, maker, reason,"
        "target_hash, request_key FROM " + chr(96) + "tab" + REQUEST + chr(96) +
        " WHERE kind=%s AND revision=%s AND request_state='Pending' "
        "ORDER BY name FOR UPDATE", (kind, row.name), as_dict=True,
    )
    _require(len(existing) <= 1, "Conflicting pending retirement requests.")
    if existing:
        previous = existing[0]
        _require(previous.company == company
                 and previous.mapping_scope == scope.name
                 and previous.financial_value == row[field]
                 and previous.maker == maker
                 and previous.reason == reason
                 and previous.target_hash == ref
                 and previous.request_key == request_key,
                 "Different retirement request already pending; inspect it before retry.")
        return {"name": previous.name, "state": "Pending", "reused": True}
    marker = "ccpc_finance_retirement_request"
    frappe.db.savepoint(marker)
    try:
        created = event_insert(REQUEST, {
            "kind": kind, "revision": row.name, "mapping_scope": scope.name,
            "company": company, "financial_value": row[field],
            "maker": maker, "reason": reason, "requested_at": now_datetime(),
            "target_hash": ref, "request_state": "Pending", "request_key": request_key,
        })
        version = audit(REQUEST, created.name, "retirement_requested",
                        {"kind": kind, "revision": row.name, "company": company,
                         "maker": maker, "target_hash": ref})
        return {"name": created.name, "state": "Pending",
                "version": version, "reused": False}
    except Exception:
        frappe.db.rollback(save_point=marker)
        raise


@frappe.whitelist(methods=["POST"])
def get_retirement_request_review(kind, request_name):
    """Checker reads the exact current request version under Finance scope locks."""
    request_name = _canonical(request_name, "Retirement request")
    locator = frappe.db.get_value(REQUEST, request_name, ["kind", "revision"], as_dict=True)
    _require(locator and locator.kind == kind, "Unknown retirement request.")
    _, field, _, company, scope, row, checker = _locate(
        kind, locator.revision, "CCPC Accounting Mapping Approver",
    )
    req = request_row(request_name)
    _require(req.kind == kind and req.revision == row.name
             and req.company == company and req.mapping_scope == scope.name
             and req.financial_value == row[field]
             and req.target_hash == approved_hash(kind, company, scope, row, field),
             "Retirement request does not match its original Approved mapping.")
    assert_independent_review(req.maker, checker)
    return {
        "name": req.name, "kind": kind, "revision": row.name,
        "company": company, "scope": scope.name,
        "value": row[field], "prepared_by": req.maker, "reason": req.reason,
        "requested_at": str(req.requested_at),
        "state": req.request_state, "modified": str(req.modified),
        "checker": checker,
    }


@frappe.whitelist(methods=["POST"])
def decide_mapping_retirement(kind, request_name, expected_modified, action,
                              decision_id=None, decline_reason=None):
    """Checker declines or accepts one reviewed request; accepted event cuts off future use."""
    action = _canonical(action, "Retirement action")
    _require(action in ("Accept", "Decline"), "Unsupported retirement action.")
    expected_modified = _canonical(expected_modified, "Reviewed request version")
    request_name = _canonical(request_name, "Retirement request")
    # Locator only; all authority comes from locks and exact re-read below.
    ref = frappe.db.get_value(REQUEST, request_name, ["kind", "revision"], as_dict=True)
    _require(ref and ref.kind == kind, "Unknown retirement request.")
    rev_dt, field, _, company, scope, row, checker = _locate(
        kind, ref.revision, "CCPC Accounting Mapping Approver",
    )
    req = request_row(request_name)
    _require(req.kind == kind and req.revision == row.name
             and req.mapping_scope == scope.name and req.company == company
             and req.financial_value == row[field]
             and req.request_state == "Pending"
             and str(req.modified) == expected_modified
             and req.target_hash == approved_hash(kind, company, scope, row, field),
             "Retirement request version or target changed.")
    assert_independent_review(req.maker, checker)
    _require(row.approval_state == "Approved" and row.effective_until is None
             and _utc(row.effective_from) <= _utc(now_datetime()),
             "Approved target is not active and open-ended.")
    marker = "ccpc_retirement_accept_or_decline"
    frappe.db.savepoint(marker)
    try:
        now = now_datetime()
        table = chr(96) + "tab" + REQUEST + chr(96)
        if action == "Decline":
            _require(not decision_id, "Decline does not consume an Accounting decision.")
            msg = reason_text(decline_reason)
            frappe.db.sql(
                "UPDATE " + table + " SET request_state='Declined',"
                "decided_by=%s,decided_at=%s,decision_reason=%s,modified=%s "
                "WHERE name=%s AND request_state='Pending' AND modified=%s",
                (checker, now, msg, now, req.name, req.modified),
            )
            _require(frappe.db.sql("SELECT ROW_COUNT()")[0][0] == 1,
                     "Retirement request already decided.")
            version = audit(REQUEST, req.name, "declined_retirement",
                            {"checker": checker, "reason": msg})
            return {"name": req.name, "state": "Declined", "version": version}
        _require(not decline_reason, "Acceptance uses the signed request's reason.")
        signed = verify_decision(decision_id, kind, "Retire", company, scope,
                                 row, checker, field, req.name)
        _require(_utc(now) > _utc(row.effective_from),
                 "Retirement cutoff cannot precede original approved start.")
        key = digest(kind, row.name)
        _require(not frappe.db.exists(RETIRE, {"target_key": key}),
                 "An accepted retirement event already exists.")
        event = event_insert(RETIRE, {
            "kind": kind, "revision": row.name, "mapping_scope": scope.name,
            "company": company, "retirement_request": req.name,
            "decision": signed.name, "approved_by": checker,
            "approved_at": now, "cutoff_at": now,
            "reason": req.reason, "target_key": key,
            "event_hash": digest(kind, row.name, signed.name, checker, now),
        })
        frappe.db.sql(
            "UPDATE " + table + " SET request_state='Accepted',"
            "decided_by=%s,decided_at=%s,modified=%s WHERE name=%s "
            "AND request_state='Pending' AND modified=%s",
            (checker, now, now, req.name, req.modified),
        )
        _require(frappe.db.sql("SELECT ROW_COUNT()")[0][0] == 1,
                 "Concurrent retirement acceptance conflict.")
        use = consume_decision(signed, kind, "Retire", row.name, event.name, checker)
        version = audit(rev_dt, row.name, "retirement_accepted",
                        {"request": req.name, "event": event.name,
                         "cutoff_at": str(now), "decision": signed.name,
                         "checker": checker, "use": use})
        return {"name": row.name, "state": "Approved",
                "cutoff_at": str(now), "event": event.name, "version": version}
    except Exception:
        frappe.db.rollback(save_point=marker)
        raise

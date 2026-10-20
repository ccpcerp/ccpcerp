# Copyright (c) 2026, CCPC and contributors
"""Finance maker/checker lifecycle for an already-approved B/C mapping.

All changes are prospective. The original revision remains Approved with a
closed half-open validity interval, preserving historical stock/GL provenance.
A replacement becomes Approved at that exact cutoff; a retirement creates a
Revoked decision record with NO replacement eligible for posting.

This code never commits, assigns roles or Company permissions, migrates a site,
or operates on DEVELOPMENT/PRODUCTION as part of GitHub review.
"""
import frappe
from frappe.utils import get_datetime, now_datetime

from ccpcerp.ccpc_erp.bc_mapping_policy import (
    MappingPolicyError, assert_independent_review,
)
from ccpcerp.ccpc_erp.bc_issue_runtime import (
    _canonical, _real_authority, _require, _utc,
)
from ccpcerp.ccpc_erp.bc_mapping_workflow import (
    _company_lock, _kind, _lock_revision, _master_validated, _scope_values,
)


def _locate(kind, revision, role):
    """Untrusted locator first, then Company -> scope -> revision row locks."""
    scope_dt, rev_dt, source_field, value_field, _, master_dt = _kind(kind)
    revision = _canonical(revision, "Mapping revision")
    scope_id = frappe.db.get_value(rev_dt, revision, "mapping_scope")
    _require(bool(scope_id), "Unknown mapping revision.")
    company = frappe.db.get_value(scope_dt, scope_id, "company")
    _require(bool(company), "Unknown mapping Company.")
    actor = _canonical(frappe.session.user, "Finance actor")
    _real_authority(actor, role, company)
    _company_lock(company)
    scope_rows = frappe.db.sql(
        "SELECT name, company, " + source_field + " FROM " +
        chr(96) + "tab" + scope_dt + chr(96) + " WHERE name=%s FOR UPDATE",
        (scope_id,), as_dict=True,
    )
    _require(len(scope_rows) == 1 and scope_rows[0].company == company,
             "Mapping scope Company changed.")
    scope = scope_rows[0]
    _scope_values(kind, company, scope[source_field])
    row = _lock_revision(rev_dt, revision)
    _require(row.mapping_scope == scope_id, "Mapping revision scope changed.")
    return rev_dt, value_field, master_dt, company, scope, row, actor


def _open_predecessor(row, scope_id, at):
    _require(row.mapping_scope == scope_id and row.approval_state == "Approved"
             and row.effective_until is None and row.approved_by and row.approved_at
             and row.approval_reference,
             "Only an open-ended, signed Approved revision may be changed.")
    _require(_utc(row.effective_from) is not None and _utc(row.effective_from) <= at,
             "The predecessor must already be effective.")
    _require(_utc(row.approved_at) is not None and _utc(row.approved_at) <= at,
             "The predecessor must have a valid past approval.")


def _only_predecessor_approved(rev_dt, scope_id, old_name):
    table = chr(96) + "tab" + rev_dt + chr(96)
    rows = frappe.db.sql(
        "SELECT name FROM " + table +
        " WHERE mapping_scope=%s AND approval_state='Approved' "
        "ORDER BY name FOR UPDATE", (scope_id,), as_dict=True,
    )
    _require([r.name for r in rows] == [old_name],
             "Additional Approved revisions require separate Finance reconciliation.")


def _audit(rev_dt, name, event, details, changes):
    document = frappe.get_doc({
        "doctype": "Version", "ref_doctype": rev_dt, "docname": name,
        "data": frappe.as_json({
            "changed": changes,
            "ccpc_mapping_lifecycle": dict(details, event=event),
        }),
    })
    document.insert(ignore_permissions=True)
    return document.name


def _propose_change(kind, old_revision, proposed_value, cutoff, reason):
    role = "CCPC Mapping Preparer"
    rev_dt, value_field, master_dt, company, scope, old, maker = _locate(
        kind, old_revision, role,
    )
    now = _utc(now_datetime())
    _open_predecessor(old, scope.name, now)
    _only_predecessor_approved(rev_dt, scope.name, old.name)
    _master_validated(master_dt, old[value_field], company)

    retirement = reason is not None
    if retirement:
        _require(isinstance(reason, str) and bool(reason.strip()) and
                 reason == reason.strip() and len(reason) <= 500,
                 "Retirement requires a bounded Finance reason.")
        _require(proposed_value is None,
                 "A retirement cannot introduce a replacement value.")
        value = old[value_field]
    else:
        value = _canonical(proposed_value, "Replacement financial master")
        _require(value != old[value_field],
                 "A replacement must select a different approved master.")
        _master_validated(master_dt, value, company)
    proposed_utc = _utc(cutoff)
    _require((retirement and cutoff in (None, "")) or
             (proposed_utc is not None and proposed_utc > now),
             "Cutoff must be strictly future, or immediate for an authorized retirement.")
    _require(proposed_utc is None or proposed_utc > _utc(old.effective_from),
             "Cutoff must follow the predecessor's effective start.")

    table = chr(96) + "tab" + rev_dt + chr(96)
    numbers = frappe.db.sql(
        "SELECT revision_no FROM " + table +
        " WHERE mapping_scope=%s ORDER BY revision_no FOR UPDATE",
        (scope.name,), as_dict=True,
    )
    next_no = max((int(r.revision_no) for r in numbers), default=0) + 1
    # Draft insertion validates the real Company-owned master. The restricted
    # lifecycle provenance is then populated server-side, never via Doc.save.
    draft = frappe.get_doc({
        "doctype": rev_dt, "mapping_scope": scope.name,
        "revision_no": next_no, value_field: value,
        "approval_state": "Draft", "effective_from": cutoff or None,
    }).insert(ignore_permissions=True)
    _require(draft.owner == maker, "Lifecycle request must belong to its actual Finance preparer.")
    frappe.db.sql(
        "UPDATE " + table +
        " SET supersedes=%s, revocation_reason=%s "
        "WHERE name=%s AND approval_state='Draft'",
        (old.name, reason if retirement else None, draft.name),
    )
    details = {
        "kind": kind, "company": company, "scope": scope.name,
        "source": scope["item_group" if kind == "Expense" else "beneficiary_department"],
        "predecessor": old.name, "proposal": draft.name, "prepared_by": maker,
        "old_value": old[value_field], "new_value": value,
        "retirement": retirement, "reason": reason or "",
        "requested_cutoff": str(cutoff or "immediately on independent approval"),
    }
    version = _audit(rev_dt, draft.name, "proposed_retirement" if retirement
                     else "proposed_replacement", details, [])
    return {"name": draft.name, "state": "Draft", "supersedes": old.name,
            "change": "Retire" if retirement else "Replace", "version": version}


@frappe.whitelist(methods=["POST"])
def propose_mapping_replacement(kind, current_revision, new_value, effective_from):
    """Finance preparer creates an immutable future-effective replacement Draft."""
    return _propose_change(kind, current_revision, new_value, effective_from, None)


@frappe.whitelist(methods=["POST"])
def propose_mapping_retirement(kind, current_revision, reason, effective_from=None):
    """Finance preparer proposes retirement; empty cutoff means approval-time."""
    return _propose_change(kind, current_revision, None, effective_from, reason)


@frappe.whitelist(methods=["POST"])
def approve_mapping_change(kind, revision, expected_modified, approval_reference):
    """Independent checker atomically closes predecessor and signs its successor.

    No implicit commit. Any exception after the first write rolls back this
    lifecycle write group to a savepoint even if the caller catches the error.
    The still-Approved predecessor remains historically verifiable.
    """
    approval_reference = _canonical(approval_reference, "Accounting approval reference")
    _require(len(approval_reference) <= 140, "Accounting approval reference is too long.")
    expected_modified = _canonical(expected_modified, "Reviewed lifecycle draft version")
    rev_dt, value_field, master_dt, company, scope, draft, checker = _locate(
        kind, revision, "CCPC Accounting Mapping Approver",
    )
    _require(draft.approval_state == "Draft" and draft.supersedes,
             "Only an explicitly linked Draft mapping change can be approved.")
    _require(str(draft.modified) == expected_modified,
             "Lifecycle Draft changed since the independent review.")
    _require(not draft.approved_by and not draft.approved_at and not draft.approval_reference,
             "Lifecycle Draft contains forged approval history.")
    maker, checker = assert_independent_review(draft.owner, checker)
    _real_authority(maker, "CCPC Mapping Preparer", company)
    old = _lock_revision(rev_dt, draft.supersedes)
    approved_at = now_datetime().replace(microsecond=0)
    approved_utc = _utc(approved_at)
    _open_predecessor(old, scope.name, approved_utc)
    _only_predecessor_approved(rev_dt, scope.name, old.name)
    _master_validated(master_dt, old[value_field], company)
    is_retirement = bool(draft.revocation_reason)
    if is_retirement:
        _require(isinstance(draft.revocation_reason, str) and
                 len(draft.revocation_reason) <= 500 and
                 draft[value_field] == old[value_field],
                 "Retirement evidence does not match the signed predecessor.")
    else:
        _require(draft[value_field] != old[value_field],
                 "Replacement must select a different financial master.")
        _master_validated(master_dt, draft[value_field], company)
    future = _utc(draft.effective_from)
    _require(future is not None or is_retirement,
             "Only retirement may become effective immediately.")
    _require(future is None or future > approved_utc,
             "Approved cutoff cannot be backdated or already elapsed.")
    cutoff = get_datetime(draft.effective_from) if future is not None else approved_at
    _require(_utc(cutoff) > _utc(old.effective_from),
             "Approved cutoff must follow the old signed interval start.")
    table = chr(96) + "tab" + rev_dt + chr(96)
    details = {
        "kind": kind, "company": company, "scope": scope.name,
        "source": scope["item_group" if kind == "Expense" else "beneficiary_department"],
        "predecessor": old.name, "proposal": draft.name,
        "old_value": old[value_field], "new_value": draft[value_field],
        "prepared_by": maker, "approved_by": checker,
        "approved_at": str(approved_at), "cutoff": str(cutoff),
        "reason": draft.revocation_reason or "",
        "approval_reference": approval_reference,
    }
    savepoint = "ccpc_mapping_lifecycle_write"
    frappe.db.savepoint(savepoint)
    try:
        frappe.db.sql(
            "UPDATE " + table +
            " SET effective_until=%s, revocation_reason=%s "
            "WHERE name=%s AND approval_state='Approved' AND effective_until IS NULL",
            (cutoff, draft.revocation_reason if is_retirement else None, old.name),
        )
        frappe.db.sql(
            "UPDATE " + table +
            " SET approval_state=%s, approved_by=%s, approved_at=%s, "
            "approval_reference=%s, effective_from=%s "
            "WHERE name=%s AND approval_state='Draft' AND supersedes=%s",
            ("Revoked" if is_retirement else "Approved", checker, approved_at,
             approval_reference, cutoff, draft.name, old.name),
        )
        old_audit = _audit(
            rev_dt, old.name, "closed_for_retirement" if is_retirement
            else "closed_for_replacement", details,
            [["effective_until", None, str(cutoff)]],
        )
        new_audit = _audit(
            rev_dt, draft.name, "approved_retirement" if is_retirement
            else "approved_replacement", details,
            [["approval_state", "Draft", "Revoked" if is_retirement else "Approved"],
             ["approved_by", None, checker], ["approved_at", None, str(approved_at)]],
        )
        closed = _lock_revision(rev_dt, old.name)
        signed = _lock_revision(rev_dt, draft.name)
        _require(_utc(closed.effective_until) == _utc(cutoff)
                 and signed.approval_state == ("Revoked" if is_retirement else "Approved")
                 and signed.approved_by == checker and signed.supersedes == old.name,
                 "Lifecycle did not persist an auditable matched cutoff.")
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    return {"name": draft.name, "state": signed.approval_state,
            "supersedes": old.name, "effective_from": str(cutoff),
            "predecessor_closed_until": str(cutoff),
            "old_version": old_audit, "new_version": new_audit}

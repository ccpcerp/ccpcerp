# Copyright (c) 2026, CCPC and contributors
"""G1/G2 review implementation: Company-scoped B/C proposal -> independent approval.

No setup-time role grants, no Admin override, no DEV/PROD deployment. Only two
explicitly recognized, Department-only or exact Item Group scopes; other keys
and role/Company combinations fail closed. Caller owns normal request/transaction
rollback. Server decides owner, timestamps, revision numbers, and final state.

Approval is not an endorsement of CCPC's live master crosswalk: that still
requires the owner's separate Company/account/Center signoff and migration.
"""
from datetime import datetime, timezone

import frappe
from frappe.utils import now_datetime

from ccpcerp.ccpc_erp.bc_issue_runtime import (
    _canonical, _master, _real_authority, _utc, _require,
)
from ccpcerp.ccpc_erp.bc_mapping_policy import (
    MappingPolicyError, assert_independent_review, intervals_overlap,
)

TYPES = {
    "Expense": (
        "CCPC Expense Mapping Scope", "CCPC Expense Mapping Revision",
        "item_group", "expense_account", "Item Group", "Account",
    ),
    "Center": (
        "CCPC Center Mapping Scope", "CCPC Center Mapping Revision",
        "beneficiary_department", "cost_center", "Department", "Cost Center",
    ),
}


def _kind(kind):
    _require(kind in TYPES, "Only Expense and Center mapping workflows are supported.")
    return TYPES[kind]


def _company_lock(company):
    _canonical(company, "Mapping Company")
    rows = frappe.db.sql("SELECT name FROM `tabCompany` WHERE name=%s FOR UPDATE",
                         (company,), as_dict=True)
    _require(len(rows) == 1, "Financial mapping Company must exist.")


def _master_validated(doctype, record, company):
    row = _master(doctype, record)
    _require(row.company == company and not row.is_group and not row.disabled,
             "Financial mapping master must be a non-disabled Company-owned leaf.")
    if doctype == "Account":
        _require(row.root_type == "Expense", "Expense mapping requires an Expense-root Account.")


def _scope_values(kind, company, source):
    scope_dt, _, source_field, _, source_dt, _ = _kind(kind)
    if kind == "Expense":
        rows = frappe.db.sql("SELECT name, is_group FROM `tabItem Group` WHERE name=%s FOR UPDATE",
                             (source,), as_dict=True)
        _require(len(rows) == 1 and not rows[0].is_group,
                 "Expense mapping requires an exact leaf Item Group.")
    else:
        rows = frappe.db.sql(
            "SELECT name, company, disabled, is_group FROM `tabDepartment` "
            "WHERE name=%s FOR UPDATE", (source,), as_dict=True,
        )
        _require(len(rows) == 1 and rows[0].company == company
                 and not rows[0].disabled and not rows[0].is_group,
                 "Center mapping requires an eligible Company beneficiary Department.")
    return scope_dt, source_field


def _lock_scopes(kind, company, source):
    scope_dt, source_field = _scope_values(kind, company, source)
    rows = frappe.db.sql(
        "SELECT name, company FROM `tab" + scope_dt + "` WHERE company=%s AND " +
        source_field + "=%s ORDER BY name FOR UPDATE",
        (company, source), as_dict=True,
    )
    _require(len(rows) <= 1, "Overlapping mapping scopes require review.")
    return rows[0].name if rows else None


def _get_or_create_scope(kind, company, source):
    # Company row is locked first, including when this scope does not yet exist.
    name = _lock_scopes(kind, company, source)
    if name:
        return name
    scope_dt, _, source_field, _, _, _ = _kind(kind)
    values = {"doctype": scope_dt, "company": company, source_field: source}
    if kind == "Center":
        values.update(selector_kind="Department Only", selector_key="DEPARTMENT_ONLY")
    return frappe.get_doc(values).insert(ignore_permissions=True).name


def _lock_revision(revision_doctype, name):
    rows = frappe.db.sql(
        "SELECT name, mapping_scope, revision_no, owner, modified, approval_state, "
        "approval_reference, approved_by, approved_at, effective_from, effective_until, "
        "supersedes, revocation_reason, "
        + ("expense_account" if revision_doctype == "CCPC Expense Mapping Revision" else "cost_center") +
        " FROM `tab" + revision_doctype + "` WHERE name=%s FOR UPDATE",
        (name,), as_dict=True,
    )
    _require(len(rows) == 1, "Financial mapping revision not found.")
    return rows[0]


@frappe.whitelist(methods=["POST"])
def propose_mapping(kind, company, source, value, effective_from=None, effective_until=None):
    """Create a *Draft* revision as an authenticated permitted Finance preparer."""
    _, rev_dt, _, value_field, _, master_dt = _kind(kind)
    company = _canonical(company, "Company")
    source = _canonical(source, "Mapping source")
    value = _canonical(value, "Proposed finance account or center")
    preparer = _canonical(frappe.session.user, "Preparer")
    _real_authority(preparer, "CCPC Mapping Preparer", company)
    _company_lock(company)
    _scope_values(kind, company, source)
    _master_validated(master_dt, value, company)
    start = _utc(effective_from)
    until = _utc(effective_until)
    if until is not None:
        _require(start is not None and until > start, "Invalid proposed approval interval.")
    # An empty start is an explicit request for "effective at the approval instant".
    scope = _get_or_create_scope(kind, company, source)
    existing = frappe.db.sql(
        "SELECT revision_no FROM `tab" + rev_dt + "` "
        "WHERE mapping_scope=%s ORDER BY revision_no FOR UPDATE",
        (scope,), as_dict=True,
    )
    drafts = frappe.db.sql("SELECT name FROM `tab" + rev_dt + "` WHERE mapping_scope=%s AND approval_state='Draft' ORDER BY name FOR UPDATE", (scope,), as_dict=True)
    _require(not drafts, "One unresolved Draft per financial scope is permitted.")
    number = max([int(row.revision_no) for row in existing], default=0) + 1
    proposed = frappe.get_doc({
        "doctype": rev_dt, "mapping_scope": scope, "revision_no": number,
        value_field: value, "approval_state": "Draft",
        "effective_from": effective_from, "effective_until": effective_until,
    }).insert(ignore_permissions=True)
    _require(proposed.owner == preparer,
             "Proposal must be owned by its authenticated Finance preparer.")
    return {"name": proposed.name, "state": "Draft", "revision_no": number}


@frappe.whitelist(methods=["POST"])
def get_mapping_review(kind, revision):
    """Read exact server-side draft fields and optimistic modified token."""
    scope_dt, rev_dt, source_field, value_field, _, _ = _kind(kind)
    revision = _canonical(revision, "Mapping revision")
    # Non-locking locator only; scope is locked before final authoritative read.
    proposed_scope = frappe.db.get_value(rev_dt, revision, "mapping_scope")
    _require(bool(proposed_scope), "Unknown mapping revision.")
    company = frappe.db.get_value(scope_dt, proposed_scope, "company")
    _require(bool(company), "Unknown mapping Company.")
    actor = frappe.session.user
    _real_authority(actor, "CCPC Accounting Mapping Approver", company)
    _company_lock(company)
    scope = frappe.db.sql(
        "SELECT name, company, " + source_field + " FROM `tab" + scope_dt +
        "` WHERE name=%s FOR UPDATE", (proposed_scope,), as_dict=True,
    )
    _require(len(scope) == 1 and scope[0].company == company, "Mapping scope changed.")
    row = _lock_revision(rev_dt, revision)
    _require(row.mapping_scope == proposed_scope, "Revision moved between financial scopes.")
    return {"name": row.name, "company": company, "source": scope[0][source_field],
            "value": row[value_field], "owner": row.owner,
            "state": row.approval_state, "effective_from": row.effective_from,
            "effective_until": row.effective_until, "modified": str(row.modified),
            "supersedes": row.supersedes, "retirement_reason": row.revocation_reason,
            "change": ("Retire" if row.revocation_reason else "Replace") if row.supersedes else "New"}


@frappe.whitelist(methods=["POST"])
def approve_mapping(kind, revision, expected_modified, approval_reference):
    """Authenticated, scope-serialized maker-checker approval; never an Admin bypass.

    Approval starts no earlier than the server-owned approval instant. An empty
    Draft start selects "effective now"; earlier proposed times are clamped to
    the approval instant. Future starts remain future. No backdated approval.
    Approved revisions are not saved through the ordinary document controller.
    """
    scope_dt, rev_dt, source_field, value_field, _, master_dt = _kind(kind)
    revision = _canonical(revision, "Mapping revision")
    approval_reference = _canonical(approval_reference, "Accounting approval reference")
    _require(len(approval_reference) <= 140, "Accounting approval reference is too long.")
    expected_modified = _canonical(expected_modified, "Reviewed draft version")
    # Lookup to locate the stable Company parent before locking; never trust
    # this first read as signed financial or authorization evidence.
    scope_id = frappe.db.get_value(rev_dt, revision, "mapping_scope")
    _require(bool(scope_id), "Unknown mapping revision.")
    company = frappe.db.get_value(scope_dt, scope_id, "company")
    _require(bool(company), "Missing mapping Company.")
    checker = _canonical(frappe.session.user, "Mapping approver")
    _real_authority(checker, "CCPC Accounting Mapping Approver", company)
    _company_lock(company)
    scopes = frappe.db.sql(
        "SELECT name, company, " + source_field +
        " FROM `tab" + scope_dt + "` WHERE name=%s FOR UPDATE",
        (scope_id,), as_dict=True,
    )
    scope = scopes[0] if len(scopes) == 1 else None
    _require(scope is not None and scope.company == company, "Financial mapping scope has changed.")
    _scope_values(kind, company, scope[source_field])
    doc = _lock_revision(rev_dt, revision)
    _require(doc.mapping_scope == scope_id and doc.approval_state == "Draft",
             "Only unchanged Draft mapping revisions may be approved.")
    _require(str(doc.modified) == expected_modified,
             "Mapping was modified since Finance review; review it again.")
    _require(not doc.approved_by and not doc.approved_at,
             "Draft contains forged approval history.")
    _require(not doc.supersedes and not doc.revocation_reason,
             "Lifecycle drafts require independent change approval, not initial approval.")
    maker, checker = assert_independent_review(doc.owner, checker)
    _real_authority(maker, "CCPC Mapping Preparer", company)
    _master_validated(master_dt, doc[value_field], company)
    from ccpcerp.ccpc_erp.bc_finance_evidence import verify_decision, consume_decision
    signed = verify_decision(approval_reference, kind, "Approve", company, scope, doc, checker, value_field)
    approval_reference = signed.name
    approved_at = now_datetime()
    # Store ERPNext site-local naive Datetime for Frappe. Comparison is UTC.
    proposed_from = _utc(doc.effective_from)
    approved_utc = _utc(approved_at)
    _require(approved_utc is not None, "Missing approval timestamp.")
    start_utc = max([d for d in (proposed_from, approved_utc) if d is not None])
    from frappe.utils import get_datetime
    # The original site-local proposed date is used only if in the future.
    effective_from = (get_datetime(doc.effective_from) if proposed_from
                      and proposed_from > approved_utc else approved_at)
    effective_until = get_datetime(doc.effective_until) if doc.effective_until else None
    _require(effective_until is None or _utc(effective_until) > start_utc,
             "Proposed end must follow server-owned activation instant.")
    other = frappe.db.sql(
        "SELECT name, effective_from, effective_until FROM `tab" + rev_dt + "` "
        "WHERE mapping_scope=%s AND approval_state='Approved' "
        "ORDER BY name FOR UPDATE", (scope_id,), as_dict=True,
    )
    for row in other:
        _require(not intervals_overlap(
            start_utc, _utc(effective_until), _utc(row.effective_from),
            _utc(row.effective_until),
        ), "Approved financial mapping intervals overlap; revise the proposal.")
    # All values below are server-side, no caller-supplied actor/timestamp/state.
    frappe.db.sql(
        "UPDATE `tab" + rev_dt + "` "
        "SET approval_state='Approved', approved_by=%s, approved_at=%s, "
        "approval_reference=%s, effective_from=%s WHERE name=%s AND approval_state='Draft'",
        (checker, approved_at, approval_reference, effective_from, revision),
    )
    # Durable review trail, stored in the same transaction. A failed Version
    # insertion fails the whole approval request; no implicit commit.
    audit = frappe.get_doc({
        "doctype": "Version", "ref_doctype": rev_dt, "docname": revision,
        "data": frappe.as_json({
            "changed": [
                ["approval_state", "Draft", "Approved"],
                ["approved_by", None, checker],
                ["approved_at", None, str(approved_at)],
                ["effective_from", str(doc.effective_from or ""), str(effective_from)],
                ["approval_reference", str(doc.approval_reference or ""), approval_reference],
            ],
            # Capture the entire reviewed Accounting decision, not just the
            # status transition. This is an audit snapshot, NOT cryptographic
            # proof against a privileged database administrator.
            "ccpc_mapping_decision": {
                "kind": kind,
                "revision": revision,
                "scope": scope_id,
                "company": company,
                "source": scope[source_field],
                "value": doc[value_field],
                "prepared_by": maker,
                "approved_by": checker,
                "approved_at": str(approved_at),
                "effective_from": str(effective_from),
                "effective_until": str(effective_until or ""),
                "approval_reference": approval_reference,
            },
        }),
    })
    audit.insert(ignore_permissions=True)
    consumed = consume_decision(signed, kind, "Approve", revision, revision, checker)
    confirmed = _lock_revision(rev_dt, revision)
    _require(confirmed.approval_state == "Approved" and confirmed.approved_by == checker
             and confirmed.approval_reference == approval_reference,
             "Finance approval write was not persisted correctly.")
    return {"name": revision, "state": "Approved", "approved_by": checker,
            "approved_at": str(approved_at), "effective_from": str(effective_from),
            "version": audit.name, "decision_use": consumed}

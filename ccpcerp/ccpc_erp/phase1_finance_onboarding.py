# Copyright (c) 2026, CCPC and contributors
"""Company-scoped Desk onboarding for initial Phase 1 financial mappings."""

from functools import wraps

import frappe
from frappe import _
from frappe.utils import now_datetime

from ccpcerp.ccpc_erp.bc_finance_evidence import sign_mapping_decision
from ccpcerp.ccpc_erp.bc_issue_runtime import (
    _assert_approval_provenance,
    _canonical,
    _effective_mapping_end,
    _lock_revisions,
    _real_authority,
    _require,
    _utc,
)
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError, is_effective_after_approval
from ccpcerp.ccpc_erp.bc_mapping_workflow import (
    _company_lock,
    _kind,
    _lock_scopes,
    _master_validated,
    approve_mapping,
    get_mapping_review,
    propose_mapping,
)

PREPARER = "CCPC Mapping Preparer"
CHECKER = "CCPC Accounting Mapping Approver"


def _finance_post(handler):
    @wraps(handler)
    def request(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except MappingPolicyError as exc:
            frappe.throw(_(str(exc)))

    return frappe.whitelist(methods=["POST"])(request)


def _actor_roles():
    user = frappe.session.user
    _require(user not in (None, "Guest", "Administrator") and frappe.db.get_value("User", user, "enabled"),
             "An enabled named Finance mapping user is required.")
    assigned = set(frappe.get_all("Has Role", filters={"parenttype": "User", "parent": user}, pluck="role"))
    roles = assigned.intersection((PREPARER, CHECKER))
    _require(roles and "Stockroom Personnel" not in assigned, "An explicit Finance mapping role is required.")
    return roles


def _authorize(company):
    roles = _actor_roles()
    _company_lock(company)
    _real_authority(frappe.session.user, PREPARER if PREPARER in roles else CHECKER, company)
    return roles


@_finance_post
def get_pilot_context():
    roles = _actor_roles()
    companies = frappe.get_all("User Permission", filters={
        "user": frappe.session.user, "allow": "Company", "apply_to_all_doctypes": 1,
        "applicable_for": ["is", "not set"],
    }, pluck="for_value")
    existing = frappe.get_all("Company", filters={"name": ["in", companies]}, pluck="name", limit=0) if companies else []
    return {"companies": sorted(set(existing).intersection(companies)),
            "can_propose": PREPARER in roles, "can_review": CHECKER in roles}


@_finance_post
def search_pilot_master(kind, company, field, text=""):
    _, _, _, _, source_type, value_type = _kind(kind)
    roles = _authorize(company)
    _require(field in ("source", "value"), "Unknown mapping field.")
    _require(field == "source" or PREPARER in roles, "Only Finance preparers can select a proposed mapping value.")
    _require(isinstance(text, str) and len(text) <= 140, "Search text is too long.")
    doctype = source_type if field == "source" else value_type
    filters = {"is_group": 0, "name": ["like", f"%{text}%"]}
    if doctype != "Item Group":
        filters.update(company=company, disabled=0)
    if doctype == "Account":
        filters["root_type"] = "Expense"
    return frappe.get_all(doctype, filters=filters, pluck="name", order_by="name asc", limit=20)


@_finance_post
def get_pilot_coverage(kind, company, source):
    scope_type, revision_type, _, value_field, _, value_type = _kind(kind)
    _authorize(company)
    _canonical(source, "Mapping source")
    scope = _lock_scopes(kind, company, source)
    if not scope:
        return {"active": None, "draft": None}
    if kind == "Center":
        selector = frappe.db.get_value(scope_type, scope, ["selector_kind", "selector_key"], as_dict=True)
        _require(selector.selector_kind == "Department Only" and selector.selector_key == "DEPARTMENT_ONLY",
                 "Explicit Cost Center selection is not activated.")

    now = _utc(now_datetime())
    active = []
    drafts = []
    for row in _lock_revisions(revision_type, scope):
        if row.approval_state == "Draft":
            drafts.append(row.name)
        elif row.approval_state == "Approved" and is_effective_after_approval(
            _utc(row.effective_from), _effective_mapping_end(kind, company, scope, row),
            _utc(row.approved_at), now,
        ):
            _assert_approval_provenance(row, company)
            _master_validated(value_type, row[value_field], company)
            active.append({"revision": row.name, "value": row[value_field]})
    _require(len(active) <= 1 and len(drafts) <= 1, "Mapping scope has conflicting revisions and requires Finance review.")
    return {"active": active[0] if active else None, "draft": drafts[0] if drafts else None}


@_finance_post
def propose_pilot_mapping(kind, company, source, value):
    """Use the initial proposal workflow with activation at approval."""
    _require(PREPARER in _authorize(company), "Only Finance preparers can propose a pilot mapping.")
    coverage = get_pilot_coverage(kind, company, source)
    _require(not coverage["active"], "This scope already has an active mapping. Use the Finance change workflow.")
    return propose_mapping(kind, company, source, value)


@_finance_post
def review_pilot_mapping(kind, revision):
    return get_mapping_review(kind, revision)


@_finance_post
def sign_and_approve_initial_mapping(kind, revision, expected_modified):
    """Approve the exact reviewed initial proposal in one auditable transaction."""
    marker = "ccpc_initial_mapping_approval"
    frappe.db.savepoint(marker)
    try:
        signed = sign_mapping_decision(kind, revision, expected_modified, "Approve")
        return approve_mapping(kind, revision, expected_modified, signed["name"])
    except Exception:
        frappe.db.rollback(save_point=marker)
        raise

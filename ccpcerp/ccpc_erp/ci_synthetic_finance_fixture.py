# Copyright (c) 2026, CCPC and contributors
"""Synthetic signed-looking B/C rows for a *disposable* CircleCI SQL fixture.

Not a Finance approval workflow. The real mapping revision controllers
intentionally reject Approved state. This helper exercises the downstream
locking/attribution guards using test-only SQL state, and is never imported
by runtime hooks or executed outside the exact CI runner/site identity.
"""
import frappe
from frappe.utils import add_to_date, now_datetime

from ccpcerp.ccpc_erp.ci_disposable_site_guard import is_reviewed_circleci_disposable_site


MAKER = "circleci-finance-maker@invalid.test"
CHECKER = "circleci-finance-checker@invalid.test"
REFERENCE = "CIRCLECI-SYNTHETIC-APPROVAL-NOT-FINANCE-AUTHORIZATION"


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


def _role(name):
    if not frappe.db.exists("Role", name):
        frappe.get_doc({"doctype": "Role", "role_name": name}).insert(ignore_permissions=True)


def _actor(email, role, company):
    _role(role)
    if not frappe.db.exists("User", email):
        frappe.get_doc({
            "doctype": "User", "email": email,
            "first_name": "CircleCI Synthetic " + ("Maker" if email == MAKER else "Checker"),
            "enabled": 1, "user_type": "System User", "send_welcome_email": 0,
        }).insert(ignore_permissions=True)
    user = frappe.get_doc("User", email)
    _require(int(user.enabled) == 1, "Synthetic mapping actor must be enabled")
    _require(not any(r.role == "Stockroom Personnel" for r in user.roles),
             "Synthetic Finance actor may not also be a Stockroom issuer")
    if not any(r.role == role for r in user.roles):
        user.append("roles", {"role": role})
        user.save(ignore_permissions=True)
    if not frappe.db.exists("User Permission", {
        "user": email, "allow": "Company", "for_value": company,
        "apply_to_all_doctypes": 1,
    }):
        frappe.get_doc({
            "doctype": "User Permission", "user": email, "allow": "Company",
            "for_value": company, "apply_to_all_doctypes": 1,
        }).insert(ignore_permissions=True)


def _scope(doctype, filters):
    name = frappe.db.get_value(doctype, filters, "name")
    if name:
        return name
    return frappe.get_doc({"doctype": doctype, **filters}).insert(ignore_permissions=True).name


def _synthetic_revision(doctype, scope, value_field, value, approved_at, effective_from):
    existing = frappe.db.get_value(doctype, {"mapping_scope": scope, "revision_no": 1},
                                   ["name", value_field, "approval_state", "owner",
                                    "approved_by", "approval_reference"], as_dict=True)
    if existing:
        _require(existing[value_field] == value
                 and existing.approval_state == "Approved"
                 and existing.owner == MAKER
                 and existing.approved_by == CHECKER
                 and existing.approval_reference == REFERENCE,
                 "Existing fixture approval differs from explicit synthetic Finance identity")
        return existing.name
    # Save a legitimate DRAFT through the real DocType validation, as the
    # production controller must NOT allow a user-driven approval.
    previous = frappe.session.user
    try:
        frappe.set_user(MAKER)
        revision = frappe.get_doc({
            "doctype": doctype, "mapping_scope": scope, "revision_no": 1,
            value_field: value, "approval_state": "Draft",
            "effective_from": effective_from,
        }).insert(ignore_permissions=True)
    finally:
        frappe.set_user(previous)
    _require(revision.owner == MAKER, "Synthetic maker must own the draft")
    # TEST-ONLY SQL transition. This deliberately does not claim the unavailable
    # Finance approval service works. Exact CircleCI/site guard above is mandatory.
    frappe.db.set_value(doctype, revision.name, {
        "approval_state": "Approved", "approved_by": CHECKER,
        "approved_at": approved_at, "approval_reference": REFERENCE,
    }, update_modified=False)
    return revision.name


def seed_circleci_test_only_mapping(company, item_group, beneficiary_department):
    _require(is_reviewed_circleci_disposable_site(getattr(frappe.local, "site", None)),
             "Synthetic Finance approval fixture only permitted on exact disposable CircleCI site")
    _require(company == "_Test Company" and item_group == "_Test Item Group",
             "Synthetic Finance mapping is restricted to canonical ERPNext test masters")
    _require(frappe.db.get_value("Department", beneficiary_department, "company") == company,
             "Synthetic beneficiary must belong to disposable test company")
    expense = frappe.db.get_value("Account", {
        "company": company, "root_type": "Expense", "is_group": 0,
        "disabled": 0,
    }, "name")
    center = frappe.db.get_value("Cost Center", {
        "company": company, "is_group": 0, "disabled": 0,
    }, "name")
    _require(bool(expense) and bool(center), "Disposable company needs leaf Expense and Cost Center")
    _actor(MAKER, "CCPC Mapping Preparer", company)
    _actor(CHECKER, "CCPC Accounting Mapping Approver", company)
    scope_b = _scope("CCPC Expense Mapping Scope", {
        "company": company, "item_group": item_group,
    })
    scope_c = _scope("CCPC Center Mapping Scope", {
        "company": company, "beneficiary_department": beneficiary_department,
        "selector_kind": "Department Only", "selector_key": "DEPARTMENT_ONLY",
    })
    # Effective only after a simulated independent approval timestamp.
    approved_at = add_to_date(now_datetime(), minutes=-5, as_datetime=True)
    effective_from = add_to_date(now_datetime(), minutes=-10, as_datetime=True)
    revision_b = _synthetic_revision("CCPC Expense Mapping Revision", scope_b,
                                     "expense_account", expense, approved_at, effective_from)
    revision_c = _synthetic_revision("CCPC Center Mapping Revision", scope_c,
                                     "cost_center", center, approved_at, effective_from)
    _require(revision_b != revision_c, "Synthetic scope revisions must be distinct")
    return {"expense_account": expense, "cost_center": center,
            "expense_revision": revision_b, "center_revision": revision_c}

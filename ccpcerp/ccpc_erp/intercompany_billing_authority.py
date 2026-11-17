# Copyright (c) 2026, CCPC and contributors
"""Fail-closed named-user authority for intercompany material billing.

Role definitions grant no named users access; each Company User Permission
must be assigned separately by an authorized administrator in the target site.
No Administrator/System Manager break-glass bypass exists in normal methods.
"""
import frappe
from frappe import _


PREPARER_ROLE = "CCPC Billing Preparer"
CHECKER_ROLE = "Finance Controller"


def require_billing_actor(role, issuing_company, requesting_company):
    """Require a named enabled user, explicit role and BOTH Company grants."""
    actor = frappe.session.user
    if (
        not isinstance(actor, str)
        or not actor.strip()
        or actor.casefold() in {"guest", "administrator"}
    ):
        frappe.throw(_("A named billing actor is required."), exc=frappe.PermissionError)

    enabled = frappe.db.get_value("User", actor, "enabled")
    if not enabled:
        frappe.throw(_("Billing actor is not an enabled User."), exc=frappe.PermissionError)

    if not frappe.db.exists("Has Role", {
        "parenttype": "User", "parent": actor, "role": role,
    }):
        frappe.throw(
            _("Billing actor lacks the required explicit role."),
            exc=frappe.PermissionError,
        )

    if frappe.db.exists("Has Role", {
        "parenttype": "User", "parent": actor, "role": "Stockroom Personnel",
    }):
        frappe.throw(
            _("Stockroom Personnel cannot authorize intercompany billing."),
            exc=frappe.PermissionError,
        )

    if not issuing_company or not requesting_company or issuing_company == requesting_company:
        frappe.throw(_("Two distinct billing Companies are required."))

    for company in (issuing_company, requesting_company):
        # Match the existing Finance mapping workflow's strict Company grant:
        # a generic User Permission for Company applying to all DocTypes.
        grants = frappe.db.sql(
            """SELECT name FROM `tabUser Permission`
            WHERE user=%s AND allow='Company' AND for_value=%s
              AND apply_to_all_doctypes=1 AND IFNULL(applicable_for, '')=''
            LIMIT 1 FOR UPDATE""",
            (actor, company),
            as_dict=True,
        )
        if not grants:
            frappe.throw(
                _("Billing actor lacks explicit permissions for a required Company."),
                exc=frappe.PermissionError,
            )
    return actor


def require_distinct_billing_review(maker, checker):
    if (
        not isinstance(maker, str) or not maker.strip()
        or not isinstance(checker, str) or not checker.strip()
        or maker.casefold() == checker.casefold()
        or maker.casefold() in {"administrator", "guest"}
        or checker.casefold() in {"administrator", "guest"}
    ):
        frappe.throw(
            _("Billing preparation and Finance approval require two different named users."),
            exc=frappe.PermissionError,
        )
    return maker, checker

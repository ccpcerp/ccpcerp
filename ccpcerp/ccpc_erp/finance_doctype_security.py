# Copyright (c) 2026, CCPC and contributors
"""Deny standard access to private Finance mapping evidence / revision DocTypes.

Frappe v16 DocType.before_insert silently adds a System Manager DocPerm to
non-child DocTypes whose JSON permissions are []. Those default rows are NOT
an Accounting authorization and must be removed after controlled app install
and migration. No Finance mapping event, ERPNext User/Role or master is changed.

All DB cleanup is a two-phase, fail-closed scan of the NINE explicitly pinned
standard DocTypes. Unexpected permissions, including any Custom DocPerm, stop
the whole operation BEFORE deleting even the first default row. This routine
must never be called by a browser or a request; only install/migrate hooks.
"""
import frappe


PROTECTED_FINANCE_DOCTYPES = (
    "CCPC Expense Mapping Scope",
    "CCPC Expense Mapping Revision",
    "CCPC Center Mapping Scope",
    "CCPC Center Mapping Revision",
    "CCPC Accounting Mapping Decision",
    "CCPC Mapping Decision Use",
    "CCPC Mapping Rejection Event",
    "CCPC Mapping Retirement Request",
    "CCPC Mapping Retirement Event",
)

# Actual frappe/frappe version-16 DocType.before_insert default DocPerm.
# A row that does not have this exact signature must NOT be auto-deleted.
DEFAULT_RIGHTS = (
    "read", "write", "create", "delete", "report", "export",
    "share", "print", "email",
)
OTHER_RIGHTS = ("select", "submit", "cancel", "amend", "import")
SCAN_COLUMNS = (
    "name", "parent", "parenttype", "parentfield", "role", "permlevel",
    "if_owner", *DEFAULT_RIGHTS, *OTHER_RIGHTS,
)


def deny_protected_finance_document_access(doc, ptype=None, user=None, debug=False):
    """Deny user-facing document access, including shares and System Manager.

    Internal ERPNext accounting services access these DocTypes exclusively
    through their audited, role-checked server paths and ignore_permissions
    when writing immutably signed evidence. Administrator's built-in superuser
    override is a framework constraint, not a valid Finance approval route.
    """
    if doc.doctype not in PROTECTED_FINANCE_DOCTYPES:
        return None
    return False


def deny_protected_finance_list_access(user=None, doctype=None):
    """Deny list/report/query discovery, even if a row was shared."""
    if doctype not in PROTECTED_FINANCE_DOCTYPES:
        return None
    return "1=0"


def _is_framework_default(row):
    return (
        row.get("parenttype") == "DocType"
        and row.get("parentfield") == "permissions"
        and row.get("role") == "System Manager"
        and int(row.get("permlevel") or 0) == 0
        and int(row.get("if_owner") or 0) == 0
        and all(int(row.get(field) or 0) == 1 for field in DEFAULT_RIGHTS)
        and all(int(row.get(field) or 0) == 0 for field in OTHER_RIGHTS)
    )


def harden_finance_mapping_doctype_permissions():
    """Fail-closed install/migrate cleanup: never override user-set rights.

    This is not a runtime endpoint and is never called by ordinary mapping
    preparation/approval or an inventory issue. On a partially customized
    site stop with a safe diagnostic, preserve existing permissions unchanged,
    and request a separate Finance-approved migration plan.
    """
    # Only these framework lifecycle hooks may perform a permission cleanup.
    # A privileged manual console invocation is deliberately not supported.
    if not (getattr(frappe.flags, "in_install", False)
            or getattr(frappe.flags, "in_migrate", False)):
        raise RuntimeError("Protected Finance DocPerm cleanup requires controlled app install/migrate.")

    # Scan every protected DocType before ANY mutation.
    default_row_names = []
    columns = ", ".join("`" + field + "`" for field in SCAN_COLUMNS)
    for doctype in PROTECTED_FINANCE_DOCTYPES:
        if not frappe.db.exists("DocType", doctype):
            raise RuntimeError("Missing protected Finance DocType during permissions setup: " + doctype)

        source_rows = frappe.db.sql(
            "SELECT " + columns + " FROM `tabDocPerm` "
            "WHERE parent=%s AND parenttype='DocType' ORDER BY name FOR UPDATE",
            (doctype,), as_dict=True,
        )
        customized_rows = frappe.db.sql(
            "SELECT name FROM `tabCustom DocPerm` WHERE parent=%s "
            "ORDER BY name FOR UPDATE",
            (doctype,), as_dict=True,
        )
        if customized_rows:
            raise RuntimeError(
                "Protected Finance DocType has separately customized permissions; "
                "manual Finance-governed review required: " + doctype
            )
        for row in source_rows:
            if not _is_framework_default(row):
                raise RuntimeError(
                    "Protected Finance DocType has non-default permissions; "
                    "manual Finance-governed review required: " + doctype
                )
            default_row_names.append((doctype, row["name"]))

    # A second pass cannot silently delete any unexpected role, permission,
    # custom policy or user record. Use the full pinned pair, not a broad filter.
    for doctype, rowname in default_row_names:
        frappe.db.delete("DocPerm", {"name": rowname, "parent": doctype,
                                    "parenttype": "DocType",
                                    "role": "System Manager"})
    for doctype in PROTECTED_FINANCE_DOCTYPES:
        frappe.clear_cache(doctype=doctype)
        remaining = frappe.db.sql(
            "SELECT name FROM `tabDocPerm` WHERE parent=%s "
            "AND parenttype='DocType' LIMIT 1",
            (doctype,), as_dict=True,
        )
        if remaining or frappe.db.exists("Custom DocPerm", {"parent": doctype}):
            raise RuntimeError("Protected Finance DocType permissions not empty: " + doctype)

# Copyright (c) 2026, CCPC and contributors
# Approved Decision A beneficiary attribution only; B/C account mappings pending.

import frappe
from erpnext import is_perpetual_inventory_enabled
from frappe import _
from frappe.utils import cint

from ccpcerp.ccpc_erp.equipment_costing import validate_department_company


def resolve_beneficiary_department(slip):
    """Resolve actual beneficiary, never silently fall back to requester.

    Explicit Work For Department takes precedence; Equipment Registry
    Department may supply it only when no explicit source exists. Conflicts
    are blocked pending a separately authorized and audited resolution.
    """
    equipment_department = None
    if slip.get("equipment"):
        equipment = frappe.db.get_value(
            "Equipment Registry", slip.equipment,
            ["company", "department"], as_dict=True,
        )
        if not equipment or equipment.company != slip.company:
            frappe.throw(_("Withdrawal Slip Equipment must exist in its issuing Company."))
        equipment_department = equipment.department

    explicit = slip.get("work_for_department") or None
    if explicit and equipment_department and explicit != equipment_department:
        frappe.throw(
            _("Beneficiary Department conflicts with the Equipment Registry Department. "
              "Posting requires a separately authorized audited resolution.")
        )
    beneficiary = explicit or equipment_department
    if not beneficiary:
        frappe.throw(
            _("Actual beneficiary Department must be established before Material Issue posting; "
              "the requesting Department is not an accounting fallback.")
        )
    validate_department_company(
        beneficiary, slip.company, _("Material Issue beneficiary"), require_eligible=True,
    )
    return beneficiary


def require_department_gl_posting(company):
    """Require ERPNext to post the issue value to GL with its Department."""
    from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
        get_accounting_dimensions,
    )

    if not cint(is_perpetual_inventory_enabled(company)):
        frappe.throw(_("Enable Perpetual Inventory for the issuing Company before posting a Withdrawal Slip."))
    if "department" not in get_accounting_dimensions():
        frappe.throw(_("Department GL Accounting Dimension must be configured before controlled Material Issue posting."))
    for doctype in ("Stock Entry", "Stock Entry Detail", "GL Entry"):
        if not frappe.get_meta(doctype).has_field("department"):
            frappe.throw(_("Missing active Department GL Accounting Dimension field on {0}.").format(doctype))


def apply_issue_beneficiary(entry, slip):
    """Populate actual beneficiary on header/items at original issue creation."""
    beneficiary = resolve_beneficiary_department(slip)
    require_department_gl_posting(slip.company)
    entry.department = beneficiary
    for row in entry.get("items") or []:
        row.department = beneficiary
    return beneficiary


def validate_issue_beneficiary(entry, slip):
    """Fail closed on direct, forged or stale voucher submission."""
    beneficiary = resolve_beneficiary_department(slip)
    require_department_gl_posting(slip.company)
    if entry.get("department") != beneficiary:
        frappe.throw(_("Material Issue header Department differs from approved actual beneficiary."))
    for row in entry.get("items") or []:
        if row.get("department") != beneficiary:
            frappe.throw(_("Material Issue row {0}: Department differs from actual beneficiary.").format(row.idx))
    return beneficiary

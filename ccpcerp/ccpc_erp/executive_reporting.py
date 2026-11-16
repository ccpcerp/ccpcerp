# Copyright (c) 2026, CCPC and contributors
"""Four-Company report-only executive gateway; never grants source DocType rights.

The ordinary native Script Reports retain their independent System Manager /
source-DocPerm protections. An executive may access the same report calculations
ONLY inside this one authenticated POST endpoint with strict Company filters.
No Company, Stock Entry, SLE, GL or Invoice role permissions are installed here.
"""
from contextvars import ContextVar
from importlib import import_module

import frappe
from frappe import _

EXECUTIVE_ROLE = "CCPC Group Executive Reporting"
COMPANIES = {
    "Cagayan Corn Products Corporation": "CCPC",
    "Ricor Mills Corporation": "RMC",
    "GSL Realty Corporation": "GSL",
    "Woodland Farms Corporation": "WFC",
}
# Fixed source-module mapping: never evaluate a user-provided Python module path.
REPORTS = {
    # Executive Page exposes Company/date only. Native Department/Equipment
    # filters require their own master-record read permissions; reject them
    # at this gateway rather than silently granting new DocType rights.
    "department": ("ccpc_department_material_consumption", {"company", "from_date", "to_date"}),
    "equipment": ("ccpc_equipment_job_material_activity", {"company", "from_date", "to_date"}),
    "intercompany": ("ccpc_intercompany_withdrawal_billing_status", {"issuing_company", "requesting_company", "from_date", "to_date"}),
    "stock_gl": ("ccpc_company_stock_pnl_gl_reconciliation", {"company", "from_date", "to_date"}),
}
_CONTEXT = ContextVar("ccpc_authorized_executive_report", default=None)


def in_executive_report():
    """Internal-only indicator scoped to the authenticated gateway's execute call."""
    return _CONTEXT.get() is not None


def _deny():
    frappe.throw(_("CCPC executive reporting access denied."), frappe.PermissionError)


def _actor_is_authorized():
    user = getattr(frappe.session, "user", None)
    if not user or user in ("Administrator", "Guest"):
        _deny()
    if EXECUTIVE_ROLE not in frappe.get_roles(user):
        _deny()
    record = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
    if not record or not record.enabled or record.user_type != "System User":
        _deny()


def _check_group_company(company):
    if not isinstance(company, str) or company not in COMPANIES:
        _deny()
    # Fail closed when a site has a misleading or renumbered Company record.
    if frappe.db.get_value("Company", company, "abbr") != COMPANIES[company]:
        _deny()


def _filters(report_key, filters):
    if report_key not in REPORTS:
        _deny()
    if isinstance(filters, str):
        try:
            filters = frappe.parse_json(filters)
        except (ValueError, TypeError):
            _deny()
    if not isinstance(filters, dict):
        _deny()
    if not set(filters).issubset(REPORTS[report_key][1]):
        _deny()
    if any(not isinstance(value, str) or len(value) > 140 for value in filters.values()):
        _deny()
    if not filters.get("from_date") or not filters.get("to_date"):
        _deny()
    if report_key == "intercompany":
        issuer = filters.get("issuing_company")
        requester = filters.get("requesting_company")
        _check_group_company(issuer)
        _check_group_company(requester)
        if issuer == requester:
            _deny()
    else:
        issuer = filters.get("company")
        requester = None
        _check_group_company(issuer)
    return filters, issuer, requester


def _assert_returned_companies(report_key, rows, issuer, requester):
    """Verify returned rows and linked master provenance before any response."""
    # The equipment report can emit many rows referring to the same frozen
    # Equipment Registry IDs; resolve each distinct ID just once per request.
    checked_equipment = set()
    for row in rows:
        if not isinstance(row, dict):
            _deny()
        if row.get("issuing_company", row.get("company")) != issuer:
            _deny()
        if requester is not None and row.get("requesting_company") != requester:
            _deny()
        if report_key == "intercompany":
            # Never reveal even a foreign invoice/billing reference when an
            # inconsistent legacy claim points outside the authorized pair.
            flags = set((row.get("source_quality") or "").split(", "))
            if flags.intersection({
                "BILLING_COMPANY_MISMATCH",
                "SALES_COMPANY_OR_LINK_INVALID",
                "PURCHASE_COMPANY_OR_LINK_INVALID",
            }):
                _deny()
        if report_key == "department":
            # The source report intentionally preserves imperfect legacy
            # Department links. Existing foreign-company masters must not
            # have their names exposed through this executive report.
            for field in ("detail_department", "header_department"):
                name = row.get(field)
                if name:
                    actual = frappe.db.get_value("Department", name, "company")
                    if actual and actual != issuer:
                        _deny()
        if report_key == "equipment":
            # Both snapshot fields are included in the report response, even
            # when legacy evidence contains a conflicting or missing pair.
            # A value is an Equipment Registry name (equipment_number); do
            # not disclose foreign OR unresolvable identifiers to executives.
            for field in ("source_equipment", "source_equipment_code"):
                name = row.get(field)
                if name is None or name == "":
                    continue  # An absent snapshot contains no identifier.
                if not isinstance(name, str):
                    _deny()
                if name not in checked_equipment:
                    actual = frappe.db.get_value("Equipment Registry", name, "company")
                    if actual != issuer:
                        _deny()
                    checked_equipment.add(name)


@frappe.whitelist(methods=["POST"])
def run_group_report(report_key=None, filters=None):
    """Read-only view of one of four existing Script Reports, Company bounded.

    Callers cannot widen the report key, SQL date predicate, Company filter or
    bypass the native report endpoint's original permission requirements.
    """
    _actor_is_authorized()
    filters, issuer, requester = _filters(report_key, filters)
    if in_executive_report():
        _deny()
    slug = REPORTS[report_key][0]
    module = import_module("ccpcerp.ccpc_erp.report." + slug + "." + slug)
    token = _CONTEXT.set((report_key, issuer, requester))
    try:
        columns, rows = module.execute(filters)
        _assert_returned_companies(report_key, rows, issuer, requester)
        return {"columns": columns, "rows": rows}
    finally:
        _CONTEXT.reset(token)

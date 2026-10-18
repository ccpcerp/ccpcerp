# Copyright (c) 2026, CCPC and contributors
# Draft, read-only baseline: NOT a complete equipment maintenance-cost report.

import frappe
from frappe import _
from frappe.utils import flt, getdate


def execute(filters=None):
    """Report only traceable posted material issues and recorded Job Order activity.

    No valuation is used as an expense/GL reconciliation claim. Approved physical
    returns, labor rates, outside services, downtime and operating hours are not
    implemented; do not infer those missing costs are zero.
    """
    _require_system_manager()
    f = frappe._dict(filters or {})
    _validate_filters(f)
    currency = frappe.db.get_value("Company", f.company, "default_currency")
    issues = _get_posted_issue_valuations(f)
    jobs = _get_job_activity(f)
    return get_columns(), _merge_activity(issues, jobs, currency)


def _require_system_manager():
    # Deliberately narrower than general operational-supervisor roles.
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Equipment costing baseline is restricted to System Managers."), frappe.PermissionError)
    for doctype in ("Equipment Registry", "Stock Entry", "Job Order"):
        if not frappe.has_permission(doctype, "read"):
            frappe.throw(_("Missing required report source permission."), frappe.PermissionError)


def _validate_filters(f):
    if not f.get("company") or not frappe.db.exists("Company", f.company):
        frappe.throw(_("Select an existing Company."), frappe.ValidationError)
    if f.get("equipment"):
        company = frappe.db.get_value("Equipment Registry", f.equipment, "company")
        if company != f.company:
            frappe.throw(_("Equipment must belong to the selected Company."), frappe.ValidationError)
    if f.get("from_date") and f.get("to_date"):
        if getdate(f.from_date) > getdate(f.to_date):
            frappe.throw(_("From Date cannot be after To Date."), frappe.ValidationError)


def _time_conditions(f, column):
    # Only compile constant fragments chosen by code, never user-provided SQL.
    conditions = []
    if f.get("from_date"):
        conditions.append(f"AND {column} >= %(from_date)s")
    if f.get("to_date"):
        conditions.append(f"AND {column} <= %(to_date)s")
    return "\n        ".join(conditions)


def _get_posted_issue_valuations(f):
    """Use SLE stock-value changes; never guess expense account or GL dimensions.

    Link SLE to the exact Stock Entry Detail row to avoid accidental stock-
    transaction multiplication. Keep the issue's original Slip Equipment;
    source immutability is a separate validation gate before production use.
    """
    sql = """
        SELECT
            eq.name AS equipment,
            eq.equipment_description,
            COUNT(DISTINCT sed.name) AS issue_rows,
            SUM(-sle.stock_value_difference) AS gross_issued_stock_value,
            SUM(CASE WHEN sle.stock_value_difference >= 0 THEN 1 ELSE 0 END) AS unexpected_value_rows
        FROM `tabStock Ledger Entry` sle
        INNER JOIN `tabStock Entry` se
            ON se.name = sle.voucher_no AND sle.voucher_type = 'Stock Entry'
        INNER JOIN `tabStock Entry Detail` sed
            ON sed.parent = se.name AND sed.name = sle.voucher_detail_no
        INNER JOIN `tabWithdrawal Slip` ws
            ON ws.name = se.withdrawal_slip AND ws.company = se.company
        INNER JOIN `tabEquipment Registry` eq
            ON eq.name = ws.equipment AND eq.company = se.company
        WHERE se.docstatus = 1
          AND se.purpose = 'Material Issue'
          AND sle.is_cancelled = 0
          AND sle.actual_qty < 0
          AND se.company = %(company)s
          AND sle.company = %(company)s
          AND sed.withdrawal_slip_item IS NOT NULL
          AND sed.withdrawal_slip_item != ''
          {equipment_condition}
          {date_conditions}
        GROUP BY eq.name, eq.equipment_description
    """.format(
        equipment_condition="AND eq.name = %(equipment)s" if f.get("equipment") else "",
        date_conditions=_time_conditions(f, "sle.posting_date"),
    )
    return frappe.db.sql(sql, dict(f), as_dict=True)


def _get_job_activity(f):
    """Job activity is grouped by date_assigned; that is NOT equipment downtime."""
    sql = """
        SELECT
            eq.name AS equipment,
            eq.equipment_description,
            COUNT(jo.name) AS job_orders,
            SUM(CASE WHEN jo.labor_hours > 0 THEN 1 ELSE 0 END) AS jobs_with_labor_hours,
            SUM(COALESCE(jo.labor_hours, 0)) AS recorded_labor_hours
        FROM `tabJob Order` jo
        INNER JOIN `tabEquipment Registry` eq
            ON eq.name = jo.equipment AND eq.company = jo.company
        WHERE jo.company = %(company)s
          AND jo.docstatus < 2
          AND COALESCE(jo.status, '') != 'Cancelled'
          {equipment_condition}
          {date_conditions}
        GROUP BY eq.name, eq.equipment_description
    """.format(
        equipment_condition="AND eq.name = %(equipment)s" if f.get("equipment") else "",
        date_conditions=_time_conditions(f, "DATE(jo.date_assigned)"),
    )
    return frappe.db.sql(sql, dict(f), as_dict=True)


def _merge_activity(issues, jobs, currency):
    equipment = {}

    for item in issues:
        key = item["equipment"]
        if key in equipment:
            raise ValueError("Duplicate equipment issue group: " + key)
        equipment[key] = {
            "equipment": key,
            "equipment_description": item.get("equipment_description"),
            "currency": currency,
            "issue_rows": int(item.get("issue_rows") or 0),
            "gross_issued_stock_value": flt(item.get("gross_issued_stock_value")),
            "unexpected_value_rows": int(item.get("unexpected_value_rows") or 0),
            "job_orders": 0,
            "recorded_labor_hours": None,
            "hours_completeness": _("No Job Orders in selected period"),
        }

    for item in jobs:
        key = item["equipment"]
        row = equipment.setdefault(
            key,
            {
                "equipment": key,
                "equipment_description": item.get("equipment_description"),
                "currency": currency,
                "issue_rows": 0,
                "gross_issued_stock_value": 0.0,
                "unexpected_value_rows": 0,
                "job_orders": 0,
                "recorded_labor_hours": None,
                "hours_completeness": "",
            },
        )
        row["job_orders"] = int(item.get("job_orders") or 0)
        counted = int(item.get("jobs_with_labor_hours") or 0)
        total = row["job_orders"]
        # Missing labor hours remain unknown, not an asserted zero.
        row["recorded_labor_hours"] = (
            flt(item.get("recorded_labor_hours")) if counted else None
        )
        row["hours_completeness"] = _("{0} of {1} Job Orders have positive labor hours").format(counted, total)

    for row in equipment.values():
        row["scope_note"] = _("PARTIAL: gross stock valuation, not GL reconciliation; returns, priced labor, services and downtime excluded")
        if row["unexpected_value_rows"]:
            row["scope_note"] += _("; unexpected nonnegative issue valuation REQUIRES REVIEW")
    return [equipment[key] for key in sorted(equipment)]


def get_columns():
    return [
        {"label": _("Equipment"), "fieldname": "equipment", "fieldtype": "Link", "options": "Equipment Registry", "width": 175},
        {"label": _("Description"), "fieldname": "equipment_description", "fieldtype": "Data", "width": 240},
        {"label": _("Issue Rows"), "fieldname": "issue_rows", "fieldtype": "Int", "width": 95},
        {"label": _("Gross Issued Stock Value (unadjusted)"), "fieldname": "gross_issued_stock_value", "fieldtype": "Currency", "options": "currency", "width": 225},
        {"label": _("Unexpected Valuation Rows"), "fieldname": "unexpected_value_rows", "fieldtype": "Int", "width": 170},
        {"label": _("Job Orders"), "fieldname": "job_orders", "fieldtype": "Int", "width": 100},
        {"label": _("Recorded Labor Hours (not cost)"), "fieldname": "recorded_labor_hours", "fieldtype": "Float", "width": 210},
        {"label": _("Positive Hours Coverage"), "fieldname": "hours_completeness", "fieldtype": "Data", "width": 200},
        {"label": _("Scope"), "fieldname": "scope_note", "fieldtype": "Data", "width": 440},
    ]

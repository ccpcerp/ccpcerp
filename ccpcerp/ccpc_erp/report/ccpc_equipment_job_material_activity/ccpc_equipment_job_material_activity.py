# Copyright (c) 2026, CCPC and contributors
"""Read-only original Equipment material and separate Job Order activity register.

This is NOT total maintenance/Job Order cost: no original Stock Entry Detail
contains an independently frozen Job Order link. Do NOT source an historical
Job Order from a mutable Withdrawal Slip; Job Order records are separate,
unpriced operational events. Gross outbound SLE valuation is not booked
expense GL, net returns, outside service, labor cost, or downtime.
"""
import frappe
from frappe import _
from frappe.utils import flt, getdate

MAX_DAYS = 366
MAX_MATERIAL = 800
MAX_JOBS = 200
QUANTITY_TOLERANCE = 0.000001
REQUIRED_SOURCES = (
    "Company", "Equipment Registry", "Stock Entry", "Stock Ledger Entry",
    "Withdrawal Slip", "Job Order", "Item",
)


def execute(filters=None):
    _require_access()
    f = frappe._dict(filters or {})
    _validate_filters(f)
    params = dict(f)
    params.update(material_limit=MAX_MATERIAL + 1, job_limit=MAX_JOBS + 1)
    materials = frappe.db.sql(_material_sql(bool(f.get("equipment"))), params, as_dict=True)
    jobs = frappe.db.sql(_job_sql(bool(f.get("equipment"))), params, as_dict=True)
    if len(materials) > MAX_MATERIAL or len(jobs) > MAX_JOBS:
        frappe.throw(
            _("Too many original issue or Job Order rows; narrow dates or Equipment."),
            frappe.ValidationError,
        )
    _check_row_permissions(materials, jobs)
    return get_columns(), _present(materials, jobs, filtered=bool(f.get("equipment")))


def _require_access():
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Equipment costing source register requires System Manager."), frappe.PermissionError)
    for doctype in REQUIRED_SOURCES:
        if not frappe.has_permission(doctype, "read"):
            frappe.throw(_("Cannot read an Equipment/Job costing report source."), frappe.PermissionError)


def _validate_filters(f):
    if not f.get("company") or not frappe.db.exists("Company", f.company):
        frappe.throw(_("Valid issuing Company is required."), frappe.ValidationError)
    frappe.get_doc("Company", f.company).check_permission("read")
    if f.get("equipment"):
        if not frappe.db.exists("Equipment Registry", f.equipment):
            frappe.throw(_("Unknown Equipment Registry record."), frappe.ValidationError)
        equipment = frappe.get_doc("Equipment Registry", f.equipment)
        equipment.check_permission("read")
        if equipment.company != f.company:
            frappe.throw(_("Equipment belongs to a different Company."), frappe.ValidationError)
    if not f.get("from_date") or not f.get("to_date"):
        frappe.throw(_("Both dates are required."), frappe.ValidationError)
    try:
        a, b = getdate(f.from_date), getdate(f.to_date)
    except (ValueError, TypeError):
        frappe.throw(_("Invalid dates."), frappe.ValidationError)
        return
    if a > b or (b - a).days > MAX_DAYS:
        frappe.throw(_("Date range must be ordered and at most 367 days inclusive."), frappe.ValidationError)


def _material_sql(filtered=False):
    # GROUP SLE BEFORE joining any other source; one row per original issue
    # Stock Entry Detail even when a valuation is split across many SLE lines.
    # No read of ws.job_order or ws.department: they can be modified later.
    equipment_filter = (
        "AND (sed.ccpc_equipment_at_issue = %(equipment)s "
        "OR sed.ccpc_equipment_code_at_issue = %(equipment)s)"
        if filtered else ""
    )
    return """
        SELECT
            se.posting_date, se.company AS issuing_company,
            co.default_currency AS issuing_currency,
            se.name AS stock_entry, sed.name AS stock_entry_detail,
            se.withdrawal_slip, sed.withdrawal_slip_item,
            sed.item_code, sed.item_name, sed.stock_uom,
            sed.transfer_qty AS issued_qty,
            sed.ccpc_equipment_at_issue AS source_equipment,
            sed.ccpc_equipment_code_at_issue AS source_equipment_code,
            eq.name AS valid_equipment,
            eq.equipment_description,
            ws.equipment AS current_slip_equipment,
            wsi.name AS valid_slip_item,
            wsi.item_code AS slip_item_code,
            ledger.outbound_qty, ledger.gross_sle_issue_value,
            ledger.ledger_rows
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed
          ON sed.parent = se.name AND sed.parenttype = 'Stock Entry'
        INNER JOIN `tabWithdrawal Slip` ws
          ON ws.name = se.withdrawal_slip AND ws.company = se.company
        INNER JOIN `tabCompany` co ON co.name = se.company
        LEFT JOIN `tabWithdrawal Slip Item` wsi
          ON wsi.name = sed.withdrawal_slip_item
         AND wsi.parent = ws.name AND wsi.parenttype = 'Withdrawal Slip'
        LEFT JOIN `tabEquipment Registry` eq
          ON eq.name = sed.ccpc_equipment_at_issue AND eq.company = se.company
        LEFT JOIN (
            SELECT sle.company, sle.voucher_no, sle.voucher_detail_no,
                SUM(-sle.actual_qty) AS outbound_qty,
                SUM(-sle.stock_value_difference) AS gross_sle_issue_value,
                COUNT(*) AS ledger_rows
            FROM `tabStock Ledger Entry` sle
            WHERE sle.voucher_type = 'Stock Entry'
              AND sle.company = %(company)s
              AND sle.posting_date BETWEEN %(from_date)s AND %(to_date)s
              AND sle.is_cancelled = 0
              AND sle.actual_qty < 0
            GROUP BY sle.company, sle.voucher_no, sle.voucher_detail_no
        ) ledger
          ON ledger.voucher_no = se.name
         AND ledger.voucher_detail_no = sed.name
         AND ledger.company = se.company
        WHERE se.docstatus = 1
          AND se.purpose = 'Material Issue'
          AND se.company = %(company)s
          AND se.posting_date BETWEEN %(from_date)s AND %(to_date)s
          {equipment_filter}
        ORDER BY se.posting_date, se.name, sed.idx
        LIMIT %(material_limit)s
    """.format(equipment_filter=equipment_filter)


def _job_sql(filtered=False):
    # Independent unpriced job activity, NOT a join onto material cost.
    # Equipment assigned on Job Order is current operational reference,
    # not a posted-material link or frozen issue-time attribution.
    equipment_filter = "AND jo.equipment = %(equipment)s" if filtered else ""
    return """
        SELECT
            jo.name AS job_order, DATE(jo.date_assigned) AS activity_date,
            jo.company AS issuing_company, jo.equipment AS job_equipment,
            eq.name AS valid_equipment, eq.equipment_description,
            jo.status AS job_status, jo.labor_hours AS recorded_hours
        FROM `tabJob Order` jo
        LEFT JOIN `tabEquipment Registry` eq
          ON eq.name = jo.equipment AND eq.company = jo.company
        WHERE jo.company = %(company)s
          AND jo.docstatus < 2
          AND COALESCE(jo.status, '') != 'Cancelled'
          AND DATE(jo.date_assigned) BETWEEN %(from_date)s AND %(to_date)s
          {equipment_filter}
        ORDER BY jo.date_assigned, jo.name
        LIMIT %(job_limit)s
    """.format(equipment_filter=equipment_filter)


def _check_row_permissions(materials, jobs):
    checked = set()
    def check(doctype, name):
        if not name or (doctype, name) in checked:
            return
        # Do not depend solely on SQL company filters for User Permission.
        frappe.get_doc(doctype, name).check_permission("read")
        checked.add((doctype, name))
    for row in materials:
        check("Stock Entry", row.stock_entry)
        check("Withdrawal Slip", row.withdrawal_slip)
        for equipment in (row.source_equipment, row.current_slip_equipment):
            if equipment and frappe.db.exists("Equipment Registry", equipment):
                check("Equipment Registry", equipment)
    for row in jobs:
        check("Job Order", row.job_order)
        if row.job_equipment and frappe.db.exists("Equipment Registry", row.job_equipment):
            check("Equipment Registry", row.job_equipment)


def _present(materials, jobs, filtered=False):
    rows = []
    seen = set()
    for r in materials:
        if r.stock_entry_detail in seen:
            frappe.throw(_("Duplicate original Stock Entry Detail; refuse to count twice."), frappe.ValidationError)
        seen.add(r.stock_entry_detail)
        flags = []
        pair_ok = bool(r.source_equipment and r.source_equipment_code) and (
            r.source_equipment == r.source_equipment_code
        )
        if not r.source_equipment and not r.source_equipment_code:
            flags.append("LEGACY_NO_EQUIPMENT_SNAPSHOT")
        elif not pair_ok:
            flags.append("SNAPSHOT_PAIR_CONFLICT")
        elif not r.valid_equipment:
            flags.append("EQUIPMENT_MASTER_OR_COMPANY_INVALID")
        if pair_ok and r.valid_equipment and r.current_slip_equipment != r.source_equipment:
            flags.append("CURRENT_SLIP_EQUIPMENT_CHANGED")
        if not r.withdrawal_slip_item or not r.valid_slip_item:
            flags.append("INVALID_OR_FOREIGN_SLIP_ITEM")
        elif r.slip_item_code != r.item_code:
            flags.append("SLIP_ITEM_CODE_CONFLICT")
        if not r.ledger_rows:
            flags.append("MISSING_OUTBOUND_SLE")
        elif abs(flt(r.outbound_qty) - flt(r.issued_qty)) > QUANTITY_TOLERANCE:
            flags.append("OUTBOUND_SLE_QUANTITY_CONFLICT")
        if r.ledger_rows and flt(r.gross_sle_issue_value) < -QUANTITY_TOLERANCE:
            flags.append("UNEXPECTED_NEGATIVE_ISSUE_VALUE")
        if filtered:
            flags.append("EQUIPMENT_FILTER_EXCLUDES_OTHER_UNATTRIBUTED_ROWS")

        rows.append({
            "activity_type": "ORIGINAL_MATERIAL_ISSUE",
            "activity_date": r.posting_date,
            "issuing_company": r.issuing_company,
            "issuing_currency": r.issuing_currency,
            "equipment": r.valid_equipment if pair_ok else None,
            "source_equipment": r.source_equipment,
            "source_equipment_code": r.source_equipment_code,
            "equipment_description": r.equipment_description if pair_ok else None,
            "stock_entry": r.stock_entry,
            "stock_entry_detail": r.stock_entry_detail,
            "withdrawal_slip": r.withdrawal_slip,
            "withdrawal_slip_item": r.withdrawal_slip_item,
            "job_order": None,  # No frozen material-to-job link exists.
            "item_code": r.item_code,
            "item_name": r.item_name,
            "issued_qty": flt(r.issued_qty),
            "outbound_sle_qty": flt(r.outbound_qty) if r.ledger_rows else None,
            "stock_uom": r.stock_uom,
            "gross_sle_material_value": (
                flt(r.gross_sle_issue_value) if r.ledger_rows else None
            ),
            "recorded_labor_hours": None,
            "job_status": None,
            "source_quality": ", ".join(flags) if flags else "OK",
            "job_attribution_quality": "NO_FROZEN_JOB_LINK_ON_ORIGINAL_ISSUE",
            "accounting_scope": "Original gross SLE value only; not GL, net returns or priced job cost",
        })
    for j in jobs:
        flags = []
        if not j.job_equipment or not j.valid_equipment:
            flags.append("JOB_EQUIPMENT_MISSING_OR_OTHER_COMPANY")
        if filtered:
            flags.append("EQUIPMENT_FILTER_EXCLUDES_OTHER_UNATTRIBUTED_ROWS")
        rows.append({
            "activity_type": "UNPRICED_JOB_ORDER_ACTIVITY",
            "activity_date": j.activity_date,
            "issuing_company": j.issuing_company,
            "issuing_currency": None,
            "equipment": j.valid_equipment,
            "source_equipment": None,
            "source_equipment_code": None,
            "equipment_description": j.equipment_description,
            "stock_entry": None,
            "stock_entry_detail": None,
            "withdrawal_slip": None,
            "withdrawal_slip_item": None,
            "job_order": j.job_order,
            "item_code": None,
            "item_name": None,
            "issued_qty": None,
            "outbound_sle_qty": None,
            "stock_uom": None,
            "gross_sle_material_value": None,
            "recorded_labor_hours": flt(j.recorded_hours) if flt(j.recorded_hours) > 0 else None,
            "job_status": j.job_status,
            "source_quality": ", ".join(flags) if flags else "JOB_ORDER_OPERATIONAL_REFERENCE",
            "job_attribution_quality": "CURRENT_JOB_EQUIPMENT_NOT_ISSUE_LINK",
            "accounting_scope": "Unpriced Job Order only; not attributable material, labor cost or GL",
        })
    return sorted(rows, key=lambda r: (
        str(r["activity_date"] or ""), r["activity_type"],
        r["stock_entry_detail"] or r["job_order"] or "",
    ))


def get_columns():
    def col(label, name, kind="Data", width=130, **options):
        return {"label": _(label), "fieldname": name,
                "fieldtype": kind, "width": width, **options}
    return [
        col("Activity Type", "activity_type", width=190),
        col("Date", "activity_date", "Date", 110),
        col("Company", "issuing_company", "Link", 170, options="Company"),
        col("Equipment (Evidence)", "equipment", "Link", 200, options="Equipment Registry"),
        col("Original Equipment Link", "source_equipment", "Data", 160),
        col("Original Equipment Code", "source_equipment_code", "Data", 160),
        col("Equipment Description", "equipment_description", width=210),
        col("Original Stock Entry", "stock_entry", "Link", 175, options="Stock Entry"),
        col("Stock Entry Detail", "stock_entry_detail", width=175),
        col("Withdrawal Slip", "withdrawal_slip", "Link", 170, options="Withdrawal Slip"),
        col("Withdrawal Item", "withdrawal_slip_item", width=145),
        col("Job Order (Unpriced)", "job_order", "Link", 160, options="Job Order"),
        col("Item", "item_code", "Link", 160, options="Item"),
        col("Item Name", "item_name", width=170),
        col("Issued Qty", "issued_qty", "Float", 95),
        col("Outbound SLE Qty", "outbound_sle_qty", "Float", 115),
        col("Stock UOM", "stock_uom", "Link", 95, options="UOM"),
        col("Gross Original SLE Material Value", "gross_sle_material_value",
            "Currency", 225, options="issuing_currency"),
        col("Company Currency", "issuing_currency", "Link", 125, options="Currency"),
        col("Recorded Hours (Unpriced)", "recorded_labor_hours", "Float", 190),
        col("Job Status", "job_status", width=125),
        col("Source Quality", "source_quality", width=310),
        col("Job Attribution", "job_attribution_quality", width=295),
        col("Accounting Scope", "accounting_scope", width=320),
    ]

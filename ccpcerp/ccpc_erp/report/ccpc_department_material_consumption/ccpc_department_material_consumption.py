# Copyright (c) 2026, CCPC and contributors
"""Original-issue, read-only Department material consumption source register.

Only a submitted Withdrawal-linked Material Issue is in scope. This is GROSS
SLE stock valuation, NOT expense GL, returned/net consumption, or intercompany
billings. The verified accounting beneficiary at issue is the Stock Entry
Detail Department matching its protected Stock Entry header Department.
Historical requesting Department/Section and Work-for Section are NOT
independently frozen on these original issue rows and cannot be certified
from the currently editable Withdrawal Slip or billing refresh snapshots.
"""

import frappe

from ccpcerp.ccpc_erp import executive_reporting
from frappe import _
from frappe.utils import flt, getdate

MAX_DAYS = 366
MAX_ROWS = 1000
QTY_TOLERANCE = 0.000001
READ_SOURCES = (
    "Company",
    "Department",
    "Stock Entry",
    "Withdrawal Slip",
    "Stock Ledger Entry",
    "Item",
)


def execute(filters=None):
    _require_access()
    f = frappe._dict(filters or {})
    _validate_filters(f)
    params = dict(f)
    params["row_limit"] = MAX_ROWS + 1
    records = frappe.db.sql(_source_sql(bool(f.get("department"))), params, as_dict=True)
    if len(records) > MAX_ROWS:
        frappe.throw(
            _("More than {0} original issue rows match. Narrow the Company, Department or posting dates.").format(MAX_ROWS),
            frappe.ValidationError,
        )
    return get_columns(), _present(records)


def _require_access():
    if executive_reporting.in_executive_report():
        return
    # This report can expose Stock Ledger valuation and department-specific
    # financial mapping evidence. Do not widen reporting roles implicitly.
    if "System Manager" not in frappe.get_roles():
        frappe.throw(
            _("Department consumption requires System Manager."),
            frappe.PermissionError,
        )
    for doctype in READ_SOURCES:
        if not frappe.has_permission(doctype, "read"):
            frappe.throw(
                _("Missing permission to read a required department consumption source."),
                frappe.PermissionError,
            )


def _validate_filters(f):
    if not f.get("company") or not frappe.db.exists("Company", f.company):
        frappe.throw(_("A valid Company is required."), frappe.ValidationError)
    # Record-level permission is not implied by System Manager, particularly
    # when User Permission restricts specific Companies.
    if not executive_reporting.in_executive_report():
        frappe.get_doc("Company", f.company).check_permission("read")

    if f.get("department"):
        if not frappe.db.exists("Department", f.department):
            frappe.throw(_("Unknown Department."), frappe.ValidationError)
        dep = frappe.get_doc("Department", f.department)
        dep.check_permission("read")
        if dep.company != f.company:
            frappe.throw(
                _("Department must belong to the selected issuing Company."),
                frappe.ValidationError,
            )
    if not f.get("from_date") or not f.get("to_date"):
        frappe.throw(_("Both posting dates are required."), frappe.ValidationError)
    try:
        start, end = getdate(f.from_date), getdate(f.to_date)
    except (ValueError, TypeError):
        frappe.throw(_("Invalid posting dates."), frappe.ValidationError)
    if start > end or (end - start).days > MAX_DAYS:
        frappe.throw(
            _("Choose an ordered date range of at most 367 calendar days."),
            frappe.ValidationError,
        )


def _source_sql(department_filter=False):
    # Group outbound SLE by exact original Stock Entry Detail BEFORE any
    # source joins. This avoids multiplying an issue row when ERPNext splits
    # valuation into multiple ledger fragments. Never aggregate by Item only.
    # The Department filter matches either source snapshot so conflicting
    # headers/items are shown as exceptions rather than silently omitted.
    dept_sql = (
        "AND (sed.department = %(department)s OR se.department = %(department)s)"
        if department_filter else ""
    )
    return """
        SELECT
            se.posting_date,
            se.company AS issuing_company,
            issuer.default_currency AS issuing_currency,
            se.name AS stock_entry,
            sed.name AS stock_entry_detail,
            se.withdrawal_slip,
            sed.withdrawal_slip_item,
            sed.item_code,
            sed.item_name,
            sed.stock_uom,
            sed.transfer_qty AS issued_qty,
            sed.department AS detail_department,
            se.department AS header_department,
            dept.name AS company_department,
            wsi.name AS matched_slip_item,
            wsi.item_code AS slip_item_code,
            source_sle.sle_issued_qty,
            source_sle.gross_sle_issue_value,
            source_sle.sle_row_count
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed
            ON sed.parent = se.name
           AND sed.parenttype = 'Stock Entry'
        INNER JOIN `tabWithdrawal Slip` ws
            ON ws.name = se.withdrawal_slip
           AND ws.company = se.company
        INNER JOIN `tabCompany` issuer
            ON issuer.name = se.company
        LEFT JOIN `tabWithdrawal Slip Item` wsi
            ON wsi.name = sed.withdrawal_slip_item
           AND wsi.parent = ws.name
           AND wsi.parenttype = 'Withdrawal Slip'
        LEFT JOIN `tabDepartment` dept
            ON dept.name = sed.department
           AND dept.company = se.company
        LEFT JOIN (
            SELECT
                sle.voucher_no,
                sle.voucher_detail_no,
                sle.company,
                SUM(-sle.actual_qty) AS sle_issued_qty,
                SUM(-sle.stock_value_difference) AS gross_sle_issue_value,
                COUNT(*) AS sle_row_count
            FROM `tabStock Ledger Entry` sle
            WHERE sle.voucher_type = 'Stock Entry'
              AND sle.company = %(company)s
              AND sle.posting_date BETWEEN %(from_date)s AND %(to_date)s
              AND sle.is_cancelled = 0
              AND sle.actual_qty < 0
            GROUP BY sle.voucher_no, sle.voucher_detail_no, sle.company
        ) source_sle
            ON source_sle.voucher_no = se.name
           AND source_sle.voucher_detail_no = sed.name
           AND source_sle.company = se.company
        WHERE se.docstatus = 1
          AND se.purpose = 'Material Issue'
          AND se.company = %(company)s
          AND se.posting_date BETWEEN %(from_date)s AND %(to_date)s
          {department_filter}
        ORDER BY se.posting_date, se.name, sed.idx
        LIMIT %(row_limit)s
    """.format(department_filter=dept_sql)


def _assert_department_visibility(rows):
    if executive_reporting.in_executive_report():
        return
    # Raw SQL bypasses Frappe's automatic per-row User Permission filtering.
    # Check every existing source document, not only Company and Department.
    # Missing legacy Department/Item masters remain quality evidence;
    # existing sensitive stock/slip source records must be readable.
    checked = set()
    for row in rows:
        for doctype, name in (
            ("Stock Entry", row.stock_entry),
            ("Withdrawal Slip", row.withdrawal_slip),
            ("Item", row.item_code),
            ("Department", row.detail_department),
            ("Department", row.header_department),
        ):
            if not name or (doctype, name) in checked:
                continue
            if doctype in ("Department", "Item") and not frappe.db.exists(doctype, name):
                continue
            frappe.get_doc(doctype, name).check_permission("read")
            checked.add((doctype, name))


def _present(records):
    _assert_department_visibility(records)
    rows = []
    seen = set()
    for record in records:
        detail = record.stock_entry_detail
        if detail in seen:
            frappe.throw(
                _("Duplicate original Stock Entry Detail in the source result."),
                frappe.ValidationError,
            )
        seen.add(detail)

        reason = []
        verified = (
            bool(record.detail_department)
            and record.detail_department == record.header_department
            and record.company_department == record.detail_department
        )
        if not record.detail_department or not record.header_department:
            reason.append("MISSING_ISSUE_DEPARTMENT")
        elif record.detail_department != record.header_department:
            reason.append("DEPARTMENT_ROW_HEADER_CONFLICT")
        elif not record.company_department:
            reason.append("DEPARTMENT_MASTER_MISSING_OR_OTHER_COMPANY")

        if not record.withdrawal_slip_item or not record.matched_slip_item:
            reason.append("SLIP_ITEM_LINK_INVALID")
        elif record.slip_item_code != record.item_code:
            reason.append("SLIP_ITEM_CODE_MISMATCH")

        if not record.sle_row_count:
            reason.append("NO_ORIGINAL_OUTBOUND_SLE")
        elif abs(flt(record.sle_issued_qty) - flt(record.issued_qty)) > QTY_TOLERANCE:
            reason.append("SLE_QUANTITY_MISMATCH")
        if record.sle_row_count and flt(record.gross_sle_issue_value) < -QTY_TOLERANCE:
            reason.append("UNEXPECTED_SLE_VALUE_SIGN")

        # Never produce a per-Department certified GL charge. Raw issue
        # value is visible for reconciliation even on unattributed rows.
        rows.append({
            "posting_date": record.posting_date,
            "issuing_company": record.issuing_company,
            "issuing_currency": record.issuing_currency,
            "beneficiary_department_at_issue": record.detail_department if verified else None,
            "detail_department": record.detail_department,
            "header_department": record.header_department,
            "withdrawal_slip": record.withdrawal_slip,
            "withdrawal_slip_item": record.withdrawal_slip_item,
            "stock_entry": record.stock_entry,
            "stock_entry_detail": detail,
            "item_code": record.item_code,
            "item_name": record.item_name,
            "stock_uom": record.stock_uom,
            "issued_qty": flt(record.issued_qty),
            "sle_issued_qty": flt(record.sle_issued_qty) if record.sle_row_count else None,
            "gross_sle_issue_value": (
                flt(record.gross_sle_issue_value) if record.sle_row_count else None
            ),
            "source_quality": ", ".join(reason) if reason else "OK",
            "section_provenance": "NO_ORIGINAL_SECTION_SNAPSHOT",
            "scope_note": "Gross original issue only; not GL, returns, billing or net consumption",
        })
    return rows


def get_columns():
    def col(label, fieldname, fieldtype="Data", width=140, **extra):
        return {
            "label": _(label),
            "fieldname": fieldname,
            "fieldtype": fieldtype,
            "width": width,
            **extra,
        }

    return [
        col("Posting Date", "posting_date", "Date", 115),
        col("Issuing Company", "issuing_company", "Link", 170, options="Company"),
        col("Beneficiary Department at Issue (Verified)", "beneficiary_department_at_issue",
            "Link", 240, options="Department"),
        col("Issue Row Department", "detail_department", "Link", 165, options="Department"),
        col("Issue Header Department", "header_department", "Link", 170, options="Department"),
        col("Withdrawal Slip", "withdrawal_slip", "Link", 145, options="Withdrawal Slip"),
        col("Withdrawal Slip Item", "withdrawal_slip_item", "Data", 145),
        col("Stock Entry", "stock_entry", "Link", 150, options="Stock Entry"),
        col("Stock Entry Detail", "stock_entry_detail", "Data", 155),
        col("Item", "item_code", "Link", 130, options="Item"),
        col("Item Name", "item_name", "Data", 190),
        col("Issued Qty", "issued_qty", "Float", 105),
        col("Outbound SLE Qty", "sle_issued_qty", "Float", 115),
        col("Stock UOM", "stock_uom", "Link", 105, options="UOM"),
        col("Gross Original SLE Issue Value", "gross_sle_issue_value",
            "Currency", 215, options="issuing_currency"),
        col("Issuing Currency", "issuing_currency", "Link", 120, options="Currency"),
        col("Source Quality", "source_quality", "Data", 275),
        col("Section Provenance", "section_provenance", "Data", 230),
        col("Accounting Scope", "scope_note", "Data", 290),
    ]

# Copyright (c) 2026, CCPC and contributors
"""Company-wide dual-perspective original stock versus posted P&L GL register.

Stock Ledger valuation and accounting General Ledger are different measures.
Never sum source valuation rows together with posted GL rows. Voucher-level
comparison is evidence for review, NOT a certified individual Department,
Account, Item or intercompany elimination. GL can aggregate lines per voucher.
"""
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate

MAX_DAYS = 366
MAX_STOCK_VOUCHERS = 400
MAX_GL_GROUPS = 800
VALUE_TOLERANCE = 0.01
SOURCE_DOCTYPES = ("Company", "Stock Entry", "Stock Ledger Entry", "GL Entry", "Account")


def execute(filters=None):
    _require_access()
    f = frappe._dict(filters or {})
    _validate_filters(f)
    args = dict(f)
    args["stock_limit"] = MAX_STOCK_VOUCHERS + 1
    args["gl_limit"] = MAX_GL_GROUPS + 1
    sources = frappe.db.sql(_stock_sql(), args, as_dict=True)
    pnl = frappe.db.sql(_pnl_sql(), args, as_dict=True)
    if len(sources) > MAX_STOCK_VOUCHERS or len(pnl) > MAX_GL_GROUPS:
        frappe.throw(
            _("Too many source vouchers or P&L GL groups. Narrow Company and dates."),
            frappe.ValidationError,
        )
    _check_row_visibility(sources, pnl)
    return get_columns(), _present(sources, pnl)


def _require_access():
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Company Stock/P&L GL review requires System Manager."), frappe.PermissionError)
    for name in SOURCE_DOCTYPES:
        if not frappe.has_permission(name, "read"):
            frappe.throw(_("Missing read permission for a reconciliation source."), frappe.PermissionError)


def _validate_filters(f):
    if not f.get("company") or not frappe.db.exists("Company", f.company):
        frappe.throw(_("An existing issuing Company is required."), frappe.ValidationError)
    frappe.get_doc("Company", f.company).check_permission("read")
    if not f.get("from_date") or not f.get("to_date"):
        frappe.throw(_("Both posting dates are required."), frappe.ValidationError)
    try:
        start, end = getdate(f.from_date), getdate(f.to_date)
    except (ValueError, TypeError):
        frappe.throw(_("Invalid posting dates."), frappe.ValidationError)
        return
    if start > end or (end - start).days > MAX_DAYS:
        frappe.throw(_("Dates must be ordered and no more than 367 days inclusive."), frappe.ValidationError)


def _stock_sql():
    # First aggregate outbound SLE at original voucher detail+Company grain,
    # then roll up to its Stock Entry voucher; do NOT join to GL Entry, Billing
    # Item, invoices or current Withdrawal Slip and multiply source value.
    # SLE is scoped to the report posting dates, so unusual late ledger posting
    # must appear as source-quality/reconciliation exception, not silent net.
    return """
        SELECT
            se.name AS stock_entry, se.posting_date,
            se.company AS issuing_company, co.default_currency AS issuing_currency,
            se.withdrawal_slip,
            COUNT(DISTINCT sed.name) AS issue_item_rows,
            COUNT(ledger.voucher_detail_no) AS rows_with_outbound_sle,
            SUM(ledger.outbound_qty) AS gross_outbound_qty_untyped,
            SUM(ledger.gross_sle_value) AS gross_sle_material_value,
            SUM(CASE WHEN ledger.gross_sle_value < 0 THEN 1 ELSE 0 END)
                AS negative_value_source_rows
        FROM `tabStock Entry` se
        INNER JOIN `tabCompany` co ON co.name = se.company
        INNER JOIN `tabStock Entry Detail` sed
          ON sed.parent = se.name AND sed.parenttype = 'Stock Entry'
        LEFT JOIN (
            SELECT
                sle.company, sle.voucher_no, sle.voucher_detail_no,
                SUM(-sle.actual_qty) AS outbound_qty,
                SUM(-sle.stock_value_difference) AS gross_sle_value
            FROM `tabStock Ledger Entry` sle
            WHERE sle.voucher_type = 'Stock Entry'
              AND sle.company = %(company)s
              AND sle.is_cancelled = 0
              AND sle.actual_qty < 0
              AND sle.posting_date BETWEEN %(from_date)s AND %(to_date)s
            GROUP BY sle.company, sle.voucher_no, sle.voucher_detail_no
        ) ledger
          ON ledger.company = se.company
         AND ledger.voucher_no = se.name
         AND ledger.voucher_detail_no = sed.name
        WHERE se.company = %(company)s
          AND se.docstatus = 1
          AND se.purpose = 'Material Issue'
          AND se.posting_date BETWEEN %(from_date)s AND %(to_date)s
        GROUP BY se.name, se.posting_date, se.company,
                 co.default_currency, se.withdrawal_slip
        ORDER BY se.posting_date, se.name
        LIMIT %(stock_limit)s
    """


def _pnl_sql():
    # Independent ALL posted P&L accounts (Income and Expense): not just
    # stock vouchers. Positive debit-minus-credit is not always consumption.
    # Filter by Company and date, and by Account accounting classification.
    return """
        SELECT
            gl.company AS issuing_company, co.default_currency AS issuing_currency,
            gl.posting_date, gl.voucher_type, gl.voucher_no, gl.account,
            acc.root_type AS pnl_root_type,
            SUM(gl.debit - gl.credit) AS pnl_net_debit,
            COUNT(*) AS gl_entry_rows
        FROM `tabGL Entry` gl
        INNER JOIN `tabAccount` acc
          ON acc.name = gl.account AND acc.company = gl.company
        INNER JOIN `tabCompany` co ON co.name = gl.company
        WHERE gl.company = %(company)s
          AND gl.is_cancelled = 0
          AND gl.posting_date BETWEEN %(from_date)s AND %(to_date)s
          AND acc.report_type = 'Profit and Loss'
          AND acc.root_type IN ('Expense', 'Income')
        GROUP BY gl.company, co.default_currency, gl.posting_date,
                 gl.voucher_type, gl.voucher_no, gl.account, acc.root_type
        ORDER BY gl.posting_date, gl.voucher_type, gl.voucher_no, gl.account
        LIMIT %(gl_limit)s
    """


def _check_row_visibility(sources, pnl):
    # SQL Company predicates are not substitutes for per-document read rights.
    seen = set()
    for s in sources:
        if s.stock_entry not in seen:
            frappe.get_doc("Stock Entry", s.stock_entry).check_permission("read")
            seen.add(s.stock_entry)
    accounts = set()
    for p in pnl:
        if p.account not in accounts:
            frappe.get_doc("Account", p.account).check_permission("read")
            accounts.add(p.account)


def _present(sources, pnl):
    gl_expenses_by_stock_voucher = defaultdict(float)
    stock_gl_vouchers = set()
    gl_keys = set()
    for g in pnl:
        key = (g.posting_date, g.voucher_type, g.voucher_no, g.account, g.pnl_root_type)
        if key in gl_keys:
            frappe.throw(_("Duplicate grouped GL key; refuse to double-count."), frappe.ValidationError)
        gl_keys.add(key)
        if g.voucher_type == "Stock Entry" and g.pnl_root_type == "Expense":
            gl_expenses_by_stock_voucher[g.voucher_no] += flt(g.pnl_net_debit)
            stock_gl_vouchers.add(g.voucher_no)

    results = []
    seen_source = set()
    for s in sources:
        if s.stock_entry in seen_source:
            frappe.throw(_("Duplicate Stock Entry voucher; refuse to duplicate source."), frappe.ValidationError)
        seen_source.add(s.stock_entry)
        flags = []
        if not s.withdrawal_slip:
            flags.append("NON_WITHDRAWAL_MATERIAL_ISSUE")
        if not s.rows_with_outbound_sle:
            flags.append("MISSING_OUTBOUND_SLE")
        elif int(s.rows_with_outbound_sle) != int(s.issue_item_rows):
            flags.append("ISSUE_ITEM_WITHOUT_OUTBOUND_SLE")
        if int(s.negative_value_source_rows or 0):
            flags.append("NEGATIVE_SLE_SOURCE_VALUATION")

        has_gl = s.stock_entry in stock_gl_vouchers
        source_value = flt(s.gross_sle_material_value) if s.rows_with_outbound_sle else None
        gl_value = gl_expenses_by_stock_voucher[s.stock_entry] if has_gl else None
        variance = source_value - gl_value if source_value is not None and gl_value is not None else None
        if not has_gl:
            flags.append("NO_PNL_EXPENSE_GL_FOR_STOCK_VOUCHER")
        elif variance is not None and abs(variance) > VALUE_TOLERANCE:
            flags.append("STOCK_VOUCHER_VALUE_GL_VARIANCE")
        # Do not aggregate quantity across unlike Stock UOM; source issue rows
        # can contain materials in many UOMs, and cannot be used as one number.
        results.append({
            "perspective": "STOCK_ISSUE_VOUCHER_COMPARISON",
            "posting_date": s.posting_date,
            "company": s.issuing_company,
            "company_currency": s.issuing_currency,
            "voucher_type": "Stock Entry",
            "voucher_no": s.stock_entry,
            "withdrawal_slip": s.withdrawal_slip,
            "account": None,
            "pnl_root_type": None,
            "source_item_rows": int(s.issue_item_rows),
            "matched_sle_item_rows": int(s.rows_with_outbound_sle),
            "gross_original_sle_value": source_value,
            "voucher_pnl_expense_gl_value": gl_value,
            "stock_vs_expense_variance": variance,
            "pnl_account_gl_net_debit": None,
            "source_quality": ", ".join(flags) if flags else "VOUCHER_VALUES_MATCH_WITHIN_TOLERANCE",
            "scope_note": "Voucher-grain source vs eligible P&L expense GL; not per-item, net returns, or certified Department P&L",
        })
    for g in pnl:
        results.append({
            "perspective": "ALL_PNL_GL_VOUCHER_ACCOUNT",
            "posting_date": g.posting_date,
            "company": g.issuing_company,
            "company_currency": g.issuing_currency,
            "voucher_type": g.voucher_type,
            "voucher_no": g.voucher_no,
            "withdrawal_slip": None,
            "account": g.account,
            "pnl_root_type": g.pnl_root_type,
            "source_item_rows": None,
            "matched_sle_item_rows": None,
            "gross_original_sle_value": None,
            "voucher_pnl_expense_gl_value": None,
            "stock_vs_expense_variance": None,
            "pnl_account_gl_net_debit": flt(g.pnl_net_debit),
            "source_quality": "INDEPENDENT_POSTED_PNL_ACCOUNT",
            "scope_note": "All Company P&L income/expense GL; includes non-stock entries, no automatic stock GL elimination",
        })
    return results


def get_columns():
    def col(label, fieldname, fieldtype="Data", width=135, **extra):
        return {"label": _(label), "fieldname": fieldname,
                "fieldtype": fieldtype, "width": width, **extra}
    return [
        col("Perspective (Do Not Add)", "perspective", width=225),
        col("Posting Date", "posting_date", "Date", 115),
        col("Company", "company", "Link", 155, options="Company"),
        col("Voucher Type", "voucher_type", width=125),
        col("Voucher", "voucher_no", width=170),
        col("Withdrawal Slip", "withdrawal_slip", "Link", 170, options="Withdrawal Slip"),
        col("P&L Account", "account", "Link", 170, options="Account"),
        col("Account Root", "pnl_root_type", width=120),
        col("Original Issue Item Rows", "source_item_rows", "Int", 150),
        col("Rows with Outbound SLE", "matched_sle_item_rows", "Int", 160),
        col("Gross Original SLE Value", "gross_original_sle_value",
            "Currency", 200, options="company_currency"),
        col("P&L Expense GL for Stock Voucher", "voucher_pnl_expense_gl_value",
            "Currency", 215, options="company_currency"),
        col("Stock vs Expense GL Variance", "stock_vs_expense_variance",
            "Currency", 210, options="company_currency"),
        col("Independent P&L Account Net Debit", "pnl_account_gl_net_debit",
            "Currency", 235, options="company_currency"),
        col("Currency", "company_currency", "Link", 100, options="Currency"),
        col("Quality/Variance", "source_quality", width=285),
        col("Accounting Scope", "scope_note", width=355),
    ]

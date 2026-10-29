# Copyright (c) 2026, CCPC and contributors
"""Read-only exact-source intercompany Withdrawal / billing-status register.

This is a source reconciliation/detail register, NOT an accounting trial
balance. An issuing Company's signed SLE valuation is distinct from frozen
billing amounts and draft invoice grand totals. Never sum them together.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate


MAX_DAYS = 366
READ_SOURCES = (
    "Company",
    "Stock Entry",
    "Withdrawal Slip",
    "Intercompany Material Billing Batch",
    "Sales Invoice",
    "Purchase Invoice",
)


def execute(filters=None):
    _require_access()
    f = frappe._dict(filters or {})
    _validate_filters(f)
    rows = frappe.db.sql(_source_sql(), dict(f), as_dict=True)
    return get_columns(), _present(rows)


def _require_access():
    # The source register spans operational inventory and sensitive finance
    # invoices. Broader role/report visibility requires Accounting signoff.
    if "System Manager" not in frappe.get_roles():
        frappe.throw(
            _("Intercompany source/billing register requires System Manager."),
            frappe.PermissionError,
        )
    for doctype in READ_SOURCES:
        if not frappe.has_permission(doctype, "read"):
            frappe.throw(
                _("Missing permission to read a required intercompany source."),
                frappe.PermissionError,
            )


def _validate_filters(f):
    if not f.get("issuing_company") or not f.get("requesting_company"):
        frappe.throw(_("Both Issuing and Requesting Companies are required."), frappe.ValidationError)
    for field in ("issuing_company", "requesting_company"):
        if f.get(field):
            if not frappe.db.exists("Company", f[field]):
                frappe.throw(_("Unknown Company filter."), frappe.ValidationError)
            # Company permission can be narrower than a report's role list.
            frappe.get_doc("Company", f[field]).check_permission("read")
    if f.get("requesting_company") and f.requesting_company == f.issuing_company:
        frappe.throw(
            _("Intercompany report requires two different Companies."),
            frappe.ValidationError,
        )
    if not f.get("from_date") or not f.get("to_date"):
        frappe.throw(_("Both posting dates are required."), frappe.ValidationError)
    start, end = getdate(f.from_date), getdate(f.to_date)
    if start > end or (end - start).days > MAX_DAYS:
        frappe.throw(
            _("Choose a valid posting-date range of at most 367 calendar days."),
            frappe.ValidationError,
        )


def _source_sql():
    # Do NOT join raw Stock Ledger Entries to billing items; a single stock
    # detail may have multiple SLE fragments and that would multiply billing
    # valuation. Collapse original outbound SLE by exact voucher/detail first.
    #
    # Keep cancelled billing batches OUTSIDE the active claim subquery so a
    # cancelled historical batch does not hide an eligible unbilled source.
    # If corrupt data creates multiple active claims, _present FAILS CLOSED.
    return """
        SELECT
            se.name AS stock_entry,
            sed.name AS stock_entry_detail,
            se.posting_date,
            ws.name AS withdrawal_slip,
            se.company AS issuing_company,
            ws.requesting_company,
            sed.item_code,
            sed.item_name,
            sed.transfer_qty AS issued_qty,
            sed.stock_uom,
            source_sle.sle_issued_qty,
            source_sle.gross_sle_issue_value,
            source_sle.sle_row_count,
            issuer.default_currency AS issuing_currency,
            claim.batch_name,
            claim.batch_status,
            claim.billing_month,
            claim.billed_issuer,
            claim.billed_requester,
            claim.bill_stock_entry,
            claim.bill_withdrawal_slip,
            claim.bill_item_code,
            claim.bill_quantity,
            claim.bill_uom,
            claim.frozen_basic_amount,
            claim.sales_invoice,
            claim.purchase_invoice,
            si.docstatus AS sales_docstatus,
            si.company AS sales_company,
            si.inter_company_invoice_reference AS sales_pair_reference,
            pi.docstatus AS purchase_docstatus,
            pi.company AS purchase_company,
            pi.inter_company_invoice_reference AS purchase_pair_reference
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed
            ON sed.parent = se.name
           AND sed.parenttype = 'Stock Entry'
        INNER JOIN `tabWithdrawal Slip` ws
            ON ws.name = se.withdrawal_slip
           AND ws.company = se.company
        INNER JOIN `tabCompany` issuer
            ON issuer.name = se.company
        LEFT JOIN (
            SELECT sle.voucher_no,
                   sle.voucher_detail_no,
                   sle.company,
                   SUM(-sle.actual_qty) AS sle_issued_qty,
                   SUM(-sle.stock_value_difference) AS gross_sle_issue_value,
                   COUNT(*) AS sle_row_count
            FROM `tabStock Ledger Entry` sle
            WHERE sle.voucher_type = 'Stock Entry'
              AND sle.company = %(issuing_company)s
              AND sle.posting_date BETWEEN %(from_date)s AND %(to_date)s
              AND sle.is_cancelled = 0
              AND sle.actual_qty < 0
            GROUP BY sle.voucher_no, sle.voucher_detail_no, sle.company
        ) source_sle
            ON source_sle.voucher_no = se.name
           AND source_sle.voucher_detail_no = sed.name
           AND source_sle.company = se.company
        LEFT JOIN (
            SELECT
                bi.stock_entry_detail,
                bi.parent AS batch_name,
                batch.status AS batch_status,
                batch.billing_month,
                batch.issuing_company AS billed_issuer,
                batch.requesting_company AS billed_requester,
                bi.stock_entry AS bill_stock_entry,
                bi.withdrawal_slip AS bill_withdrawal_slip,
                bi.item_code AS bill_item_code,
                bi.quantity AS bill_quantity,
                bi.stock_uom AS bill_uom,
                bi.frozen_basic_amount,
                batch.sales_invoice,
                batch.purchase_invoice
            FROM `tabIntercompany Material Billing Item` bi
            INNER JOIN `tabIntercompany Material Billing Batch` batch
                ON batch.name = bi.parent
               AND bi.parenttype = 'Intercompany Material Billing Batch'
               AND bi.parentfield = 'items'
               AND batch.status != 'Cancelled'
        ) claim
            ON claim.stock_entry_detail = sed.name
        LEFT JOIN `tabSales Invoice` si
            ON si.name = claim.sales_invoice
        LEFT JOIN `tabPurchase Invoice` pi
            ON pi.name = claim.purchase_invoice
        WHERE se.docstatus = 1
          AND se.purpose = 'Material Issue'
          AND se.company = %(issuing_company)s
          AND ws.requesting_company IS NOT NULL
          AND ws.requesting_company != ''
          AND ws.requesting_company != se.company
          AND sed.withdrawal_slip_item IS NOT NULL
          AND sed.withdrawal_slip_item != ''
          AND se.posting_date BETWEEN %(from_date)s AND %(to_date)s
          AND ws.requesting_company = %(requesting_company)s
        ORDER BY se.posting_date, se.name, sed.idx, claim.batch_name
    """


def _invoice_status(reference, docstatus):
    if not reference:
        return "Not Created"
    if docstatus is None:
        return "Missing Reference"
    return {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(
        int(docstatus), "Invalid Status"
    )


def _present(rows):
    seen = set()
    result = []
    for source in rows:
        detail = source.stock_entry_detail
        if detail in seen:
            # No repeated source valuations or arbitrary first claim under
            # corrupted/conflicting active billing membership.
            frappe.throw(
                _("Multiple active billing claims for Stock Entry Detail {0}.").format(
                    frappe.bold(detail)
                ),
                frappe.ValidationError,
            )
        seen.add(detail)

        reasons = []
        if not source.sle_row_count:
            reasons.append("NO_ORIGINAL_OUTBOUND_SLE")
        elif abs(flt(source.sle_issued_qty) - flt(source.issued_qty)) > 0.000001:
            reasons.append("SLE_QUANTITY_MISMATCH")

        if source.batch_name:
            if (
                source.bill_stock_entry != source.stock_entry
                or source.bill_withdrawal_slip != source.withdrawal_slip
            ):
                reasons.append("BILLING_ORIGINAL_VOUCHER_MISMATCH")
            if (
                source.billed_issuer != source.issuing_company
                or source.billed_requester != source.requesting_company
            ):
                reasons.append("BILLING_COMPANY_MISMATCH")
            if (
                source.bill_item_code != source.item_code
                or source.bill_uom != source.stock_uom
                or abs(flt(source.bill_quantity) - flt(source.issued_qty)) > 0.000001
            ):
                reasons.append("BILLING_SOURCE_MISMATCH")

        sales_status = _invoice_status(
            source.sales_invoice, source.sales_docstatus
        )
        purchase_status = _invoice_status(
            source.purchase_invoice, source.purchase_docstatus
        )
        if source.sales_invoice and source.sales_company != source.issuing_company:
            reasons.append("SALES_COMPANY_OR_LINK_INVALID")
        if source.purchase_invoice and source.purchase_company != source.requesting_company:
            reasons.append("PURCHASE_COMPANY_OR_LINK_INVALID")
        if source.sales_invoice and source.purchase_invoice:
            if (
                source.sales_pair_reference != source.purchase_invoice
                or source.purchase_pair_reference != source.sales_invoice
            ):
                reasons.append("INVOICE_RECIPROCAL_LINK_MISMATCH")
        if source.batch_status in ("Approved", "Invoiced") and (
            sales_status in ("Not Created", "Missing Reference", "Cancelled")
            or purchase_status in ("Not Created", "Missing Reference", "Cancelled")
        ):
            reasons.append("APPROVED_BATCH_INVOICE_EXCEPTION")

        result.append({
            "posting_date": source.posting_date,
            "issuing_company": source.issuing_company,
            "requesting_company": source.requesting_company,
            "withdrawal_slip": source.withdrawal_slip,
            "stock_entry": source.stock_entry,
            "stock_entry_detail": detail,
            "item_code": source.item_code,
            "item_name": source.item_name,
            "issued_qty": flt(source.issued_qty),
            "sle_issued_qty": flt(source.sle_issued_qty) if source.sle_row_count else None,
            "stock_uom": source.stock_uom,
            "gross_sle_issue_value": (
                flt(source.gross_sle_issue_value) if source.sle_row_count else None
            ),
            "issuing_currency": source.issuing_currency,
            "billing_batch": source.batch_name,
            "billing_status": source.batch_status or "Unbilled",
            "billing_month": source.billing_month,
            "frozen_billing_amount": (
                flt(source.frozen_basic_amount) if source.batch_name else None
            ),
            "sales_invoice": source.sales_invoice,
            "sales_status": sales_status,
            "purchase_invoice": source.purchase_invoice,
            "purchase_status": purchase_status,
            "source_quality": ", ".join(reasons) if reasons else "OK",
        })
    return result


def get_columns():
    def col(label, name, fieldtype="Data", width=145, **extra):
        return {"label": _(label), "fieldname": name,
                "fieldtype": fieldtype, "width": width, **extra}

    return [
        col("Posting Date", "posting_date", "Date", 115),
        col("Issuing Company", "issuing_company", "Link", 185, options="Company"),
        col("Requesting Company", "requesting_company", "Link", 185, options="Company"),
        col("Withdrawal Slip", "withdrawal_slip", "Link", 150, options="Withdrawal Slip"),
        col("Stock Entry", "stock_entry", "Link", 155, options="Stock Entry"),
        col("Stock Entry Detail", "stock_entry_detail", "Data", 140),
        col("Item", "item_code", "Link", 125, options="Item"),
        col("Item Name", "item_name", "Data", 200),
        col("Issued Qty", "issued_qty", "Float", 95),
        col("Outbound SLE Qty", "sle_issued_qty", "Float", 110),
        col("Stock UOM", "stock_uom", "Link", 90, options="UOM"),
        col("Gross SLE Issue Value", "gross_sle_issue_value", "Currency", 155, options="issuing_currency"),
        col("Issuing Currency", "issuing_currency", "Link", 105, options="Currency"),
        col("Billing Batch", "billing_batch", "Link", 165,
            options="Intercompany Material Billing Batch"),
        col("Batch Status", "billing_status", "Data", 110),
        col("Billing Month", "billing_month", "Date", 115),
        col("Frozen Billing Amount", "frozen_billing_amount", "Currency", 155, options="issuing_currency"),
        col("Sales Invoice", "sales_invoice", "Link", 155, options="Sales Invoice"),
        col("SI Status", "sales_status", "Data", 100),
        col("Purchase Invoice", "purchase_invoice", "Link", 155,
            options="Purchase Invoice"),
        col("PI Status", "purchase_status", "Data", 100),
        col("Source Quality", "source_quality", "Data", 240),
    ]

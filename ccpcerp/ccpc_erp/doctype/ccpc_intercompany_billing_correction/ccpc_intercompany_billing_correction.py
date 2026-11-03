# Copyright (c) 2026, CCPC and contributors
"""Private, append-only evidence of a human correction review.

No general DocPerm, no automatic invoice creation, cancellation or source
release. Only the guarded Billing Batch methods may create or decide a case.
"""
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


_AUTHORIZED_EDIT = object()
_INITIAL_FIELDS = (
    "billing_batch", "issuing_company", "requesting_company",
    "sales_invoice", "purchase_invoice", "reason",
    "requested_by", "requested_on",
)


class CCPCIntercompanyBillingCorrection(Document):
    def validate(self):
        if self.flags.get("_ccpc_correction_action") is not _AUTHORIZED_EDIT:
            frappe.throw(
                _("Correction records may only change through controlled Finance review."),
                exc=frappe.PermissionError,
            )
        if self.is_new():
            if self.status != "Requested" or self.reviewed_by or self.reviewed_on:
                frappe.throw(_("A new correction record must start Requested."))
            if self.review_note:
                frappe.throw(_("A new correction record cannot have a decision note."))
            return

        before = self.get_doc_before_save()
        if not before:
            frappe.throw(_("Original correction state cannot be verified."))
        for field in _INITIAL_FIELDS:
            if str(self.get(field) or "") != str(before.get(field) or ""):
                frappe.throw(_("Original correction evidence cannot be rewritten."))
        if before.status != "Requested" or self.status not in (
            "Approved for Manual Correction", "Declined",
        ):
            frappe.throw(_("Only one final Finance decision is permitted."))
        if not (self.reviewed_by and self.reviewed_on and self.review_note):
            frappe.throw(_("Finance reviewer, time and reason are required."))

    def on_trash(self):
        frappe.throw(
            _("Correction review evidence cannot be deleted."),
            exc=frappe.PermissionError,
        )

    def before_discard(self):
        frappe.throw(
            _("Correction review evidence cannot be discarded."),
            exc=frappe.PermissionError,
        )


def create_correction_case(batch, reason, actor):
    doc = frappe.get_doc({
        "doctype": "CCPC Intercompany Billing Correction",
        "billing_batch": batch.name,
        "issuing_company": batch.issuing_company,
        "requesting_company": batch.requesting_company,
        "sales_invoice": batch.sales_invoice,
        "purchase_invoice": batch.purchase_invoice,
        "status": "Requested",
        "reason": reason,
        "requested_by": actor,
        "requested_on": now_datetime(),
    })
    doc.flags._ccpc_correction_action = _AUTHORIZED_EDIT
    try:
        doc.insert(ignore_permissions=True)
    finally:
        doc.flags.pop("_ccpc_correction_action", None)
    return doc


def decide_correction_case(doc, action, note, actor):
    if action not in ("Approve", "Decline"):
        frappe.throw(_("Finance review action must be Approve or Decline."))
    doc.status = (
        "Approved for Manual Correction" if action == "Approve" else "Declined"
    )
    doc.review_note = note
    doc.reviewed_by = actor
    doc.reviewed_on = now_datetime()
    doc.flags._ccpc_correction_action = _AUTHORIZED_EDIT
    try:
        doc.save(ignore_permissions=True)
    finally:
        doc.flags.pop("_ccpc_correction_action", None)
    return doc

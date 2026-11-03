# Copyright (c) 2026, CCPC and contributors
"""Inert B revision: approval and posting activation deliberately unavailable."""

import frappe
from frappe import _
from frappe.model.document import Document

from ccpcerp.ccpc_erp.mapping_scope_keys import mapping_key


class CCPCExpenseMappingRevision(Document):
    def before_validate(self):
        if not self.mapping_scope or not self.revision_no or int(self.revision_no) <= 0:
            frappe.throw(_("A mapping scope and positive revision number are required."))
        expected = mapping_key("expense-revision", self.mapping_scope, self.revision_no)
        if self.revision_key and self.revision_key != expected:
            frappe.throw(_("Expense revision key may not be forged or edited."))
        if not self.is_new():
            old = self.get_doc_before_save()
            if old and (old.mapping_scope != self.mapping_scope or
                        int(old.revision_no) != int(self.revision_no)):
                frappe.throw(_("Mapping revision identity cannot be changed."))
        self.revision_key = expected

    def validate(self):
        # Deliberately fail closed: the approval-state workflow, approver roles,
        # master eligibility, effective-time policy and GL tests are pending.
        if self.approval_state != "Draft":
            frappe.throw(_("B mapping approval is not enabled; Finance controls remain pending."))
        if self.approved_by or self.approved_at or self.supersedes or self.revocation_reason:
            frappe.throw(_("Approval/revocation provenance cannot be supplied by draft users."))
        if any(self.get(key) for key in (
            "rejected_by", "rejected_at", "rejection_reason", "rejection_event"
        )):
            frappe.throw(_("Draft users may not supply rejection history."))
        scope = frappe.db.get_value(
            "CCPC Expense Mapping Scope", self.mapping_scope, "company"
        )
        if not scope:
            frappe.throw(_("Expense mapping scope does not exist."))
        account = frappe.db.get_value(
            "Account", self.expense_account,
            ["company", "is_group", "disabled", "root_type"], as_dict=True,
        ) if self.expense_account else None
        if not account:
            frappe.throw(_("A real Account reference is required even for a draft."))
        if account.company != scope or account.is_group or account.disabled or account.root_type != "Expense":
            frappe.throw(_("Draft expense Account must be an enabled leaf Expense account in the mapping Company."))
        if self.effective_from and self.effective_until:
            from frappe.utils import get_datetime
            if get_datetime(self.effective_until) <= get_datetime(self.effective_from):
                frappe.throw(_("The proposed effective end must follow the proposed start."))

    def before_rename(self, old, new, merge=False):
        frappe.throw(_("Mapping revisions cannot be renamed or merged."))

    def on_trash(self):
        frappe.throw(_("Mapping revisions cannot be deleted during the review-only stage."))

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
        if not frappe.db.exists("CCPC Expense Mapping Scope", self.mapping_scope):
            frappe.throw(_("Expense mapping scope does not exist."))
        if not self.expense_account or not frappe.db.exists("Account", self.expense_account):
            frappe.throw(_("A real Account reference is required even for a draft."))
        if self.effective_from and self.effective_until:
            from frappe.utils import get_datetime
            if get_datetime(self.effective_until) <= get_datetime(self.effective_from):
                frappe.throw(_("The proposed effective end must follow the proposed start."))

    def before_rename(self, old, new, merge=False):
        frappe.throw(_("Mapping revisions cannot be renamed or merged."))

    def on_trash(self):
        frappe.throw(_("Mapping revisions cannot be deleted during the review-only stage."))

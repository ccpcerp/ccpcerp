# Copyright (c) 2026, CCPC and contributors
"""Inert C revision: no active Cost Center mapping or selection permitted."""

import frappe
from frappe import _
from frappe.model.document import Document

from ccpcerp.ccpc_erp.mapping_scope_keys import mapping_key


class CCPCCenterMappingRevision(Document):
    def before_validate(self):
        if not self.mapping_scope or not self.revision_no or int(self.revision_no) <= 0:
            frappe.throw(_("A mapping scope and positive revision number are required."))
        expected = mapping_key("center-revision", self.mapping_scope, self.revision_no)
        if self.revision_key and self.revision_key != expected:
            frappe.throw(_("Center revision key may not be forged or edited."))
        if not self.is_new():
            old = self.get_doc_before_save()
            if old and (old.mapping_scope != self.mapping_scope or
                        int(old.revision_no) != int(self.revision_no)):
                frappe.throw(_("Mapping revision identity cannot be changed."))
        self.revision_key = expected

    def validate(self):
        if self.approval_state != "Draft":
            frappe.throw(_("C mapping approval is not enabled; selector and Finance controls remain pending."))
        if self.approved_by or self.approved_at or self.supersedes or self.revocation_reason:
            frappe.throw(_("Approval/revocation provenance cannot be supplied by draft users."))
        scope = frappe.db.get_value(
            "CCPC Center Mapping Scope", self.mapping_scope, "company"
        )
        if not scope:
            frappe.throw(_("Center mapping scope does not exist."))
        center = frappe.db.get_value(
            "Cost Center", self.cost_center,
            ["company", "is_group", "disabled"], as_dict=True,
        ) if self.cost_center else None
        if not center:
            frappe.throw(_("A real Cost Center reference is required even for a draft."))
        if center.company != scope or center.is_group or center.disabled:
            frappe.throw(_("Draft Cost Center must be an enabled leaf Cost Center in the mapping Company."))
        if self.effective_from and self.effective_until:
            from frappe.utils import get_datetime
            if get_datetime(self.effective_until) <= get_datetime(self.effective_from):
                frappe.throw(_("The proposed effective end must follow the proposed start."))

    def before_rename(self, old, new, merge=False):
        frappe.throw(_("Mapping revisions cannot be renamed or merged."))

    def on_trash(self):
        frappe.throw(_("Mapping revisions cannot be deleted during the review-only stage."))

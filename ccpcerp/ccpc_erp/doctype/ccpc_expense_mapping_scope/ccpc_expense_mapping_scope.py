# Copyright (c) 2026, CCPC and contributors
"""Review-only expense allocation key. No approved accounting mappings exist."""

import frappe
from frappe import _
from frappe.model.document import Document

from ccpcerp.ccpc_erp.mapping_scope_keys import mapping_key


class CCPCExpenseMappingScope(Document):
    def before_validate(self):
        if not self.company or not self.item_group:
            frappe.throw(_("Company and exact Item Group are required for expense mapping scope."))
        expected = mapping_key("expense", self.company, self.item_group)
        if self.scope_key and self.scope_key != expected:
            frappe.throw(_("Expense scope key may not be forged or edited."))
        if not self.is_new():
            old = self.get_doc_before_save()
            if old and (old.company != self.company or old.item_group != self.item_group):
                frappe.throw(_("Expense mapping identity cannot be changed; create a new scope."))
        self.scope_key = expected

    def validate(self):
        group = frappe.db.get_value("Item Group", self.item_group, "is_group")
        if group is None:
            frappe.throw(_("Expense mapping Item Group must exist."))
        if int(group):
            frappe.throw(_("Expense mapping requires an exact leaf Item Group; no parent inheritance."))

    def before_rename(self, old, new, merge=False):
        frappe.throw(_("Mapping scope records cannot be renamed or merged."))

    def on_trash(self):
        frappe.throw(_("Mapping scopes cannot be deleted during the review-only stage."))

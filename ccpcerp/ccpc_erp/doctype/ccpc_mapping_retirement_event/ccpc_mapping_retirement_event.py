# Copyright (c) 2026, CCPC and contributors
"""Server-written Finance decision/event; no normal edits or deletes."""
import frappe
from frappe.model.document import Document

class CCPCMappingRetirementEvent(Document):
    def validate(self):
        if not self.is_new():
            frappe.throw("Finance decision/event history is immutable.")
        if not getattr(frappe.flags, "ccpc_finance_event_writer", False):
            frappe.throw("Finance events must originate in the controlled workflow.")

    def before_rename(self, old, new, merge=False):
        frappe.throw("Finance audit records cannot be renamed.")

    def on_trash(self):
        frappe.throw("Finance audit records cannot be deleted.")

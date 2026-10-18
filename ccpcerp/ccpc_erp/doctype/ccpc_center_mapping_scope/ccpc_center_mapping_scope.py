# Copyright (c) 2026, CCPC and contributors
"""Review-only beneficiary allocation key; explicit selector workflow remains unapproved."""

import frappe
from frappe import _
from frappe.model.document import Document

from ccpcerp.ccpc_erp.equipment_costing import validate_department_company
from ccpcerp.ccpc_erp.mapping_scope_keys import mapping_key


class CCPCCenterMappingScope(Document):
    def before_validate(self):
        if not self.company or not self.beneficiary_department:
            frappe.throw(_("Company and actual beneficiary Department are required."))
        # The owner requires an explicit choice for multi-center Departments;
        # no selection source/UI has yet been approved, so only the basic
        # Department scope can be represented. It CANNOT become active.
        if self.selector_kind != "Department Only" or self.selector_key != "DEPARTMENT_ONLY":
            frappe.throw(_("Explicit Cost Center selection is pending separate Finance design approval."))
        expected = mapping_key(
            "center", self.company, self.beneficiary_department,
            self.selector_kind, self.selector_key,
        )
        if self.scope_key and self.scope_key != expected:
            frappe.throw(_("Center scope key may not be forged or edited."))
        if not self.is_new():
            old = self.get_doc_before_save()
            if old and any(old.get(field) != self.get(field) for field in (
                "company", "beneficiary_department", "selector_kind", "selector_key"
            )):
                frappe.throw(_("Center mapping identity cannot be changed; create a new scope."))
        self.scope_key = expected

    def validate(self):
        validate_department_company(
            self.beneficiary_department, self.company,
            _("Cost Center mapping beneficiary"), require_eligible=True,
        )

    def before_rename(self, old, new, merge=False):
        frappe.throw(_("Mapping scope records cannot be renamed or merged."))

    def on_trash(self):
        frappe.throw(_("Mapping scopes cannot be deleted during the review-only stage."))

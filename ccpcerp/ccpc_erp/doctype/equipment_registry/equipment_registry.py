# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from ccpcerp.ccpc_erp.equipment_costing import validate_department_company


class EquipmentRegistry(Document):
	def validate(self):
		validate_department_company(
			self.department,
			self.company,
			_("Equipment Registry"),
			require_eligible=self._requires_eligible_department(),
		)
		validate_equipment_section(self.section, self.department, self.company)
		if not self.is_new() and self.equipment_number != self.name:
			frappe.throw(_("Use Rename to correct an Equipment Number."))
		before = self.get_doc_before_save()
		if before and before.company != self.company and has_protected_equipment_history(self.name):
			frappe.throw(_("Company cannot be changed after Equipment assignment or stock/accounting history."))

	def _requires_eligible_department(self):
		if self.is_new():
			return True
		before = self.get_doc_before_save()
		return bool(
			not before
			or before.company != self.company
			or before.department != self.department
			or (before.status != "Active" and self.status == "Active")
		)

	def before_rename(self, old, new, merge=False):
		if merge:
			frappe.throw(_("Equipment Registry records cannot be merged."))
		if has_protected_equipment_history(old):
			frappe.throw(
				_("Equipment Number {0} cannot be renamed after Equipment assignment or stock/accounting history.").format(
					frappe.bold(old)
				)
			)


def validate_equipment_section(section, department, company):
	if not section:
		return
	section_values = frappe.db.get_value(
		"CCPC Section", section, ["department", "company", "disabled"], as_dict=True
	)
	if not section_values or section_values.disabled:
		frappe.throw(_("Section must be an active CCPC Section."))
	if section_values.department != department or section_values.company != company:
		frappe.throw(_("Section must belong to the selected Department and Company."))


def has_protected_equipment_history(equipment):
	return bool(
		frappe.db.exists("Equipment Asset Assignment", {"equipment": equipment})
		or has_submitted_equipment_use(equipment)
	)


def has_submitted_equipment_use(equipment):
	"""Return whether an Equipment tag has immutable stock/accounting history."""
	usage_doctypes = (
		"Stock Entry Detail",
		"Purchase Invoice Item",
		"Purchase Receipt Item",
		"Journal Entry Account",
	)
	for doctype in usage_doctypes:
		if frappe.get_meta(doctype).has_field("equipment") and frappe.db.exists(
			doctype, {"equipment": equipment, "docstatus": ["in", [1, 2]]}
		):
			return True

	if frappe.get_meta("GL Entry").has_field("equipment") and frappe.db.exists(
		"GL Entry", {"equipment": equipment}
	):
		return True

	return False

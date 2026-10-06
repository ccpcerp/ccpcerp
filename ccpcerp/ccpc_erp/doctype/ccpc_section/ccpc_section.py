# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CCPCSection(Document):
	def validate(self):
		values = frappe.get_cached_value(
			"Department", self.department, ["company", "disabled", "is_group"], as_dict=True
		)
		if not values:
			frappe.throw(_("Department {0} does not exist.").format(frappe.bold(self.department)))
		if not values.company:
			frappe.throw(_("Department {0} must belong to a Company.").format(frappe.bold(self.department)))
		if values.disabled or values.is_group:
			frappe.throw(_("Section must belong to an enabled leaf Department."))
		self.company = values.company

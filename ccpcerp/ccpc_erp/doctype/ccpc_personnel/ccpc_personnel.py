# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CCPCPersonnel(Document):
	def validate(self):
		self._set_company_from_department()
		self._validate_section()
		if not self.user:
			return

		existing = frappe.db.get_value(
			"CCPC Personnel",
			{"user": self.user, "name": ["!=", self.name or ""]},
			"name",
		)
		if existing:
			frappe.throw(
				_("User {0} is already linked to CCPC Personnel {1}.").format(
					frappe.bold(self.user), frappe.bold(existing)
				)
			)


	def _set_company_from_department(self):
		"""Treat Department as the personnel's home department and derive its company when available."""
		self.company = (
			frappe.get_cached_value("Department", self.department, "company")
			if self.department
			else None
		)


	def _validate_section(self):
		if not self.section:
			return
		values = frappe.get_cached_value(
			"CCPC Section", self.section, ["department", "company", "disabled"], as_dict=True
		)
		if not values or values.disabled:
			frappe.throw(_("Section must be an active CCPC Section."))
		if values.department != self.department or values.company != self.company:
			frappe.throw(_("Section must belong to the Personnel Department and Company."))


def validate_department_manager_company(doc, method=None):
	"""A Department Manager may manage many departments, but only within their company."""
	personnel = doc.get("ccpc_department_manager")
	if not personnel or not doc.company:
		return

	values = frappe.db.get_value(
		"CCPC Personnel",
		personnel,
		["personnel_name", "company", "active"],
		as_dict=True,
	)
	if not values:
		return
	if not values.active:
		frappe.throw(_("Department Manager {0} is inactive.").format(frappe.bold(values.personnel_name)))
	if values.company and values.company != doc.company:
		frappe.throw(
			_("Department Manager {0} belongs to {1}, not {2}.").format(
				frappe.bold(values.personnel_name),
				frappe.bold(values.company),
				frappe.bold(doc.company),
			)
		)

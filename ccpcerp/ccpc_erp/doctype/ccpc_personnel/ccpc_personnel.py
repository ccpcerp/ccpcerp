# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CCPCPersonnel(Document):
	def validate(self):
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

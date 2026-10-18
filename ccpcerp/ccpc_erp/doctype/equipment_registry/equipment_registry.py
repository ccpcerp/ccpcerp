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
	# Historical Withdrawal Slip issues might not have a site-specific
	# Stock Entry(.Detail).equipment field. Always check the actual submitted
	# source document link before allowing an Equipment Registry rename.
	if frappe.db.has_column("Stock Entry", "withdrawal_slip") and frappe.db.sql(
		"""
		SELECT 1
		FROM `tabStock Entry` se
		INNER JOIN `tabWithdrawal Slip` ws ON ws.name = se.withdrawal_slip
		WHERE se.docstatus IN (1, 2)
		  AND se.purpose = 'Material Issue'
		  AND ws.equipment = %(equipment)s
		LIMIT 1
		""",
		{"equipment": equipment},
	):
		return True

	# The independent original-number Data snapshot survives Link-renaming
	# behavior. Check both fields if present: old submitted rows can have
	# either or neither, and must never be silently treated as reassignable.
	source_fields = []
	for fieldname in ("ccpc_equipment_at_issue", "ccpc_equipment_code_at_issue"):
		if frappe.db.has_column("Stock Entry Detail", fieldname):
			source_fields.append("sed.`" + fieldname + "` = %(equipment)s")
	if source_fields and frappe.db.sql(
		"""
		SELECT 1
		FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name = sed.parent
		WHERE se.docstatus IN (1, 2)
		  AND se.purpose = 'Material Issue'
		  AND (""" + " OR ".join(source_fields) + """)
		LIMIT 1
		""",
		{"equipment": equipment},
	):
		return True

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

# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc

from ccpcerp.ccpc_erp.equipment_costing import (
	set_department_from_equipment,
	validate_department_company,
	validate_equipment,
)
from ccpcerp.ccpc_erp.work_request_job_order import validate_new_job_order_relationship


MATERIAL_REQUEST_ALLOWED_STATUSES = frozenset(
	{"Open", "Converted to Job Order", "In Progress"}
)


def validate_material_request_eligibility(work_request):
	if work_request.status not in MATERIAL_REQUEST_ALLOWED_STATUSES:
		frappe.throw(
			_("Material Request cannot be created from Work Request {0} with status {1}.").format(
				frappe.bold(work_request.name), frappe.bold(work_request.status or _("blank"))
			)
		)
	if not work_request.company or not frappe.get_cached_value(
		"Company", work_request.company, "name"
	):
		frappe.throw(
			_("Work Request {0} must have a valid Company before creating a Material Request.").format(
				frappe.bold(work_request.name)
			)
		)
	if work_request.request_type == "Equipment" and not work_request.equipment:
		frappe.throw(_("Equipment is required for an Equipment Work Request."))
	validate_equipment(
		work_request.equipment,
		work_request.company,
		require_active=True,
		context=_("Work Request"),
	)
	validate_department_company(
		work_request.department,
		work_request.company,
		_("Work Request"),
		require_eligible=True,
	)
	work_request.validate_derived_job_order()


def validate_job_order_creation_eligibility(work_request):
	validate_new_job_order_relationship(work_request)
	if not work_request.company or not frappe.get_cached_value(
		"Company", work_request.company, "name"
	):
		frappe.throw(
			_("Work Request {0} must have a valid Company before creating a Job Order.").format(
				frappe.bold(work_request.name)
			)
		)
	if work_request.request_type == "Equipment" and not work_request.equipment:
		frappe.throw(_("Equipment is required for an Equipment Work Request."))
	validate_equipment(
		work_request.equipment,
		work_request.company,
		require_active=True,
		context=_("Work Request"),
	)
	validate_department_company(
		work_request.department,
		work_request.company,
		_("Work Request"),
		require_eligible=True,
	)


class WorkRequest(Document):
	def before_validate(self):
		if self.is_new() or self._equipment_changed():
			set_department_from_equipment(self)

	def validate(self):
		self.validate_priority()
		if self.request_type == "Equipment" and not self.equipment:
			frappe.throw(_("Equipment is required for an Equipment Work Request."))
		validate_equipment(self.equipment, self.company, require_active=self._requires_active_equipment())
		validate_department_company(
			self.department,
			self.company,
			_("Work Request"),
			require_eligible=self._requires_eligible_department(),
		)
		self.validate_derived_job_order()

	def validate_priority(self):
		if self.is_new():
			if not self.priority:
				frappe.throw(_("Priority is required for a new Work Request."))
			return
		before = self.get_doc_before_save()
		if before and before.priority and not self.priority:
			frappe.throw(_("Priority cannot be cleared after it has been saved on a Work Request."))

	def _requires_active_equipment(self):
		if not self.equipment:
			return False
		if self.is_new():
			return True
		return self._equipment_changed()

	def _equipment_changed(self):
		before = self.get_doc_before_save()
		return not before or before.equipment != self.equipment

	def _requires_eligible_department(self):
		if self.is_new():
			return True
		before = self.get_doc_before_save()
		return not before or before.company != self.company or before.department != self.department

	def validate_derived_job_order(self):
		if getattr(self.flags, "syncing_job_order", False) or not self.job_order:
			return
		if frappe.db.get_value("Job Order", self.job_order, "work_request") != self.name:
			frappe.throw(_("Job Order is derived from Job Order.work_request and cannot be set independently."))


@frappe.whitelist()
def make_material_request(source_name, target_doc=None):
	source = frappe.get_doc("Work Request", source_name)
	source.check_permission("read")
	validate_material_request_eligibility(source)

	def set_missing_values(source, target):
		target.material_request_type = "Material Issue"
		# No Material Request Item is created here; header values are defaults only.

	return get_mapped_doc(
		"Work Request",
		source_name,
		{
			"Work Request": {
				"doctype": "Material Request",
				"field_no_map": ["naming_series", "status"],
				"field_map": {
					"name": "work_request",
					"company": "company",
					"equipment": "equipment",
					"department": "department",
				},
			}
		},
		target_doc,
		set_missing_values,
	)


@frappe.whitelist()
def make_job_order(source_name, target_doc=None):
	source = frappe.get_doc("Work Request", source_name)
	source.check_permission("read")
	validate_job_order_creation_eligibility(source)

	return get_mapped_doc(
		"Work Request",
		source_name,
		{
			"Work Request": {
				"doctype": "Job Order",
				"field_no_map": ["status", "naming_series", "job_order"],
				"field_map": {
					"name": "work_request",
					"company": "company",
					"equipment": "equipment",
					"equipment_description": "equipment_description",
					"asset_location": "asset_location",
					"department": "department",
					"maintenance_section": "maintenance_section",
					"problem_description": "problem_description",
					"priority": "priority",
					"requested_by": "requested_by",
				},
			}
		},
		target_doc,
	)

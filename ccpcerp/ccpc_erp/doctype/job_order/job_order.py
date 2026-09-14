# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from ccpcerp.ccpc_erp.equipment_costing import (
	set_department_from_equipment,
	validate_department_company,
	validate_equipment,
)
from ccpcerp.ccpc_erp.work_request_job_order import clear_work_request, sync_work_request


class JobOrder(Document):
	def before_validate(self):
		self.copy_work_request_context()
		if (self.is_new() or self._equipment_changed()) and not self.work_request:
			set_department_from_equipment(self)

	def validate(self):
		self.validate_work_request_immutable()
		self.validate_duplicate_work_request()
		self.validate_work_request_context()
		validate_equipment(self.equipment, self.company, require_active=self._requires_active_equipment())
		validate_department_company(
			self.department,
			self.company,
			_("Job Order"),
			require_eligible=self._requires_eligible_department(),
		)

	def after_insert(self):
		sync_work_request(self)

	def on_update(self):
		sync_work_request(self)

	def on_trash(self):
		if self.status not in (None, "", "Open"):
			frappe.throw(_("Only a blank or Open Job Order may be deleted."))
		clear_work_request(self)

	def copy_work_request_context(self):
		if not self.is_new() or not self.work_request:
			return
		work_request = frappe.get_doc("Work Request", self.work_request)
		for fieldname in (
			"company",
			"equipment",
			"equipment_description",
			"department",
			"problem_description",
			"priority",
			"maintenance_section",
			"requested_by",
		):
			if not self.get(fieldname):
				self.set(fieldname, work_request.get(fieldname))

	def validate_work_request_immutable(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		old_work_request = (
			before.work_request
			if before
			else frappe.db.get_value("Job Order", self.name, "work_request")
		)
		if old_work_request and self.work_request != old_work_request:
			frappe.throw(_("Work Request cannot be changed or unlinked after the Job Order is saved."))

	def validate_duplicate_work_request(self):
		if not self.work_request:
			return
		existing = frappe.db.exists(
			"Job Order",
			{"work_request": self.work_request, "name": ["!=", self.name]},
		)
		if existing:
			frappe.throw(
				_("A Job Order ({0}) already exists for Work Request {1}.").format(
					frappe.bold(existing), frappe.bold(self.work_request)
				)
			)

	def validate_work_request_context(self):
		if not self.work_request:
			return
		work_request = frappe.get_doc("Work Request", self.work_request)
		if self.company != work_request.company:
			frappe.throw(_("Job Order Company must match Work Request Company."))
		if self.equipment != work_request.equipment:
			frappe.throw(_("Job Order Equipment must match Work Request Equipment."))

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
		if not before or before.company != self.company or before.department != self.department:
			return True
		protected_statuses = {"Assigned", "In Progress", "Completed", "Closed", "Cancelled"}
		return self.status in protected_statuses and before.status != self.status

# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now, nowdate, strip_html


REQUEST_FIELDS = (
	"use_department",
	"request_category",
	"equipment",
	"job_order",
	"work_request",
	"physical_location",
	"justification",
)
REQUEST_ITEM_FIELDS = ("item_code", "requested_qty", "date_needed", "remarks")
REQUESTOR_EDIT_STATES = {"Draft", "Returned for Correction"}
MANAGEMENT_HEADER_FIELDS = ("bypass_other_management_approval", "bypass_reason")


class MRS(Document):
	def before_validate(self):
		if self.is_new():
			self.prepared_by = frappe.session.user
			self.request_date = self.request_date or nowdate()

		self.company = self.company or _get_default_company()
		self._set_requestor_context()
		self._apply_job_order_defaults()
		self._set_equipment_defaults()
		self._set_item_defaults()
		self._set_item_search_text()
		self._initialize_authorized_quantities()
		self._resolve_approvers()
		self._set_item_decisions()

	def validate(self):
		self._validate_prepared_by_immutable()
		self._validate_request_lock()
		self._validate_management_edits()
		self._validate_items()
		self._validate_approval_configuration()
		self._validate_bypass()
		self._validate_rejection()
		self._validate_owner_signature()

	def before_save(self):
		self._stamp_approval_transition()

	def _set_requestor_context(self):
		before = None if self.is_new() else self.get_doc_before_save()
		if before and (before.status or "Draft") != "Draft":
			return
		if not self.prepared_by:
			return

		personnel = frappe.db.get_value(
			"CCPC Personnel",
			{"user": self.prepared_by, "active": 1},
			["personnel_name", "department"],
			as_dict=True,
		)
		if not personnel:
			frappe.throw(
				_("User {0} must have an active CCPC Personnel record before creating an MRS.").format(
					frappe.bold(self.prepared_by)
				)
			)
		self.prepared_by_name = personnel.personnel_name
		self.requesting_department = personnel.department

	def _apply_job_order_defaults(self):
		if not self.job_order:
			return
		context = frappe.get_cached_value(
			"Job Order",
			self.job_order,
			["company", "equipment", "department", "asset_location"],
			as_dict=True,
		)
		if not context:
			frappe.throw(_("Job Order {0} does not exist.").format(frappe.bold(self.job_order)))
		self.company = context.company or self.company
		self.equipment = context.equipment or self.equipment
		self.use_department = self.use_department or context.department
		self.physical_location = self.physical_location or context.asset_location

	def _set_equipment_defaults(self):
		if not self.equipment:
			self.equipment_description = None
			return
		context = frappe.get_cached_value(
			"Equipment Registry",
			self.equipment,
			["equipment_description", "department", "physical_location", "company"],
			as_dict=True,
		)
		if not context:
			frappe.throw(_("Equipment {0} does not exist.").format(frappe.bold(self.equipment)))
		self.equipment_description = context.equipment_description
		self.company = context.company or self.company
		self.use_department = self.use_department or context.department
		self.physical_location = self.physical_location or context.physical_location

	def _set_item_defaults(self):
		for row in self.items or []:
			if not row.item_code:
				continue
			item = frappe.get_cached_value(
				"Item",
				row.item_code,
				["item_name", "description", "stock_uom", "disabled"],
				as_dict=True,
			)
			if not item:
				frappe.throw(_("Item {0} does not exist.").format(frappe.bold(row.item_code)))
			if item.disabled:
				frappe.throw(_("Item {0} is disabled.").format(frappe.bold(row.item_code)))
			row.item_name = item.item_name
			row.description = item.description
			row.uom = item.stock_uom

	def _set_item_search_text(self):
		self.item_search_text = _build_item_search_text(self.items or [])

	def _initialize_authorized_quantities(self):
		before = None if self.is_new() else self.get_doc_before_save()
		if before and (before.status or "Draft") not in REQUESTOR_EDIT_STATES:
			return
		for row in self.items or []:
			row.authorized_qty = flt(row.requested_qty)

	def _resolve_approvers(self):
		before = None if self.is_new() else self.get_doc_before_save()
		if before and (before.status or "Draft") not in REQUESTOR_EDIT_STATES:
			return

		requesting_manager = _get_department_manager_identity(self.requesting_department)
		self.requesting_department_manager = requesting_manager.user
		self.requesting_department_manager_name = requesting_manager.personnel_name

		self.use_department_manager = None
		self.use_department_manager_name = None
		if self.use_department and self.use_department != self.requesting_department:
			use_manager = _get_department_manager_identity(self.use_department)
			self.use_department_manager = use_manager.user
			self.use_department_manager_name = use_manager.personnel_name

		settings = frappe.get_single("CCPC Approval Settings")
		plant_manager = _get_personnel_identity(settings.plant_manager)
		coo = _get_personnel_identity(settings.coo)
		self.plant_manager = plant_manager.user
		self.plant_manager_name = plant_manager.personnel_name
		self.coo = coo.user
		self.coo_name = coo.personnel_name
		self.owner_name = settings.owner_name

	def _set_item_decisions(self):
		pre_management = {
			"Draft",
			"Returned for Correction",
			"Pending Requesting Department Manager",
			"Pending Use Department Manager",
		}
		for row in self.items or []:
			if (self.status or "Draft") in pre_management:
				row.decision = "Pending"
			elif flt(row.authorized_qty) <= 0:
				row.decision = "Not Approved"
			elif flt(row.authorized_qty) == flt(row.requested_qty):
				row.decision = "Approved as Requested"
			else:
				row.decision = "Modified"

	def _validate_prepared_by_immutable(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if before and (before.prepared_by != self.prepared_by or before.requesting_department != self.requesting_department):
			frappe.throw(_("Prepared By and Requesting Department cannot be changed after the MRS is saved."))

	def _validate_request_lock(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		previous_status = before.status or "Draft"
		if previous_status == "Draft":
			return
		if previous_status == "Returned for Correction" and self._can_edit_returned_request():
			return
		for fieldname in REQUEST_FIELDS:
			if (before.get(fieldname) or None) != (self.get(fieldname) or None):
				frappe.throw(_("Request details cannot be changed after the MRS is submitted for approval."))
		before_items = [tuple(row.get(fieldname) for fieldname in REQUEST_ITEM_FIELDS) for row in before.get("items") or []]
		current_items = [tuple(row.get(fieldname) for fieldname in REQUEST_ITEM_FIELDS) for row in self.items or []]
		if before_items != current_items:
			frappe.throw(_("Requested items cannot be changed after the MRS is submitted for approval."))

	def _can_edit_returned_request(self):
		user = frappe.session.user
		if user == "Administrator" or "System Manager" in frappe.get_roles(user):
			return True
		return user == self.prepared_by

	def _validate_management_edits(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return

		header_changed = any(
			(before.get(fieldname) or None) != (self.get(fieldname) or None)
			for fieldname in MANAGEMENT_HEADER_FIELDS
		)
		authorized_qty_changed = [flt(row.authorized_qty) for row in before.get("items") or []] != [
			flt(row.authorized_qty) for row in self.items or []
		]
		if not header_changed and not authorized_qty_changed:
			return

		previous_status = before.status or "Draft"
		if (
			previous_status == "Returned for Correction"
			and self._can_edit_returned_request()
			and not header_changed
			and all(flt(row.authorized_qty) == flt(row.requested_qty) for row in self.items or [])
		):
			# During correction, Authorized Qty follows the requestor's corrected
			# Requested Qty automatically. This is not a management approval edit.
			return

		user = frappe.session.user
		allowed = False
		if previous_status == "Pending Plant Manager":
			allowed = user == self.plant_manager or (
				user == self.coo and bool(self.bypass_other_management_approval)
			)
		elif previous_status == "Pending COO":
			allowed = user == self.coo
		if not allowed:
			frappe.throw(
				_("Only the active Plant Manager or COO approval stage can change Authorized Qty or bypass controls.")
			)

	def _validate_items(self):
		if not self.items:
			frappe.throw(_("At least one item is required."))
		for row in self.items:
			if not row.item_code:
				frappe.throw(_("Item is required on row {0}.").format(row.idx))
			if flt(row.requested_qty) <= 0:
				frappe.throw(_("Requested Qty must be greater than zero on row {0}.").format(row.idx))
			if flt(row.authorized_qty) < 0:
				frappe.throw(_("Authorized Qty cannot be negative on row {0}.").format(row.idx))

	def _validate_approval_configuration(self):
		if (self.status or "Draft") in REQUESTOR_EDIT_STATES:
			return
		if not self.requesting_department_manager:
			frappe.throw(_("Requesting Department must have a CCPC Department Manager configured."))
		if self.use_department != self.requesting_department and not self.use_department_manager:
			frappe.throw(_("Use Department must have a CCPC Department Manager configured."))
		if not self.plant_manager:
			frappe.throw(_("Plant Manager must be configured in CCPC Approval Settings."))
		if not self.coo:
			frappe.throw(_("Chief Operating Officer must be configured in CCPC Approval Settings."))
		if not self.owner_name:
			frappe.throw(_("General Manager Name must be configured in CCPC Approval Settings."))

	def _validate_bypass(self):
		if self.bypass_other_management_approval and not (self.bypass_reason or "").strip():
			frappe.throw(_("Bypass Reason is required when bypassing the other management approval."))

	def _validate_rejection(self):
		if self.status == "Rejected" and not (self.rejection_reason or "").strip():
			frappe.throw(_("Rejection Reason is required before rejecting an MRS."))

	def _validate_owner_signature(self):
		if self.status != "Ready for Purchasing":
			return
		if not self.owner_signed:
			frappe.throw(_("General Manager Signed must be checked before the MRS can become Ready for Purchasing."))
		if not self.owner_signed_date:
			frappe.throw(_("General Manager Signed Date is required before the MRS can become Ready for Purchasing."))

	def _stamp_approval_transition(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before or before.status == self.status:
			return
		stamp = now()
		user = frappe.session.user
		if self.status == "Returned for Correction" and before.status in {
			"Pending Requesting Department Manager",
			"Pending Use Department Manager",
		}:
			self.requesting_department_manager_approved_on = None
			self.use_department_manager_approved_on = None
		elif before.status == "Pending Requesting Department Manager" and self.status in {"Pending Use Department Manager", "Pending Plant Manager"}:
			self.requesting_department_manager_approved_on = stamp
		elif before.status == "Pending Use Department Manager" and self.status == "Pending Plant Manager":
			self.use_department_manager_approved_on = stamp
		elif before.status == "Pending Plant Manager" and self.status == "Pending COO":
			self.plant_manager_approved_on = stamp
		elif before.status == "Pending Plant Manager" and self.status == "Pending General Manager Signature":
			if user == self.plant_manager:
				self.plant_manager_approved_on = stamp
				self.management_bypassed_role = "COO"
			elif user == self.coo:
				self.coo_approved_on = stamp
				self.management_bypassed_role = "Plant Manager"
			self.management_bypass_by = user
			self.management_bypass_by_name = _get_personnel_name_for_user(user)
		elif before.status == "Pending COO" and self.status == "Pending General Manager Signature":
			self.coo_approved_on = stamp
		elif before.status == "Pending General Manager Signature" and self.status == "Ready for Purchasing":
			self.owner_signed_recorded_by = user
			self.owner_signed_recorded_by_name = _get_personnel_name_for_user(user)
			self.owner_signed_recorded_on = stamp
			self.procurement_status = self.procurement_status or "Awaiting Purchasing"


def _build_item_search_text(items):
	parts = []
	seen = set()
	for row in items or []:
		for fieldname in ("item_code", "item_name", "description"):
			value = row.get(fieldname) if hasattr(row, "get") else getattr(row, fieldname, None)
			value = " ".join(strip_html(str(value or "")).split())
			if not value:
				continue
			key = value.casefold()
			if key in seen:
				continue
			seen.add(key)
			parts.append(value)
	return "\n".join(parts)


def _get_default_company():
	company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("company")
	if not company:
		frappe.throw(_("Set a default Company before creating an MRS."))
	return company


def _get_personnel_identity(personnel_name):
	if not personnel_name:
		return frappe._dict(user=None, personnel_name=None)

	personnel = frappe.db.get_value(
		"CCPC Personnel",
		personnel_name,
		["user", "personnel_name", "active"],
		as_dict=True,
	)
	if not personnel or not personnel.active:
		return frappe._dict(user=None, personnel_name=None)
	return frappe._dict(user=personnel.user, personnel_name=personnel.personnel_name)


def _get_personnel_name_for_user(user):
	if not user:
		return None
	return frappe.db.get_value(
		"CCPC Personnel",
		{"user": user, "active": 1},
		"personnel_name",
	) or frappe.db.get_value("User", user, "full_name")


def _get_department_manager_identity(department):
	if not department:
		return frappe._dict(user=None, personnel_name=None)
	personnel_name = frappe.db.get_value("Department", department, "ccpc_department_manager")
	return _get_personnel_identity(personnel_name)

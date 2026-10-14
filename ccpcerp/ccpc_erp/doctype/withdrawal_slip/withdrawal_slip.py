# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr, flt, nowdate

from ccpcerp.ccpc_erp.equipment_costing import (
	validate_department_company,
	validate_equipment,
)


REQUEST_INTENT_HEADER_FIELDS = (
	"requestor",
	"required_by",
	"job_order",
	"equipment",
	"requesting_company",
	"department",
	"section",
	"work_for_department",
	"work_for_section",
	"physical_location",
	"purpose",
	"company",
)
REQUEST_INTENT_ITEM_FIELDS = ("item_code", "requested_qty", "remarks")
STOCKROOM_HEADER_FIELDS = ("source_warehouse", "received_by")
STOCKROOM_ITEM_FIELDS = (
	"issue_qty",
	"physical_discrepancy",
	"discrepancy_remarks",
	"stockroom_remarks",
)
ISSUE_STATES = {"Fully Issued", "Issued with Shortage", "Closed"}


class WithdrawalSlip(Document):
	def before_validate(self):
		if self.is_new():
			self.approving_supervisor = frappe.session.user
		if not self.required_by:
			self.required_by = nowdate()

		self._set_company_default()
		self._set_preparer_defaults()
		self._set_requesting_company_default()
		self._apply_job_order_defaults()
		self._set_work_for_department_from_equipment()
		self._set_equipment_details()
		self._set_physical_location_from_equipment()
		self._set_source_warehouse_default()
		self._set_item_defaults_and_availability()
		self._set_issue_qty_defaults()
		self._set_issue_tracking_totals()

	def validate(self):
		self._validate_approving_supervisor_immutable()
		self._validate_request_intent_lock()
		self._validate_stockroom_controls()
		self._validate_job_order_context()
		validate_equipment(
			self.equipment,
			self.requesting_company,
			require_active=bool(self.equipment),
			context=_("Withdrawal Slip"),
		)
		validate_department_company(
			self.department,
			self.requesting_company,
			_("Withdrawal Slip"),
			require_eligible=True,
		)
		self._validate_section_requirement(self.department, self.section, _("Requesting Section"), "department", "section")
		self._validate_section(self.section, self.department, _("Requesting Section"))
		if self.work_for_department:
			validate_department_company(self.work_for_department, frappe.get_cached_value("Department", self.work_for_department, "company"), _("Withdrawal Slip Work For"), require_eligible=True)
		self._validate_section_requirement(self.work_for_department, self.work_for_section, _("Work For Section"), "work_for_department", "work_for_section")
		self._validate_section(self.work_for_section, self.work_for_department, _("Work For Section"))
		self._validate_source_warehouse()
		self._validate_requested_quantities()
		self._validate_issue_tracking()

	def _set_company_default(self):
		if self.company:
			return
		self.company = _get_default_company()

	def _set_preparer_defaults(self):
		user = self.approving_supervisor or frappe.session.user
		if not user:
			return
		personnel = frappe.db.get_value(
			"CCPC Personnel", {"user": user, "active": 1},
			["personnel_name", "company", "department", "section"], as_dict=True
		)
		if not personnel:
			return
		self.prepared_by_name = personnel.personnel_name
		if self.is_new():
			if not self.requesting_company and personnel.company:
				self.requesting_company = personnel.company
			if not self.department and personnel.department:
				self.department = personnel.department
			if not self.section and personnel.section:
				self.section = personnel.section

	def _set_requesting_company_default(self):
		if not self.requesting_company:
			self.requesting_company = self.company

	def _set_work_for_department_from_equipment(self):
		if not self.equipment:
			return
		equipment_context = frappe.get_cached_value(
			"Equipment Registry", self.equipment, ["department", "section"], as_dict=True
		)
		if not equipment_context:
			return
		if equipment_context.department and not self.work_for_department:
			self.work_for_department = equipment_context.department
		if equipment_context.section and not self.work_for_section:
			self.work_for_section = equipment_context.section

	def _validate_section_requirement(self, department, section, label, department_field, section_field):
		if not department or section:
			return
		before = None if self.is_new() else self.get_doc_before_save()
		context_changed = (
			self.is_new()
			or not before
			or before.get(department_field) != department
			or before.get(section_field) != section
		)
		if not context_changed:
			return
		if frappe.db.exists("CCPC Section", {"department": department, "disabled": 0}):
			frappe.throw(_("{0} is required for Department {1}.").format(label, frappe.bold(department)))

	def _validate_section(self, section, department, label):
		if not section:
			return
		values = frappe.get_cached_value(
			"CCPC Section", section, ["department", "company", "disabled"], as_dict=True
		)
		if not values or values.disabled:
			frappe.throw(_("{0} {1} is invalid or disabled.").format(label, frappe.bold(section)))
		if not department or values.department != department:
			frappe.throw(
				_("{0} {1} does not belong to Department {2}.").format(
					label, frappe.bold(section), frappe.bold(department or "")
				)
			)

	def _apply_job_order_defaults(self):
		if not self.job_order:
			return

		before = None if self.is_new() else self.get_doc_before_save()
		if before and before.job_order == self.job_order:
			return

		context = frappe.get_cached_value(
			"Job Order",
			self.job_order,
			["company", "equipment", "department", "asset_location"],
			as_dict=True,
		)
		if not context:
			frappe.throw(_("Job Order {0} does not exist.").format(frappe.bold(self.job_order)))

		if context.company:
			self.company = context.company
		self.equipment = context.equipment or None
		if not self.work_for_department and context.department:
			self.work_for_department = context.department
		if not self.equipment and not self.physical_location and context.asset_location:
			self.physical_location = context.asset_location

	def _set_company_from_equipment(self):
		if not self.equipment or self.company:
			return
		self.company = frappe.get_cached_value("Equipment Registry", self.equipment, "company")

	def _set_equipment_details(self):
		if not self.equipment:
			self.equipment_description = None
			return
		self.equipment_description = frappe.get_cached_value(
			"Equipment Registry", self.equipment, "equipment_description"
		)

	def _set_physical_location_from_equipment(self):
		if not self.equipment:
			return

		new_location = frappe.get_cached_value("Equipment Registry", self.equipment, "physical_location")
		if not new_location:
			return

		before = None if self.is_new() else self.get_doc_before_save()
		previous_equipment = before.equipment if before else None
		previous_location = (
			frappe.get_cached_value("Equipment Registry", previous_equipment, "physical_location")
			if previous_equipment
			else None
		)
		if not self.physical_location or (
			previous_location and self.physical_location == previous_location
		):
			self.physical_location = new_location

	def _set_source_warehouse_default(self):
		if self.source_warehouse or not self.company:
			return
		self.source_warehouse = _get_default_source_warehouse(self.company)

	def _set_item_defaults_and_availability(self):
		if not self.source_warehouse:
			return

		for row in self.items or []:
			if not row.item_code:
				continue
			item = frappe.get_cached_value(
				"Item",
				row.item_code,
				["item_name", "stock_uom", "disabled", "is_stock_item"],
				as_dict=True,
			)
			if not item:
				frappe.throw(_("Item {0} does not exist.").format(frappe.bold(row.item_code)))
			if item.disabled:
				frappe.throw(_("Item {0} is disabled.").format(frappe.bold(row.item_code)))
			if not item.is_stock_item:
				frappe.throw(
					_("Item {0} is not a stock item and cannot be withdrawn.").format(
						frappe.bold(row.item_code)
					)
				)
			row.item_name = item.item_name
			row.uom = item.stock_uom
			row.available_qty = _get_actual_qty(row.item_code, self.source_warehouse)

	def _validate_approving_supervisor_immutable(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if before and before.approving_supervisor != self.approving_supervisor:
			frappe.throw(_("Prepared By cannot be changed after the Withdrawal Slip is saved."))
		if before and before.owner != self.owner:
			frappe.throw(_("Withdrawal Slip creator cannot be changed after the document is saved."))

	def _validate_job_order_context(self):
		if not self.job_order:
			return
		context = frappe.get_cached_value(
			"Job Order", self.job_order, ["company", "equipment"], as_dict=True
		)
		if not context:
			frappe.throw(_("Job Order {0} does not exist.").format(frappe.bold(self.job_order)))
		if self.company != context.company:
			frappe.throw(_("Withdrawal Slip Company must match the selected Job Order."))
		if (self.equipment or None) != (context.equipment or None):
			frappe.throw(_("Withdrawal Slip Equipment must match the selected Job Order."))

	def _validate_source_warehouse(self):
		if not self.source_warehouse:
			frappe.throw(
				_(
					"Warehouse 1 could not be resolved for Company {0}. Configure the warehouse before using Withdrawal Slip."
				).format(frappe.bold(self.company))
			)

		warehouse = frappe.get_cached_value(
			"Warehouse",
			self.source_warehouse,
			["company", "is_group", "disabled"],
			as_dict=True,
		)
		if not warehouse:
			frappe.throw(
				_("Warehouse {0} does not exist.").format(frappe.bold(self.source_warehouse))
			)
		if warehouse.company != self.company:
			frappe.throw(_("Source Warehouse must belong to the Withdrawal Slip Company."))
		if warehouse.is_group:
			frappe.throw(_("Source Warehouse must be a leaf warehouse."))
		if warehouse.disabled:
			frappe.throw(_("Source Warehouse is disabled."))

	def _validate_request_intent_lock(self):
		if self.is_new():
			return

		before = self.get_doc_before_save()
		if not before or not self._request_intent_changed(before):
			return

		previous_state = before.status or "Draft"
		if previous_state == "Draft":
			return
		if previous_state == "Returned for Correction" and self._can_edit_returned_request(before):
			return

		frappe.throw(
			_(
				"Approved Withdrawal Slip request details cannot be changed. Return the slip for correction before changing the request."
			)
		)

	def _request_intent_changed(self, before):
		for fieldname in REQUEST_INTENT_HEADER_FIELDS:
			if _normalize_request_value(fieldname, before.get(fieldname)) != _normalize_request_value(
				fieldname, self.get(fieldname)
			):
				return True

		before_items = [
			tuple(
				_normalize_request_value(fieldname, row.get(fieldname))
				for fieldname in REQUEST_INTENT_ITEM_FIELDS
			)
			for row in before.items or []
		]
		current_items = [
			tuple(
				_normalize_request_value(fieldname, row.get(fieldname))
				for fieldname in REQUEST_INTENT_ITEM_FIELDS
			)
			for row in self.items or []
		]
		return before_items != current_items

	def _can_edit_returned_request(self, before):
		user = frappe.session.user
		if user == "Administrator" or "System Manager" in frappe.get_roles(user):
			return True
		return user == before.approving_supervisor

	def _set_issue_qty_defaults(self):
		if self.is_new() or self.status != "Stockroom Review":
			return

		before = self.get_doc_before_save()
		if not before or (before.status or "Draft") == "Stockroom Review":
			return

		for row in self.items or []:
			row.issue_qty = flt(row.requested_qty)

	def _set_issue_tracking_totals(self):
		for row in self.items or []:
			issued_qty = flt(row.issued_qty)
			returned_qty = flt(row.returned_qty)
			tracking_started = bool(issued_qty or returned_qty or self.status in ISSUE_STATES)
			row.shortage_qty = (
				max(flt(row.requested_qty) - issued_qty, 0) if tracking_started else 0
			)
			row.net_used_qty = max(issued_qty - returned_qty, 0)

	def _validate_stockroom_controls(self):
		if self.is_new():
			return

		before = self.get_doc_before_save()
		if not before or not self._stockroom_controls_changed(before):
			return

		if self._can_edit_stockroom(before):
			return

		frappe.throw(
			_(
				"Stockroom-controlled fields can only be changed by Stockroom while the Withdrawal Slip is in Stockroom Review."
			)
		)

	def _stockroom_controls_changed(self, before):
		for fieldname in STOCKROOM_HEADER_FIELDS:
			if (before.get(fieldname) or None) != (self.get(fieldname) or None):
				return True

		before_items = [
			tuple((row.get(fieldname) or None) for fieldname in STOCKROOM_ITEM_FIELDS)
			for row in before.items or []
		]
		current_items = [
			tuple((row.get(fieldname) or None) for fieldname in STOCKROOM_ITEM_FIELDS)
			for row in self.items or []
		]
		return before_items != current_items

	def _can_edit_stockroom(self, before):
		previous_state = before.status or "Draft"
		if previous_state != "Stockroom Review" and not (
			previous_state == "Approved" and self.status == "Stockroom Review"
		):
			return False
		user = frappe.session.user
		if user == "Administrator":
			return True
		roles = frappe.get_roles(user)
		return "System Manager" in roles or "Stockroom Personnel" in roles

	def _validate_issue_tracking(self):
		for row in self.items or []:
			requested_qty = flt(row.requested_qty)
			issue_qty = flt(row.issue_qty)
			issued_qty = flt(row.issued_qty)
			returned_qty = flt(row.returned_qty)

			if self.status in {"Stockroom Review", "Fully Issued", "Issued with Shortage"}:
				if issue_qty <= 0:
					frappe.throw(_("Issue Qty must be greater than zero on row {0}.").format(row.idx))
				if issue_qty > requested_qty:
					frappe.throw(
						_("Issue Qty cannot exceed Requested Qty on row {0}.").format(row.idx)
					)
				if issue_qty < requested_qty and not (row.stockroom_remarks or "").strip():
					frappe.throw(
						_(
							"Stockroom Remarks / Shortage Reason is required when Issue Qty is less than Requested Qty on row {0}."
						).format(row.idx)
					)

			if issued_qty < 0:
				frappe.throw(_("Issued Qty cannot be negative on row {0}.").format(row.idx))
			if issued_qty > requested_qty:
				frappe.throw(
					_("Issued Qty cannot exceed Requested Qty on row {0}.").format(row.idx)
				)
			if returned_qty < 0:
				frappe.throw(_("Returned Qty cannot be negative on row {0}.").format(row.idx))
			if returned_qty > issued_qty:
				frappe.throw(
					_("Returned Qty cannot exceed Issued Qty on row {0}.").format(row.idx)
				)
			if row.physical_discrepancy and not (row.discrepancy_remarks or "").strip():
				frappe.throw(
					_("Discrepancy Remarks are required on row {0}.").format(row.idx)
				)


	def _validate_requested_quantities(self):
		if not self.items:
			frappe.throw(_("At least one item is required."))

		before = None if self.is_new() else self.get_doc_before_save()
		enforce_current_stock = not before or (before.status or "Draft") in {
			"Draft",
			"Returned for Correction",
		}

		requested_by_item = defaultdict(float)
		available_by_item = {}
		for row in self.items:
			if not row.item_code:
				frappe.throw(_("Item is required on row {0}.").format(row.idx))
			if flt(row.requested_qty) <= 0:
				frappe.throw(
					_("Requested Qty must be greater than zero on row {0}.").format(row.idx)
				)
			requested_by_item[row.item_code] += flt(row.requested_qty)
			available_by_item[row.item_code] = flt(row.available_qty)

		if not enforce_current_stock:
			return

		for item_code, requested_qty in requested_by_item.items():
			available_qty = available_by_item[item_code]
			if requested_qty > available_qty:
				frappe.throw(
					_(
						"Requested quantity {0} for Item {1} exceeds the current available stock {2} in {3}."
					).format(
						frappe.bold(requested_qty),
						frappe.bold(item_code),
						frappe.bold(available_qty),
						frappe.bold(self.source_warehouse),
					)
				)


def _normalize_request_value(fieldname, value):
	if fieldname == "requested_qty":
		return flt(value)
	return cstr(value or "")


def _get_default_company():
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)


def _get_default_source_warehouse(company):
	if not company:
		return None
	warehouses = frappe.get_all(
		"Warehouse",
		filters={
			"company": company,
			"warehouse_name": "Warehouse 1",
			"is_group": 0,
			"disabled": 0,
		},
		pluck="name",
		limit_page_length=2,
	)
	return warehouses[0] if len(warehouses) == 1 else None


def _get_actual_qty(item_code, warehouse):
	if not item_code or not warehouse:
		return 0
	return flt(
		frappe.db.get_value(
			"Bin",
			{"item_code": item_code, "warehouse": warehouse},
			"actual_qty",
		)
		or 0
	)


@frappe.whitelist()
def get_form_defaults():
	company = frappe.defaults.get_user_default("Company")
	personnel = frappe.db.get_value(
		"CCPC Personnel",
		{"user": frappe.session.user, "active": 1},
		["personnel_name", "company", "department", "section"],
		as_dict=True,
	)
	return {
		"company": company,
		"source_warehouse": _get_default_source_warehouse(company),
		"prepared_by_name": personnel.personnel_name if personnel else None,
		"requesting_company": personnel.company if personnel and personnel.company else company,
		"department": personnel.department if personnel else None,
		"section": personnel.section if personnel else None,
	}


@frappe.whitelist()
def get_item_availability(item_code, warehouse):
	if not item_code:
		return {}
	item = frappe.get_cached_value(
		"Item",
		item_code,
		["item_name", "stock_uom", "disabled", "is_stock_item"],
		as_dict=True,
	)
	if not item:
		frappe.throw(_("Item {0} does not exist.").format(frappe.bold(item_code)))
	if item.disabled:
		frappe.throw(_("Item {0} is disabled.").format(frappe.bold(item_code)))
	if not item.is_stock_item:
		frappe.throw(
			_("Item {0} is not a stock item and cannot be withdrawn.").format(
				frappe.bold(item_code)
			)
		)
	return {
		"item_name": item.item_name,
		"uom": item.stock_uom,
		"available_qty": _get_actual_qty(item_code, warehouse),
	}


@frappe.whitelist()
def get_physical_location_suggestions(company=None):
	filters = {"physical_location": ["!=", ""]}
	if company:
		filters["company"] = company
	rows = frappe.get_all(
		"Equipment Registry",
		filters=filters,
		fields=["physical_location"],
		order_by="physical_location asc",
		limit_page_length=500,
	)
	return sorted({row.physical_location for row in rows if row.physical_location})


@frappe.whitelist()
def get_requestor_suggestions():
	rows = frappe.get_list(
		"Withdrawal Slip",
		filters={"requestor": ["!=", ""]},
		fields=["requestor"],
		order_by="modified desc",
		limit_page_length=500,
	)
	seen = set()
	result = []
	for row in rows:
		name = (row.requestor or "").strip()
		if not name:
			continue
		key = name.casefold()
		if key in seen:
			continue
		seen.add(key)
		result.append(name)
	return result

# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


PURCHASING_ROLES = {"Purchasing Personnel", "Purchasing Supervisor"}

AWAITING_PURCHASING = "Awaiting Purchasing"
MATERIAL_REQUEST_DRAFT = "Material Request Draft"
MATERIAL_REQUEST_SUBMITTED = "Material Request Submitted"
MATERIAL_REQUEST_CANCELLED = "Material Request Cancelled"


@frappe.whitelist()
def make_material_request(source_name):
	"""Create exactly one Purchase Material Request from an approved MRS."""
	_require_purchasing_role()
	frappe.has_permission("Material Request", "create", throw=True)

	if not source_name:
		frappe.throw(_("MRS is required."))

	# Serialize creation for one MRS so two purchasing users cannot create duplicates.
	frappe.db.sql("select name from `tabMRS` where name = %s for update", source_name)
	mrs = frappe.get_doc("MRS", source_name)
	mrs.check_permission("read")

	if mrs.status != "Ready for Purchasing":
		frappe.throw(
			_("MRS {0} must be Ready for Purchasing before creating a Material Request.").format(
				frappe.bold(mrs.name)
			)
		)

	existing = _existing_material_request(mrs)
	if existing:
		return {"material_request": existing, "created": False}

	approved_items = [row for row in mrs.items or [] if flt(row.authorized_qty) > 0]
	if not approved_items:
		frappe.throw(_("MRS {0} has no authorized quantity to purchase.").format(frappe.bold(mrs.name)))

	transaction_date = nowdate()
	material_request = frappe.new_doc("Material Request")
	material_request.material_request_type = "Purchase"
	material_request.company = mrs.company
	material_request.transaction_date = transaction_date
	material_request.mrs = mrs.name
	material_request.equipment = mrs.equipment
	material_request.department = mrs.use_department
	material_request.work_request = mrs.work_request

	adjusted_required_by = False
	for row in approved_items:
		required_by = row.date_needed
		if not required_by or getdate(required_by) < getdate(transaction_date):
			required_by = transaction_date
			adjusted_required_by = True
		material_request.append(
			"items",
			{
				"item_code": row.item_code,
				"qty": flt(row.authorized_qty),
				"schedule_date": required_by,
			},
		)

	material_request.run_method("set_missing_values")
	material_request.insert()

	_set_mrs_material_request(
		mrs.name,
		material_request.name,
		MATERIAL_REQUEST_DRAFT,
	)

	return {
		"material_request": material_request.name,
		"created": True,
		"required_by_adjusted": adjusted_required_by,
	}


def sync_material_request_status(doc, method=None):
	"""Mirror the linked Material Request lifecycle onto the requestor-visible MRS."""
	mrs_name = doc.get("mrs")
	if not mrs_name or not frappe.db.exists("MRS", mrs_name):
		return

	_set_mrs_material_request(
		mrs_name,
		doc.name,
		_material_request_procurement_status(doc.docstatus),
	)


def backfill_mrs_procurement_status():
	"""Initialize tracking for MRS records created before procurement tracking existed."""
	if not frappe.db.table_exists("MRS") or not frappe.db.has_column("MRS", "procurement_status"):
		return

	for row in frappe.get_all(
		"MRS",
		fields=["name", "status", "material_request", "procurement_status"],
	):
		if row.procurement_status:
			continue

		procurement_status = None
		if row.material_request and frappe.db.exists("Material Request", row.material_request):
			docstatus = frappe.db.get_value("Material Request", row.material_request, "docstatus")
			procurement_status = _material_request_procurement_status(docstatus)
		elif row.status == "Ready for Purchasing":
			procurement_status = AWAITING_PURCHASING

		if procurement_status:
			frappe.db.set_value(
				"MRS",
				row.name,
				"procurement_status",
				procurement_status,
				update_modified=False,
			)


def _existing_material_request(mrs):
	if mrs.material_request:
		if not frappe.db.exists("Material Request", mrs.material_request):
			frappe.throw(
				_("MRS {0} references missing Material Request {1}.").format(
					frappe.bold(mrs.name),
					frappe.bold(mrs.material_request),
				)
			)
		return mrs.material_request

	existing = frappe.db.get_value("Material Request", {"mrs": mrs.name}, "name")
	if not existing:
		return None

	docstatus = frappe.db.get_value("Material Request", existing, "docstatus")
	_set_mrs_material_request(
		mrs.name,
		existing,
		_material_request_procurement_status(docstatus),
	)
	return existing


def _set_mrs_material_request(mrs_name, material_request, procurement_status):
	frappe.db.set_value(
		"MRS",
		mrs_name,
		{
			"material_request": material_request,
			"procurement_status": procurement_status,
		},
		update_modified=False,
	)


def _material_request_procurement_status(docstatus):
	docstatus = int(docstatus or 0)
	if docstatus == 2:
		return MATERIAL_REQUEST_CANCELLED
	if docstatus == 1:
		return MATERIAL_REQUEST_SUBMITTED
	return MATERIAL_REQUEST_DRAFT


def _require_purchasing_role():
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	if user == "Administrator" or "System Manager" in roles or roles.intersection(PURCHASING_ROLES):
		return

	frappe.throw(
		_("Only Purchasing Personnel or Purchasing Supervisor can create a Material Request from an MRS."),
		frappe.PermissionError,
	)

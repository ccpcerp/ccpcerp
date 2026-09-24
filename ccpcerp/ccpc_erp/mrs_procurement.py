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
REQUEST_FOR_QUOTATION_DRAFT = "Request for Quotation Draft"
REQUEST_FOR_QUOTATION_SUBMITTED = "Request for Quotation Submitted"
REQUEST_FOR_QUOTATION_CANCELLED = "Request for Quotation Cancelled"


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

	# Once an RFQ exists, its lifecycle is the more advanced procurement stage.
	if _get_request_for_quotation_procurement_status(mrs_name):
		return

	_set_mrs_material_request(
		mrs_name,
		doc.name,
		_material_request_procurement_status(doc.docstatus),
	)


def set_request_for_quotation_mrs(doc, method=None):
	"""Derive the MRS provenance of an RFQ from its Material Request item links."""
	items = doc.get("items") or []
	material_requests = {row.get("material_request") for row in items if row.get("material_request")}
	has_unlinked_items = any(not row.get("material_request") for row in items)

	doc.mrs = _resolve_mrs_from_material_requests(
		material_requests,
		has_unlinked_items=has_unlinked_items,
		strict=True,
	)


def sync_request_for_quotation_status(doc, method=None):
	"""Mirror aggregate RFQ progress onto the requestor-visible MRS."""
	mrs_name = doc.get("mrs")
	if not mrs_name or not frappe.db.exists("MRS", mrs_name):
		return

	procurement_status = _get_request_for_quotation_procurement_status(mrs_name)
	if procurement_status:
		_set_mrs_procurement_status(mrs_name, procurement_status)


def backfill_mrs_procurement_status():
	"""Initialize or advance tracking for records created before procurement tracking existed."""
	if not frappe.db.table_exists("MRS") or not frappe.db.has_column("MRS", "procurement_status"):
		return

	_backfill_request_for_quotation_mrs_links()

	for row in frappe.get_all(
		"MRS",
		fields=["name", "status", "material_request", "procurement_status"],
	):
		procurement_status = _get_request_for_quotation_procurement_status(row.name)

		if not procurement_status and row.material_request and frappe.db.exists(
			"Material Request", row.material_request
		):
			docstatus = frappe.db.get_value("Material Request", row.material_request, "docstatus")
			procurement_status = _material_request_procurement_status(docstatus)
		elif not procurement_status and row.status == "Ready for Purchasing":
			procurement_status = AWAITING_PURCHASING

		if procurement_status and row.procurement_status != procurement_status:
			frappe.db.set_value(
				"MRS",
				row.name,
				"procurement_status",
				procurement_status,
				update_modified=False,
			)


def _backfill_request_for_quotation_mrs_links():
	if (
		not frappe.db.table_exists("Request for Quotation")
		or not frappe.db.table_exists("Request for Quotation Item")
		or not frappe.db.has_column("Request for Quotation", "mrs")
	):
		return

	for rfq in frappe.get_all("Request for Quotation", fields=["name", "mrs"]):
		if rfq.mrs:
			continue

		items = frappe.get_all(
			"Request for Quotation Item",
			filters={"parent": rfq.name, "parenttype": "Request for Quotation"},
			fields=["material_request"],
			limit_page_length=0,
		)
		material_requests = {row.material_request for row in items if row.material_request}
		has_unlinked_items = any(not row.material_request for row in items)
		mrs_name = _resolve_mrs_from_material_requests(
			material_requests,
			has_unlinked_items=has_unlinked_items,
			strict=False,
		)
		if mrs_name:
			frappe.db.set_value(
				"Request for Quotation",
				rfq.name,
				"mrs",
				mrs_name,
				update_modified=False,
			)


def _resolve_mrs_from_material_requests(material_requests, has_unlinked_items=False, strict=True):
	material_requests = {name for name in material_requests if name}
	if not material_requests:
		return None

	rows = frappe.get_all(
		"Material Request",
		filters={"name": ["in", list(material_requests)]},
		fields=["name", "mrs"],
		limit_page_length=0,
	)
	mrs_by_request = {row.name: row.mrs for row in rows}
	tracked_mrs = {mrs_name for mrs_name in mrs_by_request.values() if mrs_name}
	untracked_requests = {
		name for name in material_requests if not mrs_by_request.get(name)
	}

	ambiguous = len(tracked_mrs) > 1 or bool(tracked_mrs and (untracked_requests or has_unlinked_items))
	if ambiguous:
		if strict:
			frappe.throw(
				_(
					"An MRS-linked Request for Quotation cannot mix items from different MRS records "
					"or items without the same MRS provenance."
				)
			)
		return None

	return next(iter(tracked_mrs), None)


def _get_request_for_quotation_procurement_status(mrs_name):
	if (
		not frappe.db.table_exists("Request for Quotation")
		or not frappe.db.has_column("Request for Quotation", "mrs")
	):
		return None

	docstatuses = frappe.get_all(
		"Request for Quotation",
		filters={"mrs": mrs_name},
		pluck="docstatus",
		limit_page_length=0,
	)
	return _request_for_quotation_procurement_status(docstatuses)


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


def _set_mrs_procurement_status(mrs_name, procurement_status):
	frappe.db.set_value(
		"MRS",
		mrs_name,
		"procurement_status",
		procurement_status,
		update_modified=False,
	)


def _material_request_procurement_status(docstatus):
	docstatus = int(docstatus or 0)
	if docstatus == 2:
		return MATERIAL_REQUEST_CANCELLED
	if docstatus == 1:
		return MATERIAL_REQUEST_SUBMITTED
	return MATERIAL_REQUEST_DRAFT


def _request_for_quotation_procurement_status(docstatuses):
	docstatuses = {int(docstatus or 0) for docstatus in docstatuses}
	if 1 in docstatuses:
		return REQUEST_FOR_QUOTATION_SUBMITTED
	if 0 in docstatuses:
		return REQUEST_FOR_QUOTATION_DRAFT
	if 2 in docstatuses:
		return REQUEST_FOR_QUOTATION_CANCELLED
	return None


def _require_purchasing_role(user=None):
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	if user == "Administrator" or "System Manager" in roles or roles.intersection(PURCHASING_ROLES):
		return

	frappe.throw(
		_("Only Purchasing Personnel or Purchasing Supervisor can create a Material Request from an MRS."),
		frappe.PermissionError,
	)

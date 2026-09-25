# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import escape_html, flt, getdate, nowdate, validate_email_address


PURCHASING_ROLES = {"Purchasing Personnel", "Purchasing Supervisor"}

AWAITING_PURCHASING = "Awaiting Purchasing"
MATERIAL_REQUEST_DRAFT = "Material Request Draft"
MATERIAL_REQUEST_SUBMITTED = "Material Request Submitted"
MATERIAL_REQUEST_CANCELLED = "Material Request Cancelled"
REQUEST_FOR_QUOTATION_DRAFT = "Request for Quotation Draft"
REQUEST_FOR_QUOTATION_SUBMITTED = "Request for Quotation Submitted"
REQUEST_FOR_QUOTATION_CANCELLED = "Request for Quotation Cancelled"
SUPPLIER_QUOTATION_DRAFT = "Supplier Quotation Draft"
SUPPLIER_QUOTATION_RECEIVED = "Supplier Quotation Received"
PURCHASE_ORDER_DRAFT = "Purchase Order Draft"
PURCHASE_ORDER_SUBMITTED = "Purchase Order Submitted"
RECEIVING_IN_PROGRESS = "Receiving in Progress"
PARTIALLY_RECEIVED = "Partially Received"
FULLY_RECEIVED = "Fully Received"


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
	"""Keep the MRS backlink current without letting an upstream event hide later progress."""
	mrs_name = doc.get("mrs")
	if not mrs_name or not frappe.db.exists("MRS", mrs_name):
		return

	_set_mrs_material_request(
		mrs_name,
		doc.name,
		_material_request_procurement_status(doc.docstatus),
	)
	_sync_mrs_procurement_status(mrs_name)


def set_request_for_quotation_mrs(doc, method=None):
	"""Derive the MRS provenance of an RFQ from its Material Request item links."""
	items = doc.get("items") or []
	material_requests = {row.get("material_request") for row in items if row.get("material_request")}
	has_unlinked_items = any(not row.get("material_request") for row in items)
	previous_mrs = doc.get("mrs")

	doc.mrs = _resolve_mrs_from_material_requests(
		material_requests,
		has_unlinked_items=has_unlinked_items,
		strict=True,
	)
	if previous_mrs and previous_mrs != doc.mrs:
		doc.flags.ccpc_previous_mrs = previous_mrs


def sync_request_for_quotation_status(doc, method=None):
	"""Mirror aggregate procurement progress after an RFQ lifecycle change."""
	_sync_mrs_names(doc)


def set_supplier_quotation_mrs(doc, method=None):
	"""Derive Supplier Quotation MRS provenance from its standard RFQ/MR item links."""
	items = doc.get("items") or []
	request_for_quotations = {
		row.get("request_for_quotation") for row in items if row.get("request_for_quotation")
	}
	material_requests = {row.get("material_request") for row in items if row.get("material_request")}
	has_unlinked_items = any(
		not row.get("request_for_quotation") and not row.get("material_request") for row in items
	)
	previous_mrs = doc.get("mrs")

	doc.mrs = _resolve_mrs_from_procurement_sources(
		request_for_quotations,
		material_requests,
		has_unlinked_items=has_unlinked_items,
		strict=True,
	)
	if previous_mrs and previous_mrs != doc.mrs:
		doc.flags.ccpc_previous_mrs = previous_mrs


def sync_supplier_quotation_status(doc, method=None):
	"""Mirror aggregate Supplier Quotation progress onto the requestor-visible MRS."""
	_sync_mrs_names(doc)


def set_purchase_order_mrs(doc, method=None):
	"""Derive Purchase Order MRS provenance from its standard SQ/MR item links."""
	items = doc.get("items") or []
	supplier_quotations = {
		row.get("supplier_quotation") for row in items if row.get("supplier_quotation")
	}
	material_requests = {row.get("material_request") for row in items if row.get("material_request")}
	has_unlinked_items = any(
		not row.get("supplier_quotation") and not row.get("material_request") for row in items
	)
	previous_mrs = doc.get("mrs")

	doc.mrs = _resolve_mrs_from_purchase_order_sources(
		supplier_quotations,
		material_requests,
		has_unlinked_items=has_unlinked_items,
		strict=True,
	)
	if previous_mrs and previous_mrs != doc.mrs:
		doc.flags.ccpc_previous_mrs = previous_mrs


def sync_purchase_order_status(doc, method=None):
	"""Mirror aggregate Purchase Order progress onto the requestor-visible MRS."""
	_sync_mrs_names(doc)


def set_purchase_receipt_mrs(doc, method=None):
	"""Derive Purchase Receipt MRS provenance from its standard PO/MR item links."""
	items = doc.get("items") or []
	purchase_orders = {row.get("purchase_order") for row in items if row.get("purchase_order")}
	material_requests = {row.get("material_request") for row in items if row.get("material_request")}
	has_unlinked_items = any(
		not row.get("purchase_order") and not row.get("material_request") for row in items
	)
	previous_mrs = doc.get("mrs")

	doc.mrs = _resolve_mrs_from_purchase_receipt_sources(
		purchase_orders,
		material_requests,
		has_unlinked_items=has_unlinked_items,
		strict=True,
	)
	if previous_mrs and previous_mrs != doc.mrs:
		doc.flags.ccpc_previous_mrs = previous_mrs


def sync_purchase_receipt_status(doc, method=None):
	"""Mirror aggregate receiving progress onto the requestor-visible MRS."""
	_sync_mrs_names(doc)


def notify_mrs_requestor_of_receipt(doc, method=None):
	"""Email the originating MRS requestor after Stockroom submits a receipt.

	Email is advisory and must never block stock receipt posting. If DEVELOPMENT
	or another site has no usable outgoing account, the notification is skipped
	quietly until an outgoing account is deliberately configured.
	"""
	if int(doc.docstatus or 0) != 1 or not doc.get("mrs") or doc.get("is_return"):
		return

	sender = _get_procurement_email_sender()
	if not sender:
		return

	mrs = frappe.db.get_value(
		"MRS",
		doc.mrs,
		["prepared_by", "prepared_by_name", "procurement_status"],
		as_dict=True,
	)
	if not mrs or not mrs.prepared_by:
		return

	recipient = frappe.db.get_value("User", mrs.prepared_by, "email") or mrs.prepared_by
	if not validate_email_address(recipient):
		return

	status = mrs.procurement_status or _get_purchase_receipt_procurement_status(doc.mrs)
	subject, message = _build_purchase_receipt_requestor_email(doc, mrs, status)

	try:
		frappe.sendmail(
			recipients=[recipient],
			sender=sender,
			subject=subject,
			message=message,
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(
			title="Purchase Receipt requestor email failed",
			message=frappe.get_traceback(),
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)


def _get_procurement_email_sender():
	"""Use Buying's fixed account when configured, otherwise the default outgoing account."""
	fixed_account = frappe.db.get_single_value("Buying Settings", "fixed_email")
	if fixed_account:
		account = frappe.db.get_value(
			"Email Account",
			fixed_account,
			["email_id", "enable_outgoing"],
			as_dict=True,
		)
		if account and account.enable_outgoing and account.email_id:
			return account.email_id

	return frappe.db.get_value(
		"Email Account",
		{"default_outgoing": 1, "enable_outgoing": 1},
		"email_id",
	)


def _build_purchase_receipt_requestor_email(doc, mrs, status):
	status = status or PARTIALLY_RECEIVED
	requestor_name = mrs.prepared_by_name or mrs.prepared_by or _("Requestor")
	supplier = doc.get("supplier_name") or doc.get("supplier") or ""
	status_text = _(status)

	if status == FULLY_RECEIVED:
		subject = _("Items Fully Received for {0}").format(doc.mrs)
	else:
		subject = _("Delivery Received for {0}").format(doc.mrs)

	item_rows = []
	for row in doc.get("items") or []:
		item_name = row.get("item_name") or row.get("item_code") or ""
		accepted_qty = flt(row.get("qty"))
		rejected_qty = flt(row.get("rejected_qty"))
		uom = row.get("uom") or row.get("stock_uom") or ""
		warehouse = row.get("warehouse") or ""
		rejected_text = (
			_("; rejected {0} {1}").format(f"{rejected_qty:g}", escape_html(uom))
			if rejected_qty
			else ""
		)
		item_rows.append(
			"<li>{item}: {qty} {uom} {accepted}{rejected}; {warehouse_label}: {warehouse}</li>".format(
				item=escape_html(item_name),
				qty=f"{accepted_qty:g}",
				uom=escape_html(uom),
				accepted=_("accepted"),
				rejected=rejected_text,
				warehouse_label=_("Warehouse"),
				warehouse=escape_html(warehouse),
			)
		)

	message = (
		"<p>{greeting} {name},</p>"
		"<p>{intro}</p>"
		"<p><strong>{status_label}:</strong> {status}</p>"
		"<p><strong>{mrs_label}:</strong> {mrs}<br>"
		"<strong>{receipt_label}:</strong> {receipt}<br>"
		"<strong>{supplier_label}:</strong> {supplier}</p>"
		"<ul>{items}</ul>"
	).format(
		greeting=_("Hello"),
		name=escape_html(requestor_name),
		intro=_("Stockroom has recorded a supplier delivery for your material request."),
		status_label=_("Receiving Status"),
		status=escape_html(status_text),
		mrs_label=_("MRS"),
		mrs=escape_html(doc.mrs),
		receipt_label=_("Purchase Receipt"),
		receipt=escape_html(doc.name),
		supplier_label=_("Supplier"),
		supplier=escape_html(supplier),
		items="".join(item_rows),
	)
	return subject, message


def _sync_mrs_names(doc):
	mrs_names = {
		name
		for name in (doc.get("mrs"), doc.flags.get("ccpc_previous_mrs"))
		if name
	}
	for mrs_name in mrs_names:
		_sync_mrs_procurement_status(mrs_name)


def _sync_mrs_procurement_status(mrs_name):
	if not frappe.db.exists("MRS", mrs_name):
		return

	procurement_status = _get_purchase_receipt_procurement_status(mrs_name)
	if not procurement_status:
		procurement_status = _get_purchase_order_procurement_status(mrs_name)
	if not procurement_status:
		procurement_status = _get_supplier_quotation_procurement_status(mrs_name)
	if not procurement_status:
		procurement_status = _get_request_for_quotation_procurement_status(mrs_name)

	if not procurement_status:
		mrs = frappe.db.get_value(
			"MRS",
			mrs_name,
			["status", "material_request"],
			as_dict=True,
		)
		if not mrs:
			return

		if mrs.material_request and frappe.db.exists("Material Request", mrs.material_request):
			docstatus = frappe.db.get_value("Material Request", mrs.material_request, "docstatus")
			procurement_status = _material_request_procurement_status(docstatus)
		elif mrs.status == "Ready for Purchasing":
			procurement_status = AWAITING_PURCHASING

	if procurement_status:
		_set_mrs_procurement_status(mrs_name, procurement_status)


def backfill_mrs_procurement_status():
	"""Backfill downstream MRS links, then recalculate requestor-visible procurement progress."""
	if not frappe.db.table_exists("MRS") or not frappe.db.has_column("MRS", "procurement_status"):
		return

	_backfill_request_for_quotation_mrs_links()
	_backfill_supplier_quotation_mrs_links()
	_backfill_purchase_order_mrs_links()
	_backfill_purchase_receipt_mrs_links()

	for mrs_name in frappe.get_all("MRS", pluck="name"):
		_sync_mrs_procurement_status(mrs_name)


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


def _backfill_supplier_quotation_mrs_links():
	if (
		not frappe.db.table_exists("Supplier Quotation")
		or not frappe.db.table_exists("Supplier Quotation Item")
		or not frappe.db.has_column("Supplier Quotation", "mrs")
	):
		return

	for quotation in frappe.get_all("Supplier Quotation", fields=["name", "mrs"]):
		if quotation.mrs:
			continue

		items = frappe.get_all(
			"Supplier Quotation Item",
			filters={"parent": quotation.name, "parenttype": "Supplier Quotation"},
			fields=["request_for_quotation", "material_request"],
			limit_page_length=0,
		)
		request_for_quotations = {
			row.request_for_quotation for row in items if row.request_for_quotation
		}
		material_requests = {row.material_request for row in items if row.material_request}
		has_unlinked_items = any(
			not row.request_for_quotation and not row.material_request for row in items
		)
		mrs_name = _resolve_mrs_from_procurement_sources(
			request_for_quotations,
			material_requests,
			has_unlinked_items=has_unlinked_items,
			strict=False,
		)
		if mrs_name:
			frappe.db.set_value(
				"Supplier Quotation",
				quotation.name,
				"mrs",
				mrs_name,
				update_modified=False,
			)


def _backfill_purchase_order_mrs_links():
	if (
		not frappe.db.table_exists("Purchase Order")
		or not frappe.db.table_exists("Purchase Order Item")
		or not frappe.db.has_column("Purchase Order", "mrs")
	):
		return

	for purchase_order in frappe.get_all("Purchase Order", fields=["name", "mrs"]):
		if purchase_order.mrs:
			continue

		items = frappe.get_all(
			"Purchase Order Item",
			filters={"parent": purchase_order.name, "parenttype": "Purchase Order"},
			fields=["supplier_quotation", "material_request"],
			limit_page_length=0,
		)
		supplier_quotations = {
			row.supplier_quotation for row in items if row.supplier_quotation
		}
		material_requests = {row.material_request for row in items if row.material_request}
		has_unlinked_items = any(
			not row.supplier_quotation and not row.material_request for row in items
		)
		mrs_name = _resolve_mrs_from_purchase_order_sources(
			supplier_quotations,
			material_requests,
			has_unlinked_items=has_unlinked_items,
			strict=False,
		)
		if mrs_name:
			frappe.db.set_value(
				"Purchase Order",
				purchase_order.name,
				"mrs",
				mrs_name,
				update_modified=False,
			)


def _backfill_purchase_receipt_mrs_links():
	if (
		not frappe.db.table_exists("Purchase Receipt")
		or not frappe.db.table_exists("Purchase Receipt Item")
		or not frappe.db.has_column("Purchase Receipt", "mrs")
	):
		return

	for purchase_receipt in frappe.get_all("Purchase Receipt", fields=["name", "mrs"]):
		if purchase_receipt.mrs:
			continue

		items = frappe.get_all(
			"Purchase Receipt Item",
			filters={"parent": purchase_receipt.name, "parenttype": "Purchase Receipt"},
			fields=["purchase_order", "material_request"],
			limit_page_length=0,
		)
		purchase_orders = {row.purchase_order for row in items if row.purchase_order}
		material_requests = {row.material_request for row in items if row.material_request}
		has_unlinked_items = any(
			not row.purchase_order and not row.material_request for row in items
		)
		mrs_name = _resolve_mrs_from_purchase_receipt_sources(
			purchase_orders,
			material_requests,
			has_unlinked_items=has_unlinked_items,
			strict=False,
		)
		if mrs_name:
			frappe.db.set_value(
				"Purchase Receipt",
				purchase_receipt.name,
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


def _resolve_mrs_from_procurement_sources(
	request_for_quotations,
	material_requests,
	has_unlinked_items=False,
	strict=True,
):
	request_for_quotations = {name for name in request_for_quotations if name}
	material_requests = {name for name in material_requests if name}
	tracked_mrs = set()
	untracked_sources = False

	if request_for_quotations:
		rows = frappe.get_all(
			"Request for Quotation",
			filters={"name": ["in", list(request_for_quotations)]},
			fields=["name", "mrs"],
			limit_page_length=0,
		)
		mrs_by_rfq = {row.name: row.mrs for row in rows}
		tracked_mrs.update(mrs_name for mrs_name in mrs_by_rfq.values() if mrs_name)
		untracked_sources = untracked_sources or any(
			not mrs_by_rfq.get(name) for name in request_for_quotations
		)

	if material_requests:
		rows = frappe.get_all(
			"Material Request",
			filters={"name": ["in", list(material_requests)]},
			fields=["name", "mrs"],
			limit_page_length=0,
		)
		mrs_by_request = {row.name: row.mrs for row in rows}
		tracked_mrs.update(mrs_name for mrs_name in mrs_by_request.values() if mrs_name)
		untracked_sources = untracked_sources or any(
			not mrs_by_request.get(name) for name in material_requests
		)

	ambiguous = len(tracked_mrs) > 1 or bool(
		tracked_mrs and (untracked_sources or has_unlinked_items)
	)
	if ambiguous:
		if strict:
			frappe.throw(
				_(
					"An MRS-linked Supplier Quotation cannot mix items from different MRS records "
					"or items without the same MRS provenance."
				)
			)
		return None

	return next(iter(tracked_mrs), None)


def _resolve_mrs_from_purchase_order_sources(
	supplier_quotations,
	material_requests,
	has_unlinked_items=False,
	strict=True,
):
	supplier_quotations = {name for name in supplier_quotations if name}
	material_requests = {name for name in material_requests if name}
	tracked_mrs = set()
	untracked_sources = False

	if supplier_quotations:
		rows = frappe.get_all(
			"Supplier Quotation",
			filters={"name": ["in", list(supplier_quotations)]},
			fields=["name", "mrs"],
			limit_page_length=0,
		)
		mrs_by_quotation = {row.name: row.mrs for row in rows}
		tracked_mrs.update(mrs_name for mrs_name in mrs_by_quotation.values() if mrs_name)
		untracked_sources = untracked_sources or any(
			not mrs_by_quotation.get(name) for name in supplier_quotations
		)

	if material_requests:
		rows = frappe.get_all(
			"Material Request",
			filters={"name": ["in", list(material_requests)]},
			fields=["name", "mrs"],
			limit_page_length=0,
		)
		mrs_by_request = {row.name: row.mrs for row in rows}
		tracked_mrs.update(mrs_name for mrs_name in mrs_by_request.values() if mrs_name)
		untracked_sources = untracked_sources or any(
			not mrs_by_request.get(name) for name in material_requests
		)

	ambiguous = len(tracked_mrs) > 1 or bool(
		tracked_mrs and (untracked_sources or has_unlinked_items)
	)
	if ambiguous:
		if strict:
			frappe.throw(
				_(
					"An MRS-linked Purchase Order cannot mix items from different MRS records "
					"or items without the same MRS provenance."
				)
			)
		return None

	return next(iter(tracked_mrs), None)


def _resolve_mrs_from_purchase_receipt_sources(
	purchase_orders,
	material_requests,
	has_unlinked_items=False,
	strict=True,
):
	purchase_orders = {name for name in purchase_orders if name}
	material_requests = {name for name in material_requests if name}
	tracked_mrs = set()
	untracked_sources = False

	if purchase_orders:
		rows = frappe.get_all(
			"Purchase Order",
			filters={"name": ["in", list(purchase_orders)]},
			fields=["name", "mrs"],
			limit_page_length=0,
		)
		mrs_by_order = {row.name: row.mrs for row in rows}
		tracked_mrs.update(mrs_name for mrs_name in mrs_by_order.values() if mrs_name)
		untracked_sources = untracked_sources or any(
			not mrs_by_order.get(name) for name in purchase_orders
		)

	if material_requests:
		rows = frappe.get_all(
			"Material Request",
			filters={"name": ["in", list(material_requests)]},
			fields=["name", "mrs"],
			limit_page_length=0,
		)
		mrs_by_request = {row.name: row.mrs for row in rows}
		tracked_mrs.update(mrs_name for mrs_name in mrs_by_request.values() if mrs_name)
		untracked_sources = untracked_sources or any(
			not mrs_by_request.get(name) for name in material_requests
		)

	ambiguous = len(tracked_mrs) > 1 or bool(
		tracked_mrs and (untracked_sources or has_unlinked_items)
	)
	if ambiguous:
		if strict:
			frappe.throw(
				_(
					"An MRS-linked Purchase Receipt cannot mix items from different MRS records "
					"or items without the same MRS provenance."
				)
			)
		return None

	return next(iter(tracked_mrs), None)


def _get_purchase_receipt_procurement_status(mrs_name):
	if (
		not frappe.db.table_exists("Purchase Receipt")
		or not frappe.db.has_column("Purchase Receipt", "mrs")
	):
		return None

	docstatuses = frappe.get_all(
		"Purchase Receipt",
		filters={"mrs": mrs_name},
		pluck="docstatus",
		limit_page_length=0,
	)
	if not docstatuses:
		return None

	received_percentages = []
	if 1 in {int(docstatus or 0) for docstatus in docstatuses}:
		received_percentages = frappe.get_all(
			"Purchase Order",
			filters={"mrs": mrs_name, "docstatus": 1},
			pluck="per_received",
			limit_page_length=0,
		)

	return _purchase_receipt_procurement_status(docstatuses, received_percentages)


def _get_purchase_order_procurement_status(mrs_name):
	if (
		not frappe.db.table_exists("Purchase Order")
		or not frappe.db.has_column("Purchase Order", "mrs")
	):
		return None

	docstatuses = frappe.get_all(
		"Purchase Order",
		filters={"mrs": mrs_name},
		pluck="docstatus",
		limit_page_length=0,
	)
	return _purchase_order_procurement_status(docstatuses)


def _get_supplier_quotation_procurement_status(mrs_name):
	if (
		not frappe.db.table_exists("Supplier Quotation")
		or not frappe.db.has_column("Supplier Quotation", "mrs")
	):
		return None

	docstatuses = frappe.get_all(
		"Supplier Quotation",
		filters={"mrs": mrs_name},
		pluck="docstatus",
		limit_page_length=0,
	)
	return _supplier_quotation_procurement_status(docstatuses)


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


def _supplier_quotation_procurement_status(docstatuses):
	docstatuses = {int(docstatus or 0) for docstatus in docstatuses}
	if 1 in docstatuses:
		return SUPPLIER_QUOTATION_RECEIVED
	if 0 in docstatuses:
		return SUPPLIER_QUOTATION_DRAFT
	return None


def _purchase_order_procurement_status(docstatuses):
	docstatuses = {int(docstatus or 0) for docstatus in docstatuses}
	if 1 in docstatuses:
		return PURCHASE_ORDER_SUBMITTED
	if 0 in docstatuses:
		return PURCHASE_ORDER_DRAFT
	return None


def _purchase_receipt_procurement_status(docstatuses, received_percentages=None):
	docstatuses = {int(docstatus or 0) for docstatus in docstatuses}
	if 1 in docstatuses:
		received_percentages = [flt(value) for value in (received_percentages or [])]
		if received_percentages and all(value >= 100 for value in received_percentages):
			return FULLY_RECEIVED
		return PARTIALLY_RECEIVED
	if 0 in docstatuses:
		return RECEIVING_IN_PROGRESS
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

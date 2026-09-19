# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, get_datetime

from ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip import _get_actual_qty


ISSUABLE_STATES = {"Stockroom Review", "Partially Issued"}
EPSILON = 0.000001


@frappe.whitelist()
def make_material_issue(withdrawal_slip):
	_require_stockroom_user()

	slip = frappe.get_doc("Withdrawal Slip", withdrawal_slip)
	slip.check_permission("write")

	if slip.status not in ISSUABLE_STATES:
		frappe.throw(
			_("Withdrawal Slip {0} must be in Stockroom Review or Partially Issued.").format(
				frappe.bold(slip.name)
			)
		)
	if not slip.received_by:
		frappe.throw(_("Received By is required before creating a Material Issue."))

	existing = frappe.get_all(
		"Stock Entry",
		filters={
			"withdrawal_slip": slip.name,
			"docstatus": 0,
		},
		pluck="name",
		order_by="creation desc",
		limit_page_length=1,
	)
	if existing:
		return {"name": existing[0], "existing": 1}

	submitted_qty = _get_submitted_qty_by_slip_item(slip.name)
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = slip.company
	stock_entry.purpose = "Material Issue"
	stock_entry.set_stock_entry_type()
	stock_entry.from_warehouse = slip.source_warehouse
	stock_entry.withdrawal_slip = slip.name
	stock_entry.remarks = _(
		"Material issue against Withdrawal Slip {0}. Received by: {1}"
	).format(slip.name, slip.received_by)

	if stock_entry.meta.has_field("equipment"):
		stock_entry.equipment = slip.equipment
	if stock_entry.meta.has_field("department"):
		stock_entry.department = slip.department

	detail_meta = frappe.get_meta("Stock Entry Detail")
	for row in slip.items or []:
		remaining_qty = flt(row.requested_qty) - flt(submitted_qty.get(row.name))
		if remaining_qty <= EPSILON:
			continue

		values = {
			"item_code": row.item_code,
			"qty": remaining_qty,
			"uom": row.uom,
			"conversion_factor": 1,
			"s_warehouse": slip.source_warehouse,
			"withdrawal_slip_item": row.name,
		}
		if detail_meta.has_field("equipment"):
			values["equipment"] = slip.equipment
		if detail_meta.has_field("department"):
			values["department"] = slip.department
		stock_entry.append("items", values)

	if not stock_entry.items:
		sync_withdrawal_slip_issue_status_for_name(slip.name)
		frappe.throw(_("Withdrawal Slip {0} has no remaining quantity to issue.").format(slip.name))

	stock_entry.insert()
	return {"name": stock_entry.name, "existing": 0}


def validate_withdrawal_slip_stock_entry(doc, method=None):
	if not doc.get("withdrawal_slip") or doc.docstatus != 0:
		return

	_require_stockroom_user()
	slip = frappe.get_doc("Withdrawal Slip", doc.withdrawal_slip)

	if slip.status not in ISSUABLE_STATES:
		frappe.throw(
			_("Withdrawal Slip {0} cannot be issued while it is in status {1}.").format(
				frappe.bold(slip.name), frappe.bold(slip.status)
			)
		)
	if not slip.received_by:
		frappe.throw(_("Received By is required on the Withdrawal Slip before issuing stock."))
	if doc.company != slip.company:
		frappe.throw(_("Material Issue Company must match the Withdrawal Slip Company."))
	if doc.purpose != "Material Issue":
		frappe.throw(_("A Stock Entry linked to a Withdrawal Slip must use Material Issue."))
	if doc.from_warehouse and doc.from_warehouse != slip.source_warehouse:
		frappe.throw(_("Default Source Warehouse must match the Withdrawal Slip Source Warehouse."))

	_validate_provenance_immutable(doc)
	slip_items = {row.name: row for row in slip.items or []}
	current_by_slip_item = defaultdict(float)
	current_by_item = defaultdict(float)

	if not doc.items:
		frappe.throw(_("At least one Material Issue item is required."))

	for row in doc.items:
		if not row.withdrawal_slip_item:
			frappe.throw(
				_("Row {0}: every item must originate from the linked Withdrawal Slip.").format(row.idx)
			)
		request_row = slip_items.get(row.withdrawal_slip_item)
		if not request_row:
			frappe.throw(
				_("Row {0}: Withdrawal Slip Item reference is invalid.").format(row.idx)
			)
		if row.item_code != request_row.item_code:
			frappe.throw(
				_("Row {0}: Item must match the linked Withdrawal Slip row.").format(row.idx)
			)
		if row.s_warehouse != slip.source_warehouse:
			frappe.throw(
				_("Row {0}: Source Warehouse must match the Withdrawal Slip.").format(row.idx)
			)
		if row.t_warehouse:
			frappe.throw(_("Row {0}: Material Issue cannot have a Target Warehouse.").format(row.idx))

		stock_qty = _stock_qty(row)
		if stock_qty <= 0:
			frappe.throw(_("Row {0}: Issue quantity must be greater than zero.").format(row.idx))
		current_by_slip_item[row.withdrawal_slip_item] += stock_qty
		current_by_item[row.item_code] += stock_qty

	submitted_qty = _get_submitted_qty_by_slip_item(slip.name)
	for row_name, current_qty in current_by_slip_item.items():
		requested_qty = flt(slip_items[row_name].requested_qty)
		total_issued = flt(submitted_qty.get(row_name)) + current_qty
		if total_issued > requested_qty + EPSILON:
			frappe.throw(
				_(
					"Issue quantity for row {0} would exceed the approved Requested Qty {1}."
				).format(slip_items[row_name].idx, frappe.bold(requested_qty))
			)

	for item_code, issue_qty in current_by_item.items():
		actual_qty = _get_actual_qty(item_code, slip.source_warehouse)
		if issue_qty > actual_qty + EPSILON:
			frappe.throw(
				_(
					"Issue quantity {0} for Item {1} exceeds current Actual Qty {2} in {3}."
				).format(
					frappe.bold(issue_qty),
					frappe.bold(item_code),
					frappe.bold(actual_qty),
					frappe.bold(slip.source_warehouse),
				)
			)

	if method == "before_submit":
		doc.withdrawal_slip_issued_by = frappe.session.user


def sync_withdrawal_slip_issue_status(doc, method=None):
	if not doc.get("withdrawal_slip"):
		return
	sync_withdrawal_slip_issue_status_for_name(doc.withdrawal_slip)


def sync_withdrawal_slip_issue_status_for_name(withdrawal_slip):
	slip = frappe.get_doc("Withdrawal Slip", withdrawal_slip)
	submitted_qty = _get_submitted_qty_by_slip_item(slip.name)

	quantities = []
	any_issued = False
	fully_issued = True
	for row in slip.items or []:
		issued_qty = flt(submitted_qty.get(row.name))
		requested_qty = flt(row.requested_qty)
		returned_qty = flt(row.returned_qty)
		quantities.append((row, issued_qty, requested_qty, returned_qty))
		any_issued = any_issued or issued_qty > EPSILON
		if issued_qty + EPSILON < requested_qty:
			fully_issued = False

	for row, issued_qty, requested_qty, returned_qty in quantities:
		shortage_qty = max(requested_qty - issued_qty, 0) if any_issued else 0
		net_used_qty = max(issued_qty - returned_qty, 0)
		frappe.db.set_value(
			"Withdrawal Slip Item",
			row.name,
			{
				"issued_qty": issued_qty,
				"shortage_qty": shortage_qty,
				"net_used_qty": net_used_qty,
			},
			update_modified=False,
		)

	if not any_issued:
		status = "Stockroom Review"
	elif fully_issued:
		status = "Fully Issued"
	else:
		status = "Partially Issued"

	latest = _get_latest_submitted_issue(slip.name)
	issued_by = latest.withdrawal_slip_issued_by if latest else None
	issued_by_name = _get_user_full_name(issued_by)
	issued_on = (
		get_datetime(f"{latest.posting_date} {latest.posting_time}") if latest else None
	)

	frappe.db.set_value(
		"Withdrawal Slip",
		slip.name,
		{
			"status": status,
			"issued_by": issued_by,
			"issued_by_name": issued_by_name,
			"issued_on": issued_on,
		},
	)
	frappe.clear_cache(doctype="Withdrawal Slip")


def _get_submitted_qty_by_slip_item(withdrawal_slip):
	entry_names = frappe.get_all(
		"Stock Entry",
		filters={"withdrawal_slip": withdrawal_slip, "docstatus": 1},
		pluck="name",
	)
	if not entry_names:
		return {}

	rows = frappe.get_all(
		"Stock Entry Detail",
		filters={
			"parent": ["in", entry_names],
			"parenttype": "Stock Entry",
			"withdrawal_slip_item": ["is", "set"],
		},
		fields=["withdrawal_slip_item", "transfer_qty"],
	)
	result = defaultdict(float)
	for row in rows:
		result[row.withdrawal_slip_item] += flt(row.transfer_qty)
	return dict(result)


def _get_latest_submitted_issue(withdrawal_slip):
	rows = frappe.get_all(
		"Stock Entry",
		filters={"withdrawal_slip": withdrawal_slip, "docstatus": 1},
		fields=[
			"name",
			"posting_date",
			"posting_time",
			"withdrawal_slip_issued_by",
		],
		order_by="posting_date desc, posting_time desc, creation desc",
		limit_page_length=1,
	)
	return rows[0] if rows else None


def _validate_provenance_immutable(doc):
	if doc.is_new():
		return
	before = doc.get_doc_before_save()
	if not before:
		return
	if before.get("withdrawal_slip") != doc.get("withdrawal_slip"):
		frappe.throw(_("Withdrawal Slip reference cannot be changed after the Stock Entry is saved."))

	old_rows = {row.name: row for row in before.items or []}
	for row in doc.items or []:
		old_row = old_rows.get(row.name)
		if old_row and old_row.get("withdrawal_slip_item") != row.get("withdrawal_slip_item"):
			frappe.throw(
				_("Row {0}: Withdrawal Slip Item reference cannot be changed.").format(row.idx)
			)


def _stock_qty(row):
	if flt(row.transfer_qty):
		return flt(row.transfer_qty)
	return flt(row.qty) * flt(row.conversion_factor or 1)


def _get_user_full_name(user):
	if not user:
		return None
	return frappe.get_cached_value("User", user, "full_name") or user


def _require_stockroom_user():
	user = frappe.session.user
	if user == "Administrator":
		return
	roles = frappe.get_roles(user)
	if "Stock User" in roles or "System Manager" in roles:
		return
	frappe.throw(_("Only Stockroom users can issue a Withdrawal Slip."), frappe.PermissionError)

# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import logging
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, get_datetime

from ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip import _get_actual_qty


ISSUABLE_STATES = {"Stockroom Review"}
EPSILON = 0.000001


def _clear_withdrawal_slip_cache_after_commit():
	"""Best-effort cache refresh: never report a committed stock issue as failed."""
	try:
		frappe.clear_cache(doctype="Withdrawal Slip")
	except Exception:
		# The Stock Entry / ledger SQL is already committed at this point.
		# A cache outage must not provoke an ambiguous retry of a successful issue.
		logging.getLogger(__name__).exception("Post-commit Withdrawal Slip cache refresh failed")


def _lock_withdrawal_slip(withdrawal_slip):
	"""Serialize issue creation and submission on the same parent row."""
	rows = frappe.db.sql(
		"""SELECT status, received_by, company, source_warehouse,
		owner, approving_supervisor
		FROM `tabWithdrawal Slip` WHERE name = %s FOR UPDATE""",
		(withdrawal_slip,),
		as_dict=True,
	)
	if not rows:
		frappe.throw(_("Withdrawal Slip {0} does not exist.").format(frappe.bold(withdrawal_slip)))
	return rows[0]


def _get_existing_draft_names(withdrawal_slip):
	"""Use a locking/current read, not a possibly stale transaction snapshot."""
	rows = frappe.db.sql(
		"""SELECT name FROM `tabStock Entry`
		WHERE withdrawal_slip = %s AND docstatus = 0
		ORDER BY creation DESC FOR UPDATE""",
		(withdrawal_slip,),
		as_dict=True,
	)
	return [row.name for row in rows]


def _assert_no_submitted_material_issue(withdrawal_slip):
	"""Detect any submitted issue, even one with missing child provenance rows."""
	rows = frappe.db.sql(
		"""SELECT name FROM `tabStock Entry`
		WHERE withdrawal_slip = %s AND docstatus = 1
		LIMIT 1 FOR UPDATE""",
		(withdrawal_slip,),
		as_dict=True,
	)
	if rows:
		frappe.throw(
			_("Withdrawal Slip {0} already has a submitted Material Issue.").format(
				frappe.bold(withdrawal_slip)
			)
		)


def _assert_independent_issuer(locked):
	"""Enforce peer issuance using locked ERPNext account identities."""
	owner = locked.get("owner")
	prepared_by = locked.get("approving_supervisor")
	if not owner or not prepared_by or owner != prepared_by:
		frappe.throw(
			_("The Withdrawal Slip creator and Prepared By must match before issuing materials."),
			frappe.PermissionError,
		)

	issuer = frappe.session.user
	if not issuer or issuer in ("Guest", "Administrator"):
		frappe.throw(_("A signed-in Stockroom employee is required."), frappe.PermissionError)
	if "Stockroom Personnel" not in frappe.get_roles(issuer):
		frappe.throw(_("Only assigned Stockroom personnel may issue materials."), frappe.PermissionError)
	if issuer == owner:
		frappe.throw(
			_("A different authorized Stockroom employee must issue materials for this Withdrawal Slip."),
			frappe.PermissionError,
		)


def _get_issuable_slip(withdrawal_slip, locked):
	slip = frappe.get_doc("Withdrawal Slip", withdrawal_slip)
	slip.check_permission("write")
	# A previous nonlocking read may have opened a REPEATABLE READ snapshot.
	# Never issue from a stale Slip document after acquiring the row lock.
	for fieldname in ("status", "received_by", "company", "source_warehouse", "owner", "approving_supervisor"):
		if (slip.get(fieldname) or None) != (locked.get(fieldname) or None):
			frappe.throw(_("Withdrawal Slip changed during issuance. Please retry."))
	if slip.status not in ISSUABLE_STATES:
		frappe.throw(
			_("Withdrawal Slip {0} must be in Stockroom Review before stock can be issued.").format(
				frappe.bold(slip.name)
			)
		)
	if not slip.received_by:
		frappe.throw(_("Received By is required before creating a Material Issue."))
	_assert_independent_issuer(locked)
	return slip


def _build_material_issue(slip):
	"""Construct the only Stock Entry eligible for scoped stockroom posting."""
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

	detail_meta = frappe.get_meta("Stock Entry Detail")
	for row in slip.get("items") or []:
		requested_qty = flt(row.requested_qty)
		issue_qty = flt(row.issue_qty)
		if issue_qty <= 0 or issue_qty > requested_qty + EPSILON:
			frappe.throw(
				_("Row {0}: Issue Qty must be greater than zero and cannot exceed Requested Qty.").format(
					row.idx
				)
			)
		if issue_qty < requested_qty - EPSILON and not (row.stockroom_remarks or "").strip():
			frappe.throw(
				_(
					"Row {0}: Stockroom Remarks / Shortage Reason is required when Issue Qty is reduced."
				).format(row.idx)
			)

		values = {
			"item_code": row.item_code,
			"qty": issue_qty,
			"uom": row.uom,
			"conversion_factor": 1,
			"s_warehouse": slip.source_warehouse,
			"withdrawal_slip_item": row.name,
		}
		if detail_meta.has_field("equipment"):
			values["equipment"] = slip.equipment
		stock_entry.append("items", values)

	if not stock_entry.items:
		frappe.throw(_("Withdrawal Slip {0} has no items to issue.").format(slip.name))
	return stock_entry


@frappe.whitelist(methods=["POST"])
def make_material_issue(withdrawal_slip):
	"""Legacy draft action, only for users with regular Stock Entry creation rights."""
	_require_stockroom_user()
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(
			_("Use Issue Materials on the Withdrawal Slip to post stock with stockroom authorization."),
			frappe.PermissionError,
		)

	locked = _lock_withdrawal_slip(withdrawal_slip)
	slip = _get_issuable_slip(withdrawal_slip, locked)
	_assert_no_submitted_material_issue(slip.name)

	drafts = _get_existing_draft_names(slip.name)
	if len(drafts) > 1:
		frappe.throw(_("Multiple draft Material Issues exist for this Withdrawal Slip."))
	if drafts:
		return {"name": drafts[0], "existing": 1}

	stock_entry = _build_material_issue(slip)
	stock_entry.insert()
	return {"name": stock_entry.name, "existing": 0}


@frappe.whitelist(methods=["POST"])
def issue_withdrawal_slip(withdrawal_slip):
	"""Atomically submit one slip-bound Material Issue without broad Stock Entry roles.

	A normal POST request owns the transaction. Do not commit or catch exceptions:
	Frappe must roll back the inserted draft and any ledger/status changes on failure.
	"""
	_require_stockroom_user()
	locked = _lock_withdrawal_slip(withdrawal_slip)
	slip = _get_issuable_slip(withdrawal_slip, locked)
	_assert_no_submitted_material_issue(slip.name)

	drafts = _get_existing_draft_names(slip.name)
	if drafts:
		frappe.throw(
			_("Withdrawal Slip {0} already has a draft Material Issue. Have an authorized Stock Entry user review or discard that draft before issuing.").format(
				frappe.bold(slip.name)
			)
		)

	# Never elevate permissions on a pre-existing user-editable Stock Entry draft:
	# it could carry expense account, costing or other posting-related overrides.
	stock_entry = _build_material_issue(slip)
	stock_entry.flags.ignore_permissions = True
	stock_entry.insert(ignore_permissions=True)
	stock_entry.submit()  # Runs normal Stock Entry validators, stock ledger and GL hooks.
	return {"name": stock_entry.name, "submitted": 1}


def validate_withdrawal_slip_stock_entry(doc, method=None):
	if not doc.get("withdrawal_slip") or doc.docstatus not in (0, 1):
		return

	_require_stockroom_user()
	locked = _lock_withdrawal_slip(doc.withdrawal_slip) if method == "before_submit" else None
	slip = frappe.get_doc("Withdrawal Slip", doc.withdrawal_slip)
	if locked:
		for fieldname in ("status", "received_by", "company", "source_warehouse", "owner", "approving_supervisor"):
			if (slip.get(fieldname) or None) != (locked.get(fieldname) or None):
				frappe.throw(_("Withdrawal Slip changed during issuance. Please retry."))
		_assert_independent_issuer(locked)
		_assert_no_submitted_material_issue(slip.name)

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
	slip_items = {row.name: row for row in slip.get("items") or []}
	current_by_slip_item = defaultdict(float)
	current_by_item = defaultdict(float)

	if not doc.get("items"):
		frappe.throw(_("At least one Material Issue item is required."))

	for row in doc.get("items") or []:
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
	if any(flt(qty) > EPSILON for qty in submitted_qty.values()):
		frappe.throw(
			_("Withdrawal Slip {0} already has a submitted stock issue.").format(
				frappe.bold(slip.name)
			)
		)

	for row_name, request_row in slip_items.items():
		requested_qty = flt(request_row.requested_qty)
		planned_issue_qty = flt(request_row.issue_qty)
		if planned_issue_qty <= 0 or planned_issue_qty > requested_qty + EPSILON:
			frappe.throw(
				_("Row {0}: Issue Qty must be greater than zero and cannot exceed Requested Qty.").format(
					request_row.idx
				)
			)
		if planned_issue_qty < requested_qty - EPSILON and not (
			request_row.stockroom_remarks or ""
		).strip():
			frappe.throw(
				_(
					"Row {0}: Stockroom Remarks / Shortage Reason is required when Issue Qty is reduced."
				).format(request_row.idx)
			)

		material_issue_qty = flt(current_by_slip_item.get(row_name))
		if abs(material_issue_qty - planned_issue_qty) > EPSILON:
			frappe.throw(
				_(
					"Row {0}: Material Issue Qty must exactly match the stockroom Issue Qty {1}."
				).format(request_row.idx, frappe.bold(planned_issue_qty))
			)

	if method == "before_submit":
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
	for row in slip.get("items") or []:
		issued_qty = flt(submitted_qty.get(row.name))
		requested_qty = flt(row.requested_qty)
		returned_qty = flt(row.returned_qty)
		quantities.append((row, issued_qty, requested_qty, returned_qty))
		any_issued = any_issued or issued_qty > EPSILON
		if abs(issued_qty - requested_qty) > EPSILON:
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
		status = "Issued with Shortage"

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
	# Defer the explicit cache invalidation until the stock/GL transaction commits.
	# A full rollback discards the callback; SQL rollback cannot reverse Redis effects.
	frappe.db.after_commit.add(_clear_withdrawal_slip_cache_after_commit)


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

	old_rows = {row.name: row for row in before.get("items") or []}
	for row in doc.get("items") or []:
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
	if "Stockroom Personnel" in roles or "System Manager" in roles:
		return
	frappe.throw(_("Only Stockroom users can issue a Withdrawal Slip."), frappe.PermissionError)

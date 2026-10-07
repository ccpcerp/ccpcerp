# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce, Sum
from frappe.utils import flt


ALLOWED_ROLES = {"CCPC Stock Inquiry User", "System Manager"}


def execute(filters=None):
	_validate_access()
	filters = frappe._dict(filters or {})
	rows = _get_rows(filters)
	return get_columns(), rows


def _validate_access():
	if not set(frappe.get_roles()).intersection(ALLOWED_ROLES):
		frappe.throw(_("You are not permitted to view CCPC Item Stock Inquiry."), frappe.PermissionError)


def get_columns():
	return [
		{"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Data", "width": 140},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 300},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Data", "width": 180},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Data", "width": 70},
		{"label": _("Stock Qty"), "fieldname": "actual_qty", "fieldtype": "Float", "width": 105},
		{
			"label": _("Maintaining Balance"),
			"fieldname": "maintaining_balance",
			"fieldtype": "Float",
			"width": 135,
		},
		{
			"label": _("Expected Incoming Qty"),
			"fieldname": "incoming_qty",
			"fieldtype": "Float",
			"width": 145,
		},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 215},
	]


def _get_rows(filters):
	actual = _get_actual_qty(filters)
	maintaining = _get_maintaining_balance(filters)
	incoming = _get_expected_incoming(filters)
	keys = set(actual) | set(maintaining) | set(incoming)
	if not keys:
		return []

	item_map = _get_item_map({item_code for item_code, _warehouse in keys})
	search_text = (filters.get("search_item") or "").strip().lower()
	data = []

	for item_code, warehouse in sorted(keys, key=lambda value: (value[0], value[1])):
		item = item_map.get(item_code)
		if not item:
			continue
		if filters.get("item_group") and item.item_group != filters.item_group:
			continue
		if search_text and search_text not in " ".join(
			[item.name or "", item.item_name or "", item.description or ""]
		).lower():
			continue

		stock_qty = flt(actual.get((item_code, warehouse)))
		maintaining_balance = flt(maintaining.get((item_code, warehouse)))
		incoming_qty = flt(incoming.get((item_code, warehouse)))

		if filters.get("below_maintaining_only") and not (
			maintaining_balance > 0 and stock_qty < maintaining_balance
		):
			continue

		data.append(
			{
				"item_code": item_code,
				"item_name": item.item_name or item_code,
				"warehouse": warehouse,
				"stock_uom": item.stock_uom,
				"actual_qty": stock_qty,
				"maintaining_balance": maintaining_balance,
				"incoming_qty": incoming_qty,
				"status": _stock_status(stock_qty, maintaining_balance, incoming_qty),
			}
		)

	return data


def _stock_status(stock_qty, maintaining_balance, incoming_qty):
	stock_qty = flt(stock_qty)
	maintaining_balance = flt(maintaining_balance)
	incoming_qty = flt(incoming_qty)

	if maintaining_balance <= 0:
		return _("No Maintaining Balance Set")
	if stock_qty >= maintaining_balance:
		return _("OK")
	if incoming_qty > 0 and stock_qty + incoming_qty >= maintaining_balance:
		return _("Incoming - No New Order Needed")
	return _("Reorder Needed")


def _warehouse_conditions(query, warehouse_table, filters):
	if filters.get("company"):
		query = query.where(warehouse_table.company == filters.company)
	if filters.get("warehouse"):
		query = query.where(warehouse_table.name == filters.warehouse)
	return query


def _get_actual_qty(filters):
	bin_table = frappe.qb.DocType("Bin")
	warehouse = frappe.qb.DocType("Warehouse")
	query = (
		frappe.qb.from_(bin_table)
		.join(warehouse)
		.on(bin_table.warehouse == warehouse.name)
		.select(bin_table.item_code, bin_table.warehouse, bin_table.actual_qty)
	)
	query = _warehouse_conditions(query, warehouse, filters)
	if filters.get("item_code"):
		query = query.where(bin_table.item_code == filters.item_code)
	return {
		(row.item_code, row.warehouse): flt(row.actual_qty)
		for row in query.run(as_dict=True)
	}


def _get_maintaining_balance(filters):
	reorder = frappe.qb.DocType("Item Reorder")
	warehouse = frappe.qb.DocType("Warehouse")
	query = (
		frappe.qb.from_(reorder)
		.join(warehouse)
		.on(reorder.warehouse == warehouse.name)
		.select(reorder.parent.as_("item_code"), reorder.warehouse, reorder.warehouse_reorder_level)
	)
	query = _warehouse_conditions(query, warehouse, filters)
	if filters.get("item_code"):
		query = query.where(reorder.parent == filters.item_code)
	return {
		(row.item_code, row.warehouse): flt(row.warehouse_reorder_level)
		for row in query.run(as_dict=True)
	}


def _get_expected_incoming(filters):
	purchase_order = frappe.qb.DocType("Purchase Order")
	purchase_order_item = frappe.qb.DocType("Purchase Order Item")
	warehouse = frappe.qb.DocType("Warehouse")

	pending_stock_qty = (
		(purchase_order_item.qty - purchase_order_item.received_qty)
		* purchase_order_item.conversion_factor
	)
	query = (
		frappe.qb.from_(purchase_order_item)
		.join(purchase_order)
		.on(purchase_order_item.parent == purchase_order.name)
		.join(warehouse)
		.on(purchase_order_item.warehouse == warehouse.name)
		.select(
			purchase_order_item.item_code,
			purchase_order_item.warehouse,
			Sum(pending_stock_qty).as_("incoming_qty"),
		)
		.where(purchase_order.docstatus == 1)
		.where(purchase_order.status.notin(["Closed", "Delivered"]))
		.where(purchase_order_item.qty > purchase_order_item.received_qty)
		.where(Coalesce(purchase_order_item.delivered_by_supplier, 0) == 0)
		.groupby(purchase_order_item.item_code, purchase_order_item.warehouse)
	)
	query = _warehouse_conditions(query, warehouse, filters)
	if filters.get("item_code"):
		query = query.where(purchase_order_item.item_code == filters.item_code)
	return {
		(row.item_code, row.warehouse): flt(row.incoming_qty)
		for row in query.run(as_dict=True)
	}


def _get_item_map(item_codes):
	if not item_codes:
		return {}

	item = frappe.qb.DocType("Item")
	rows = (
		frappe.qb.from_(item)
		.select(item.name, item.item_name, item.description, item.item_group, item.stock_uom)
		.where(item.name.isin(list(item_codes)))
		.where(item.is_stock_item == 1)
		.where(item.disabled == 0)
		.run(as_dict=True)
	)
	return {row.name: row for row in rows}

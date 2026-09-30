# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import json
from pathlib import Path
from unittest import TestCase

from ccpcerp import setup
from ccpcerp.ccpc_erp.report.ccpc_item_stock_inquiry import ccpc_item_stock_inquiry


class TestCCPCItemStockInquiry(TestCase):
	def test_quantity_only_columns_and_wide_item_name(self):
		columns = {row["fieldname"]: row for row in ccpc_item_stock_inquiry.get_columns()}
		self.assertEqual(
			list(columns),
			[
				"item_code",
				"item_name",
				"warehouse",
				"stock_uom",
				"actual_qty",
				"maintaining_balance",
				"incoming_qty",
				"status",
			],
		)
		self.assertGreaterEqual(columns["item_name"]["width"], 300)
		self.assertEqual(columns["incoming_qty"]["label"], "Expected Incoming Qty")
		self.assertEqual(columns["maintaining_balance"]["label"], "Maintaining Balance")

	def test_status_accounts_for_open_incoming_stock(self):
		status = ccpc_item_stock_inquiry._stock_status
		self.assertEqual(status(15, 10, 0), "OK")
		self.assertEqual(status(0, 10, 20), "Incoming - No New Order Needed")
		self.assertEqual(status(3, 10, 5), "Reorder Needed")
		self.assertEqual(status(0, 0, 20), "No Maintaining Balance Set")

	def test_report_does_not_expose_money_fields(self):
		report_source = (
			Path(__file__).parents[0]
			/ "report/ccpc_item_stock_inquiry/ccpc_item_stock_inquiry.py"
		).read_text()
		for forbidden in (
			"valuation_rate",
			"stock_value",
			"price_list_rate",
			"last_purchase_rate",
			"net_amount",
			"base_amount",
		):
			self.assertNotIn(forbidden, report_source)

	def test_operational_users_cannot_extend_stock_inquiry_columns(self):
		report_js = (
			Path(__file__).parents[0]
			/ "report/ccpc_item_stock_inquiry/ccpc_item_stock_inquiry.js"
		).read_text()
		self.assertIn('frappe.user.has_role("System Manager")', report_js)
		for blocked_action in ("Edit", "Add Column", "Setup Auto Email", "User Permissions"):
			self.assertIn(blocked_action, report_js)

	def test_report_is_restricted_to_stock_inquiry_role(self):
		path = (
			Path(__file__).parents[0]
			/ "report/ccpc_item_stock_inquiry/ccpc_item_stock_inquiry.json"
		)
		report = json.loads(path.read_text())
		roles = {row["role"] for row in report["roles"]}
		self.assertIn(setup.STOCK_INQUIRY_ROLE, roles)
		self.assertIn("System Manager", roles)

	def test_workspace_has_stock_inquiry_shortcut(self):
		workspace = json.loads(
			(
				Path(__file__).parents[0]
				/ "workspace/ccpc_erp/ccpc_erp.json"
			).read_text()
		)
		shortcut = next(row for row in workspace["shortcuts"] if row["label"] == "Item Stock Inquiry")
		self.assertEqual(shortcut["type"], "Report")
		self.assertEqual(shortcut["link_to"], "CCPC Item Stock Inquiry")
		self.assertEqual(shortcut["report_ref_doctype"], "Item")

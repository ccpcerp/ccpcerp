# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

from pathlib import Path
from unittest import TestCase

import frappe

from ccpcerp import hooks
from ccpcerp.ccpc_erp.purchase_order_required_by import PurchaseOrderRequiredByMixin


class _CoreLikePurchaseOrder:
	def __init__(self, transaction_date, schedule_date):
		self.transaction_date = transaction_date
		self.schedule_date = schedule_date
		self.transaction_date_seen_by_core = "not-called"

	def validate_schedule_date(self):
		self.transaction_date_seen_by_core = self.transaction_date
		if not self.schedule_date:
			raise frappe.ValidationError("Required By is required")
		if self.transaction_date and self.schedule_date < self.transaction_date:
			raise frappe.ValidationError("Required By cannot be before Date")
		return "validated"


class _CCPCPurchaseOrder(PurchaseOrderRequiredByMixin, _CoreLikePurchaseOrder):
	pass


class TestPurchaseOrderRequiredBy(TestCase):
	def test_past_required_by_is_allowed_and_po_date_is_restored(self):
		doc = _CCPCPurchaseOrder("2026-09-25", "2026-09-24")

		self.assertEqual(doc.validate_schedule_date(), "validated")
		self.assertIsNone(doc.transaction_date_seen_by_core)
		self.assertEqual(doc.transaction_date, "2026-09-25")
		self.assertEqual(doc.schedule_date, "2026-09-24")

	def test_required_by_remains_required(self):
		doc = _CCPCPurchaseOrder("2026-09-25", None)

		with self.assertRaises(frappe.ValidationError):
			doc.validate_schedule_date()

		self.assertEqual(doc.transaction_date, "2026-09-25")

	def test_purchase_order_extension_and_client_script_are_hooked(self):
		self.assertEqual(
			hooks.extend_doctype_class["Purchase Order"],
			"ccpcerp.ccpc_erp.purchase_order_required_by.PurchaseOrderRequiredByMixin",
		)
		self.assertEqual(
			hooks.doctype_js["Purchase Order"],
			"public/js/purchase_order_required_by.js",
		)

	def test_client_script_removes_datepicker_minimum_and_warns_without_blocking(self):
		app_root = Path(__file__).parents[1]
		contents = (app_root / "public/js/purchase_order_required_by.js").read_text()

		self.assertIn("minDate: null", contents)
		self.assertIn("row.schedule_date < frm.doc.transaction_date", contents)
		self.assertIn("This is allowed", contents)

# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

from unittest import TestCase
from unittest.mock import patch

import frappe

from ccpcerp import setup
from ccpcerp.ccpc_erp import mrs_procurement


class TestMRSProcurement(TestCase):
	def test_purchasing_material_request_permissions(self):
		rights = setup.PURCHASING_PERSONNEL_PERMISSIONS["Material Request"]
		self.assertTrue({"read", "write", "create", "submit"}.issubset(rights))
		self.assertEqual(
			setup.MAINTENANCE_SUPERVISOR_PERMISSIONS["Material Request"],
			{"read", "print", "report"},
		)

	def test_purchasing_rfq_permissions(self):
		rights = setup.PURCHASING_PERSONNEL_PERMISSIONS["Request for Quotation"]
		self.assertTrue({"read", "write", "create", "submit"}.issubset(rights))

	def test_purchasing_supplier_quotation_permissions(self):
		rights = setup.PURCHASING_PERSONNEL_PERMISSIONS["Supplier Quotation"]
		self.assertTrue({"read", "write", "create", "submit"}.issubset(rights))

	def test_purchasing_purchase_order_permissions(self):
		personnel_rights = setup.PURCHASING_PERSONNEL_PERMISSIONS["Purchase Order"]
		supervisor_rights = setup.PURCHASING_SUPERVISOR_PERMISSIONS["Purchase Order"]
		self.assertTrue({"read", "write", "create"}.issubset(personnel_rights))
		self.assertNotIn("submit", personnel_rights)
		self.assertIn("submit", supervisor_rights)

	def test_stockroom_receiving_permissions(self):
		purchase_order_rights = setup.STOCKROOM_PERSONNEL_PERMISSIONS["Purchase Order"]
		purchase_receipt_rights = setup.STOCKROOM_PERSONNEL_PERMISSIONS["Purchase Receipt"]

		self.assertEqual(purchase_order_rights, {"read", "print", "report"})
		self.assertTrue(
			{"read", "write", "create", "submit", "print", "report"}.issubset(
				purchase_receipt_rights
			)
		)
		self.assertNotIn("cancel", purchase_receipt_rights)
		self.assertNotIn("amend", purchase_receipt_rights)
		self.assertNotIn("delete", purchase_receipt_rights)
		self.assertEqual(setup.STOCKROOM_PERSONNEL_PERMISSIONS["Buying Settings"], {"read"})
		self.assertNotIn("Purchase Invoice", setup.STOCKROOM_PERSONNEL_PERMISSIONS)
		self.assertNotIn("Payment Entry", setup.STOCKROOM_PERSONNEL_PERMISSIONS)

	def test_purchase_receipt_procurement_status(self):
		self.assertEqual(
			mrs_procurement._purchase_receipt_procurement_status([0, 2]),
			mrs_procurement.RECEIVING_IN_PROGRESS,
		)
		self.assertEqual(
			mrs_procurement._purchase_receipt_procurement_status([1], [50]),
			mrs_procurement.PARTIALLY_RECEIVED,
		)
		self.assertEqual(
			mrs_procurement._purchase_receipt_procurement_status([0, 1, 2], [100, 100]),
			mrs_procurement.FULLY_RECEIVED,
		)
		self.assertIsNone(mrs_procurement._purchase_receipt_procurement_status([2], [100]))
		self.assertIsNone(mrs_procurement._purchase_receipt_procurement_status([], []))

	@patch.object(frappe, "get_all")
	def test_purchase_receipt_resolves_matching_po_and_mr_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="PO-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-1", mrs="MRS-1")],
		]
		self.assertEqual(
			mrs_procurement._resolve_mrs_from_purchase_receipt_sources(
				{"PO-1"},
				{"MAT-MR-1"},
			),
			"MRS-1",
		)

	@patch.object(frappe, "get_all")
	def test_purchase_receipt_rejects_mixed_mrs_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="PO-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-2", mrs="MRS-2")],
		]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_purchase_receipt_sources(
				{"PO-1"},
				{"MAT-MR-2"},
			)

	@patch.object(frappe, "get_all")
	def test_purchase_receipt_rejects_unlinked_item_mixed_with_mrs(self, get_all):
		get_all.return_value = [frappe._dict(name="PO-1", mrs="MRS-1")]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_purchase_receipt_sources(
				{"PO-1"},
				set(),
				has_unlinked_items=True,
			)

	def test_purchasing_accounting_permissions(self):
		for doctype in ("Purchase Invoice", "Payment Entry"):
			with self.subTest(doctype=doctype):
				rights = setup.PURCHASING_PERSONNEL_PERMISSIONS[doctype]
				self.assertTrue({"read", "write", "create", "submit", "print", "report"}.issubset(rights))
				self.assertNotIn("cancel", rights)
				self.assertNotIn("amend", rights)

		self.assertEqual(setup.PURCHASING_PERSONNEL_PERMISSIONS["Mode of Payment"], {"read"})
		self.assertEqual(setup.PURCHASING_PERSONNEL_PERMISSIONS["Buying Settings"], {"read"})

	def test_material_request_procurement_status(self):
		self.assertEqual(
			mrs_procurement._material_request_procurement_status(0),
			mrs_procurement.MATERIAL_REQUEST_DRAFT,
		)
		self.assertEqual(
			mrs_procurement._material_request_procurement_status(1),
			mrs_procurement.MATERIAL_REQUEST_SUBMITTED,
		)
		self.assertEqual(
			mrs_procurement._material_request_procurement_status(2),
			mrs_procurement.MATERIAL_REQUEST_CANCELLED,
		)

	def test_request_for_quotation_procurement_status(self):
		self.assertEqual(
			mrs_procurement._request_for_quotation_procurement_status([0, 2]),
			mrs_procurement.REQUEST_FOR_QUOTATION_DRAFT,
		)
		self.assertEqual(
			mrs_procurement._request_for_quotation_procurement_status([0, 1, 2]),
			mrs_procurement.REQUEST_FOR_QUOTATION_SUBMITTED,
		)
		self.assertEqual(
			mrs_procurement._request_for_quotation_procurement_status([2]),
			mrs_procurement.REQUEST_FOR_QUOTATION_CANCELLED,
		)
		self.assertIsNone(mrs_procurement._request_for_quotation_procurement_status([]))

	@patch.object(frappe, "get_all")
	def test_rfq_resolves_single_mrs_provenance(self, get_all):
		get_all.return_value = [frappe._dict(name="MAT-MR-1", mrs="MRS-1")]
		self.assertEqual(
			mrs_procurement._resolve_mrs_from_material_requests({"MAT-MR-1"}),
			"MRS-1",
		)

	@patch.object(frappe, "get_all")
	def test_rfq_rejects_mixed_mrs_provenance(self, get_all):
		get_all.return_value = [
			frappe._dict(name="MAT-MR-1", mrs="MRS-1"),
			frappe._dict(name="MAT-MR-2", mrs="MRS-2"),
		]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_material_requests({"MAT-MR-1", "MAT-MR-2"})

	@patch.object(frappe, "get_all")
	def test_rfq_rejects_unlinked_item_mixed_with_mrs(self, get_all):
		get_all.return_value = [frappe._dict(name="MAT-MR-1", mrs="MRS-1")]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_material_requests(
				{"MAT-MR-1"},
				has_unlinked_items=True,
			)

	def test_supplier_quotation_procurement_status(self):
		self.assertEqual(
			mrs_procurement._supplier_quotation_procurement_status([0, 2]),
			mrs_procurement.SUPPLIER_QUOTATION_DRAFT,
		)
		self.assertEqual(
			mrs_procurement._supplier_quotation_procurement_status([0, 1, 2]),
			mrs_procurement.SUPPLIER_QUOTATION_RECEIVED,
		)
		self.assertIsNone(mrs_procurement._supplier_quotation_procurement_status([2]))
		self.assertIsNone(mrs_procurement._supplier_quotation_procurement_status([]))

	@patch.object(frappe, "get_all")
	def test_supplier_quotation_resolves_matching_rfq_and_mr_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="RFQ-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-1", mrs="MRS-1")],
		]
		self.assertEqual(
			mrs_procurement._resolve_mrs_from_procurement_sources(
				{"RFQ-1"},
				{"MAT-MR-1"},
			),
			"MRS-1",
		)

	@patch.object(frappe, "get_all")
	def test_supplier_quotation_rejects_mixed_mrs_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="RFQ-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-2", mrs="MRS-2")],
		]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_procurement_sources(
				{"RFQ-1"},
				{"MAT-MR-2"},
			)

	@patch.object(frappe, "get_all")
	def test_supplier_quotation_rejects_unlinked_item_mixed_with_mrs(self, get_all):
		get_all.return_value = [frappe._dict(name="RFQ-1", mrs="MRS-1")]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_procurement_sources(
				{"RFQ-1"},
				set(),
				has_unlinked_items=True,
			)

	def test_purchase_order_procurement_status(self):
		self.assertEqual(
			mrs_procurement._purchase_order_procurement_status([0, 2]),
			mrs_procurement.PURCHASE_ORDER_DRAFT,
		)
		self.assertEqual(
			mrs_procurement._purchase_order_procurement_status([0, 1, 2]),
			mrs_procurement.PURCHASE_ORDER_SUBMITTED,
		)
		self.assertIsNone(mrs_procurement._purchase_order_procurement_status([2]))
		self.assertIsNone(mrs_procurement._purchase_order_procurement_status([]))

	@patch.object(frappe, "get_all")
	def test_purchase_order_resolves_matching_sq_and_mr_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="SQ-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-1", mrs="MRS-1")],
		]
		self.assertEqual(
			mrs_procurement._resolve_mrs_from_purchase_order_sources(
				{"SQ-1"},
				{"MAT-MR-1"},
			),
			"MRS-1",
		)

	@patch.object(frappe, "get_all")
	def test_purchase_order_rejects_mixed_mrs_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="SQ-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-2", mrs="MRS-2")],
		]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_purchase_order_sources(
				{"SQ-1"},
				{"MAT-MR-2"},
			)

	@patch.object(frappe, "get_all")
	def test_purchase_order_rejects_unlinked_item_mixed_with_mrs(self, get_all):
		get_all.return_value = [frappe._dict(name="SQ-1", mrs="MRS-1")]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_purchase_order_sources(
				{"SQ-1"},
				set(),
				has_unlinked_items=True,
			)

	def test_purchase_receipt_email_content_targets_requestor_context(self):
		doc = frappe._dict(
			doctype="Purchase Receipt",
			name="MAT-PRE-1",
			mrs="MRS-1",
			supplier="SUP-1",
			supplier_name="Supplier One",
			items=[
				frappe._dict(
					item_code="ITEM-1",
					item_name="Bearing",
					qty=2,
					rejected_qty=1,
					uom="Nos",
					warehouse="Warehouse 1",
				)
			],
		)
		mrs = frappe._dict(prepared_by="requestor@example.com", prepared_by_name="Requestor Name")
		subject, message = mrs_procurement._build_purchase_receipt_requestor_email(
			doc,
			mrs,
			mrs_procurement.PARTIALLY_RECEIVED,
		)
		self.assertIn("MRS-1", subject)
		self.assertIn("Requestor Name", message)
		self.assertIn("Partially Received", message)
		self.assertIn("Bearing", message)
		self.assertIn("rejected 1 Nos", message)

	@patch.object(frappe, "get_roles", return_value=["Purchasing Personnel"])
	def test_purchasing_personnel_is_allowed(self, _get_roles):
		mrs_procurement._require_purchasing_role("purchasing@example.com")

	@patch.object(frappe, "get_roles", return_value=["Employee"])
	def test_non_purchasing_user_is_rejected(self, _get_roles):
		with self.assertRaises(frappe.PermissionError):
			mrs_procurement._require_purchasing_role("employee@example.com")

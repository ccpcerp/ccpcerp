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

	@patch.object(frappe, "get_roles", return_value=["Purchasing Personnel"])
	def test_purchasing_personnel_is_allowed(self, _get_roles):
		mrs_procurement._require_purchasing_role("purchasing@example.com")

	@patch.object(frappe, "get_roles", return_value=["Employee"])
	def test_non_purchasing_user_is_rejected(self, _get_roles):
		with self.assertRaises(frappe.PermissionError):
			mrs_procurement._require_purchasing_role("employee@example.com")

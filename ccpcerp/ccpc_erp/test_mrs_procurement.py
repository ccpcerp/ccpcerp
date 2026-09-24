# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

from unittest import TestCase
from unittest.mock import patch

import frappe

from ccpcerp.ccpc_erp import mrs_procurement


class TestMRSProcurement(TestCase):
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

	@patch.object(frappe, "get_roles", return_value=["Purchasing Personnel"])
	def test_purchasing_personnel_is_allowed(self, _get_roles):
		with patch.object(frappe.session, "user", "purchasing@example.com"):
			mrs_procurement._require_purchasing_role()

	@patch.object(frappe, "get_roles", return_value=["Employee"])
	def test_non_purchasing_user_is_rejected(self, _get_roles):
		with patch.object(frappe.session, "user", "employee@example.com"):
			with self.assertRaises(frappe.PermissionError):
				mrs_procurement._require_purchasing_role()

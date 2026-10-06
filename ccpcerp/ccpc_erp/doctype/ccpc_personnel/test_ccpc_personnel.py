# Copyright (c) 2026, CCPC and contributors
# See license.txt

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp.ccpc_erp.doctype.ccpc_personnel.ccpc_personnel import (
	CCPCPersonnel,
	validate_department_manager_company,
)
from ccpcerp.setup import backfill_ccpc_personnel_company


class TestCCPCPersonnel(TestCase):
	@patch("ccpcerp.setup.frappe.db.set_value")
	@patch("ccpcerp.setup.frappe.db.get_value", return_value="Ricor Mills Corporation")
	@patch(
		"ccpcerp.setup.frappe.get_all",
		return_value=[frappe._dict(name="pam1ogn2u3", department="Glucose Plant - RMC")],
	)
	@patch("ccpcerp.setup.frappe.db.table_exists", return_value=True)
	def test_backfill_company_from_home_department(
		self, table_exists, get_all, get_value, set_value
	):
		backfill_ccpc_personnel_company()
		get_all.assert_called_once_with(
			"CCPC Personnel",
			filters={"company": ["is", "not set"], "department": ["is", "set"]},
			fields=["name", "department"],
		)
		set_value.assert_called_once_with(
			"CCPC Personnel",
			"pam1ogn2u3",
			"company",
			"Ricor Mills Corporation",
			update_modified=False,
		)

	@patch(
		"ccpcerp.ccpc_erp.doctype.ccpc_personnel.ccpc_personnel.frappe.get_cached_value",
		return_value="Ricor Mills Corporation",
	)
	def test_company_is_derived_from_home_department(self, get_cached_value):
		doc = MagicMock(spec=CCPCPersonnel)
		doc.department = "Glucose Plant - RMC"
		CCPCPersonnel._set_company_from_department(doc)
		self.assertEqual(doc.company, "Ricor Mills Corporation")

	@patch(
		"ccpcerp.ccpc_erp.doctype.ccpc_personnel.ccpc_personnel.frappe.db.get_value"
	)
	def test_manager_can_manage_multiple_departments_in_same_company(self, get_value):
		get_value.return_value = frappe._dict(
			personnel_name="Jack Melad",
			company="Ricor Mills Corporation",
			active=1,
		)
		doc = frappe._dict(
			company="Ricor Mills Corporation",
			ccpc_department_manager="pam1ogn2u3",
		)
		validate_department_manager_company(doc)

	@patch(
		"ccpcerp.ccpc_erp.doctype.ccpc_personnel.ccpc_personnel._",
		side_effect=lambda value: value,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.ccpc_personnel.ccpc_personnel.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.ccpc_personnel.ccpc_personnel.frappe.db.get_value"
	)
	def test_manager_cannot_be_assigned_across_companies(self, get_value, throw, translate):
		get_value.return_value = frappe._dict(
			personnel_name="Jack Melad",
			company="Ricor Mills Corporation",
			active=1,
		)
		doc = frappe._dict(
			company="Cagayan Corn Products Corporation",
			ccpc_department_manager="pam1ogn2u3",
		)
		with self.assertRaises(frappe.ValidationError):
			validate_department_manager_company(doc)

# Copyright (c) 2026, CCPC and Contributors
# See license.txt

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp.setup import migrate_equipment_area_to_section

from ccpcerp.ccpc_erp.doctype.equipment_registry.equipment_registry import (
	EquipmentRegistry,
	has_protected_equipment_history,
	has_submitted_equipment_use,
	validate_equipment_section,
)


class TestEquipmentRegistryDepartmentEligibility(TestCase):
	@patch("ccpcerp.ccpc_erp.doctype.equipment_registry.equipment_registry.validate_department_company")
	@patch("ccpcerp.ccpc_erp.doctype.equipment_registry.equipment_registry.has_protected_equipment_history", return_value=True)
	def test_company_change_is_blocked_when_assignment_exists(self, history, validate_department):
		doc = MagicMock(spec=EquipmentRegistry)
		doc.is_new.return_value = False
		doc.name = "EQ-1"
		doc.equipment_number = "EQ-1"
		doc.company = "Company B"
		doc.department = "Department B"
		doc._requires_eligible_department.return_value = True
		doc.get_doc_before_save.return_value = frappe._dict(company="Company A")
		with self.assertRaises(frappe.ValidationError):
			EquipmentRegistry.validate(doc)

	@patch("ccpcerp.ccpc_erp.doctype.equipment_registry.equipment_registry.has_protected_equipment_history", return_value=True)
	def test_rename_is_blocked_when_assignment_exists(self, history):
		doc = MagicMock(spec=EquipmentRegistry)
		with self.assertRaises(frappe.ValidationError):
			EquipmentRegistry.before_rename(doc, "EQ-1", "EQ-2")

	@patch("ccpcerp.ccpc_erp.doctype.equipment_registry.equipment_registry.has_submitted_equipment_use", return_value=True)
	@patch("ccpcerp.ccpc_erp.doctype.equipment_registry.equipment_registry.frappe.db.exists", return_value=None)
	def test_phase1_history_still_counts_as_protected_history(self, exists, submitted_use):
		self.assertTrue(has_protected_equipment_history("EQ-1"))
		submitted_use.assert_called_once_with("EQ-1")

	@patch("ccpcerp.ccpc_erp.doctype.equipment_registry.equipment_registry._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.equipment_registry.equipment_registry.validate_department_company")
	def test_validation_requests_full_eligibility_for_new_equipment(self, validate_department, translate):
		doc = MagicMock(spec=EquipmentRegistry)
		doc.is_new.return_value = True
		doc.name = "EQ-1"
		doc.equipment_number = "EQ-1"
		doc.company = "Cagayan Corn Products Corporation"
		doc.department = "Wet Mill Plant - CCPC"
		doc._requires_eligible_department.return_value = True
		doc.get_doc_before_save.return_value = None

		EquipmentRegistry.validate(doc)

		validate_department.assert_called_once_with(
			doc.department, doc.company, "Equipment Registry", require_eligible=True
		)

	def test_new_reassigned_and_reactivated_equipment_require_eligible_department(self):
		cases = (
			(True, None, "Active", "Wet Mill Plant - CCPC", True),
			(False, "Other Department", "Active", "Wet Mill Plant - CCPC", True),
			(False, "Wet Mill Plant - CCPC", "Inactive", "Wet Mill Plant - CCPC", True),
			(False, "Wet Mill Plant - CCPC", "Active", "Wet Mill Plant - CCPC", False),
		)
		for is_new, old_department, old_status, department, expected in cases:
			with self.subTest(is_new=is_new, old_department=old_department, old_status=old_status):
				doc = MagicMock(spec=EquipmentRegistry)
				doc.is_new.return_value = is_new
				doc.company = "Cagayan Corn Products Corporation"
				doc.department = department
				doc.status = "Active"
				doc.get_doc_before_save.return_value = (
					None
					if is_new
					else frappe._dict(
						company=doc.company, department=old_department, status=old_status
					)
				)
				self.assertEqual(
					EquipmentRegistry._requires_eligible_department(doc), expected
				)

	def test_unchanged_historical_equipment_does_not_require_new_use_eligibility(self):
		doc = MagicMock(spec=EquipmentRegistry)
		doc.is_new.return_value = False
		doc.company = "Cagayan Corn Products Corporation"
		doc.department = "Wet Mill Plant - CCPC"
		doc.status = "Inactive"
		doc.get_doc_before_save.return_value = frappe._dict(
			company=doc.company, department=doc.department, status=doc.status
		)
		self.assertFalse(EquipmentRegistry._requires_eligible_department(doc))



class TestEquipmentRegistryAdditionalRules(TestCase):

	@patch("frappe.get_meta")
	@patch("frappe.db.exists")
	def test_submitted_child_use_locks_rename(self, exists, get_meta):
		get_meta.return_value.has_field.return_value = True
		exists.side_effect = lambda doctype, filters: doctype == "Stock Entry Detail"
		self.assertTrue(has_submitted_equipment_use("WP-401A"))

	@patch("frappe.get_meta")
	@patch("frappe.db.exists", return_value=False)
	def test_unused_equipment_can_be_renamed(self, exists, get_meta):
		get_meta.return_value.has_field.return_value = True
		self.assertFalse(has_submitted_equipment_use("WP-401A"))


class TestEquipmentRegistrySection(TestCase):
	@patch("ccpcerp.ccpc_erp.doctype.equipment_registry.equipment_registry.frappe.db.get_value")
	def test_section_must_match_department_and_company(self, get_value):
		get_value.return_value = frappe._dict(
			department="Glucose Plant - RMC", company="Ricor Mills Corporation", disabled=0
		)
		validate_equipment_section(
			"Filtration Section - Glucose Plant - RMC",
			"Glucose Plant - RMC",
			"Ricor Mills Corporation",
		)

	@patch("ccpcerp.ccpc_erp.doctype.equipment_registry.equipment_registry.frappe.db.get_value")
	def test_section_from_other_department_is_rejected(self, get_value):
		get_value.return_value = frappe._dict(
			department="Wet Mill Plant - CCPC", company="Cagayan Corn Products Corporation", disabled=0
		)
		with self.assertRaises(frappe.ValidationError):
			validate_equipment_section(
				"Refining Section - Wet Mill Plant - CCPC",
				"Glucose Plant - RMC",
				"Ricor Mills Corporation",
			)


class TestEquipmentAreaMigration(TestCase):
	@patch("ccpcerp.setup.frappe.db.set_value")
	@patch("ccpcerp.setup.frappe.db.get_value", return_value="Refining Section - Wet Mill Plant - CCPC")
	@patch("ccpcerp.setup.frappe.db.sql")
	@patch("ccpcerp.setup.frappe.db.has_column", return_value=True)
	@patch("ccpcerp.setup.frappe.get_meta")
	@patch("ccpcerp.setup.frappe.db.table_exists", return_value=True)
	def test_legacy_area_backfills_section_after_area_leaves_doctype_meta(
		self, table_exists, get_meta, has_column, sql, get_value, set_value
	):
		get_meta.return_value.has_field.side_effect = lambda fieldname: fieldname == "section"
		sql.return_value = [
			frappe._dict(
				name="WPS-401",
				company="Cagayan Corn Products Corporation",
				department="Wet Mill Plant - CCPC",
				area="Refining Section",
			)
		]

		migrate_equipment_area_to_section()

		has_column.assert_called_once_with("Equipment Registry", "area")
		set_value.assert_called_once_with(
			"Equipment Registry",
			"WPS-401",
			"section",
			"Refining Section - Wet Mill Plant - CCPC",
			update_modified=False,
		)

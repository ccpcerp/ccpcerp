# Copyright (c) 2026, CCPC and Contributors
# See license.txt

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp.ccpc_erp.doctype.equipment_registry.equipment_registry import (
	EquipmentRegistry,
	has_protected_equipment_history,
	has_submitted_equipment_use,
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

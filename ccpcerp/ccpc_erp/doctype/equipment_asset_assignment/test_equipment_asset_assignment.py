# Copyright (c) 2026, CCPC and Contributors
# See license.txt

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment import (
	EquipmentAssetAssignment,
)


def assignment(**values):
	defaults = {
		"name": None,
		"equipment": "EQ-1",
		"asset": "ASSET-1",
		"company": "Company A",
		"installed_on": "2026-09-01 08:00:00",
		"removed_on": None,
		"removal_reason": None,
	}
	defaults.update(values)
	doc = MagicMock(spec=EquipmentAssetAssignment)
	for fieldname, value in defaults.items():
		setattr(doc, fieldname, value)
	doc.get.side_effect = lambda fieldname: getattr(doc, fieldname)
	doc.meta.get_label.side_effect = lambda fieldname: fieldname
	return doc


class TestEquipmentAssetAssignment(TestCase):
	@staticmethod
	def query_deadlock(errno):
		database_error = Exception(errno, "database conflict")
		error = frappe.QueryDeadlockError(database_error)
		error.__cause__ = database_error
		return error

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.get_values")
	def test_checkread_during_new_overlap_read_becomes_retry_validation_error(self, get_values):
		get_values.side_effect = self.query_deadlock(1020)
		with (
			patch(
				"ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment._",
				side_effect=lambda value: value,
			),
			self.assertRaisesRegex(
				frappe.ValidationError,
				"changed concurrently.*not saved.*retry",
			),
		):
			EquipmentAssetAssignment.validate_overlap(assignment(), for_update=True)
		get_values.assert_called_once()
		self.assertTrue(get_values.call_args.kwargs["for_update"])

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.get_values")
	def test_non_checkread_deadlock_is_not_converted(self, get_values):
		error = self.query_deadlock(1213)
		get_values.side_effect = error
		with self.assertRaises(frappe.QueryDeadlockError) as raised:
			EquipmentAssetAssignment.validate_overlap(assignment(), for_update=True)
		self.assertIs(raised.exception, error)

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.get_values")
	def test_saved_nonlocking_checkread_is_not_converted(self, get_values):
		error = self.query_deadlock(1020)
		get_values.side_effect = error
		with self.assertRaises(frappe.QueryDeadlockError) as raised:
			EquipmentAssetAssignment.validate_overlap(assignment(), for_update=False)
		self.assertIs(raised.exception, error)

	def test_new_validation_requests_locking_overlap_read(self):
		doc = assignment()
		doc.is_new.return_value = True
		EquipmentAssetAssignment.validate(doc)
		doc.lock_assignment_resources.assert_called_once_with()
		doc.validate_overlap.assert_called_once_with(for_update=True)

	def test_saved_validation_keeps_nonlocking_overlap_read(self):
		doc = assignment()
		doc.is_new.return_value = False
		EquipmentAssetAssignment.validate(doc)
		doc.lock_assignment_resources.assert_not_called()
		doc.validate_overlap.assert_called_once_with(for_update=False)

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.get_value")
	def test_new_assignment_locks_equipment_then_asset(self, get_value):
		doc = assignment()
		EquipmentAssetAssignment.lock_assignment_resources(doc)
		self.assertEqual(
			get_value.call_args_list,
			[
				(("Equipment Registry", "EQ-1", "name"), {"for_update": True}),
				(("Asset", "ASSET-1", "name"), {"for_update": True}),
			],
		)

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.get_value")
	def test_company_is_derived_from_equipment_on_new_assignment(self, get_value):
		get_value.return_value = "Company A"
		doc = assignment(company="Forced Company")
		doc.is_new.return_value = True
		EquipmentAssetAssignment.before_validate(doc)
		self.assertEqual(doc.company, "Company A")

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.get_value")
	def test_equipment_must_exist_and_be_active(self, get_value):
		doc = assignment()
		for equipment, allowed in (
			(None, False),
			(frappe._dict(company="Company A", status="Inactive"), False),
			(frappe._dict(company="Company A", status="Decommissioned"), False),
			(frappe._dict(company="Company A", status="Active"), True),
		):
			with self.subTest(equipment=equipment):
				get_value.return_value = equipment
				if allowed:
					EquipmentAssetAssignment.validate_equipment(doc)
				else:
					with self.assertRaises(frappe.ValidationError):
						EquipmentAssetAssignment.validate_equipment(doc)

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.get_value")
	def test_asset_submission_company_and_status_eligibility(self, get_value):
		doc = assignment()
		cases = (
			(None, False),
			(frappe._dict(company="Company A", docstatus=0, status="Draft"), False),
			(frappe._dict(company="Company A", docstatus=2, status="Cancelled"), False),
			(frappe._dict(company="Company B", docstatus=1, status="Submitted"), False),
			(frappe._dict(company="Company A", docstatus=1, status="Sold"), False),
			(frappe._dict(company="Company A", docstatus=1, status="Scrapped"), False),
			(frappe._dict(company="Company A", docstatus=1, status="Capitalized"), False),
			(frappe._dict(company="Company A", docstatus=1, status="Submitted"), True),
			(frappe._dict(company="Company A", docstatus=1, status="Partially Depreciated"), True),
			(frappe._dict(company="Company A", docstatus=1, status="Fully Depreciated"), True),
			(frappe._dict(company="Company A", docstatus=1, status="In Maintenance"), True),
			(frappe._dict(company="Company A", docstatus=1, status="Out of Order"), True),
			(frappe._dict(company="Company A", docstatus=1, status="Issue"), True),
			(frappe._dict(company="Company A", docstatus=1, status="Receipt"), True),
		)
		for asset, allowed in cases:
			with self.subTest(asset=asset):
				get_value.return_value = asset
				if allowed:
					EquipmentAssetAssignment.validate_asset(doc)
				else:
					with self.assertRaises(frappe.ValidationError):
						EquipmentAssetAssignment.validate_asset(doc)

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.now_datetime")
	def test_date_and_closure_rules(self, now):
		now.return_value = frappe.utils.get_datetime("2026-09-14 12:00:00")
		for values, allowed in (
			({"installed_on": "2026-09-14 12:00:01"}, False),
			({"removed_on": "2026-09-01 08:00:00", "removal_reason": "Replaced"}, False),
			({"removed_on": "2026-09-01 07:59:59", "removal_reason": "Replaced"}, False),
			({"removed_on": "2026-09-02 08:00:00"}, False),
			({"removed_on": "2026-09-02 08:00:00", "removal_reason": "Replaced"}, True),
		):
			with self.subTest(values=values):
				doc = assignment(**values)
				if allowed:
					EquipmentAssetAssignment.validate_dates(doc)
				else:
					with self.assertRaises(frappe.ValidationError):
						EquipmentAssetAssignment.validate_dates(doc)

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.get_values")
	def test_equipment_and_asset_overlap_is_rejected(self, get_values):
		get_values.return_value = [
			frappe._dict(name="EAA-00001", installed_on="2026-08-01", removed_on=None)
		]
		with (
			patch(
				"ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment._",
				side_effect=lambda value: value,
			),
			self.assertRaisesRegex(
				frappe.ValidationError,
				"Equipment assignment overlaps with <strong>EAA-00001</strong>\\.",
			),
		):
			EquipmentAssetAssignment.validate_overlap(
				assignment(name="EAA-00002"), for_update=True
			)
		self.assertEqual(get_values.call_args.args[0], "Equipment Asset Assignment")
		self.assertTrue(get_values.call_args.kwargs["for_update"])

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.get_values")
	def test_asset_overlap_is_rejected_when_equipment_is_available(self, get_values):
		get_values.side_effect = lambda doctype, **kwargs: (
			[frappe._dict(name="EAA-00001", installed_on="2026-08-01", removed_on=None)]
			if doctype == "Equipment Asset Assignment" and "asset" in kwargs["filters"]
			else []
		)
		with (
			patch(
				"ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment._",
				side_effect=lambda value: value,
			),
			self.assertRaises(frappe.ValidationError),
		):
			EquipmentAssetAssignment.validate_overlap(
				assignment(name="EAA-00002"), for_update=True
			)
		assignment_queries = [
			call for call in get_values.call_args_list if call.args[0] == "Equipment Asset Assignment"
		]
		self.assertEqual(len(assignment_queries), 2)
		self.assertTrue(
			any(call.kwargs["filters"].get("asset") == "ASSET-1" for call in assignment_queries),
			assignment_queries,
		)
		self.assertTrue(all(call.kwargs["for_update"] for call in assignment_queries))

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.get_values")
	def test_exact_boundary_replacement_is_allowed_for_equipment_and_asset(self, get_values):
		get_values.return_value = [
			frappe._dict(
				name="EAA-00001", installed_on="2026-08-01", removed_on="2026-09-01 08:00:00"
			)
		]
		with patch(
			"ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment._",
			side_effect=lambda value: value,
		):
			EquipmentAssetAssignment.validate_overlap(assignment(name="EAA-00002"), for_update=True)
		self.assertEqual(get_values.call_count, 2)

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.get_values")
	def test_nonoverlapping_historical_intervals_are_allowed(self, get_values):
		get_values.return_value = [
			frappe._dict(
				name="EAA-00001", installed_on="2026-08-01", removed_on="2026-08-15"
			)
		]
		with patch(
			"ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment._",
			side_effect=lambda value: value,
		):
			EquipmentAssetAssignment.validate_overlap(
				assignment(name="EAA-00002", installed_on="2026-09-01", removed_on="2026-09-15"),
				for_update=True,
			)
		self.assertEqual(get_values.call_count, 2)

	def test_identity_and_installed_time_are_immutable(self):
		for fieldname in ("equipment", "asset", "company", "installed_on"):
			with self.subTest(fieldname=fieldname):
				doc = assignment()
				doc.is_new.return_value = False
				before = frappe._dict({name: getattr(doc, name) for name in ("equipment", "asset", "company", "installed_on", "removed_on")})
				doc.get_doc_before_save.return_value = before
				setattr(doc, fieldname, "changed")
				with self.assertRaises(frappe.ValidationError):
					EquipmentAssetAssignment.validate_immutability(doc)

	def test_closing_once_is_allowed_but_closed_assignment_cannot_change_or_reopen(self):
		doc = assignment(removed_on="2026-09-02")
		doc.is_new.return_value = False
		doc.get_doc_before_save.return_value = frappe._dict(
			equipment=doc.equipment, asset=doc.asset, company=doc.company,
			installed_on=doc.installed_on, removed_on=None,
		)
		EquipmentAssetAssignment.validate_immutability(doc)

		for changed in (None, "2026-09-03"):
			doc.removed_on = changed
			doc.get_doc_before_save.return_value.removed_on = "2026-09-02"
			with self.assertRaises(frappe.ValidationError):
				EquipmentAssetAssignment.validate_immutability(doc)

	@patch("ccpcerp.ccpc_erp.doctype.equipment_asset_assignment.equipment_asset_assignment.frappe.db.exists")
	def test_deletion_rules(self, exists):
		with self.assertRaises(frappe.ValidationError):
			EquipmentAssetAssignment.on_trash(assignment(removed_on="2026-09-02"))

		exists.side_effect = lambda doctype, filters: "WR-01" if doctype == "Work Request" else None
		with self.assertRaises(frappe.ValidationError):
			EquipmentAssetAssignment.on_trash(assignment(name="EAA-00001"))

		exists.return_value = None
		exists.side_effect = None
		EquipmentAssetAssignment.on_trash(assignment(name="EAA-00002"))

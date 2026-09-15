# Copyright (c) 2026, CCPC and Contributors
# See license.txt

from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from frappe.model.mapper import map_doc
from frappe.tests import IntegrationTestCase

from ccpcerp.ccpc_erp.doctype.work_request.work_request import (
	MATERIAL_REQUEST_ALLOWED_STATUSES,
	WorkRequest,
	make_job_order,
	make_material_request,
	validate_job_order_creation_eligibility,
	validate_material_request_eligibility,
)


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class TestWorkRequestMapper(TestCase):
	@patch("ccpcerp.ccpc_erp.equipment_asset.get_current_equipment_asset")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.set_department_from_equipment")
	def test_new_work_request_snapshots_only_equipment_context(self, set_department, current):
		current.return_value = ("EAA-00001", "ASSET-1")
		for request_type, equipment, expected in (
			("Equipment", "EQ-1", True),
			("Equipment", None, False),
			("Facility", None, False),
			("Furniture", None, False),
			("Vehicle", None, False),
			("Other", None, False),
		):
			doc = Mock(spec=WorkRequest, request_type=request_type, equipment=equipment)
			doc.is_new.return_value = True
			WorkRequest.before_validate(doc)
			self.assertEqual(
				(doc.equipment_asset_assignment, doc.asset),
				("EAA-00001", "ASSET-1") if expected else (None, None),
			)
		current.assert_called_once_with("EQ-1")

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.throw", side_effect=frappe.ValidationError)
	def test_new_work_request_requires_priority_for_every_request_type(self, throw, translate):
		for request_type in ("Equipment", "Facility", "Furniture", "Vehicle", "Other"):
			with self.subTest(request_type=request_type):
				for priority in ("1", "2", "3", "4"):
					doc = Mock(spec=WorkRequest, priority=priority, request_type=request_type)
					doc.is_new.return_value = True
					WorkRequest.validate_priority(doc)
				doc = Mock(spec=WorkRequest, priority=None, request_type=request_type)
				doc.is_new.return_value = True
				with self.assertRaises(frappe.ValidationError):
					WorkRequest.validate_priority(doc)

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.throw", side_effect=frappe.ValidationError)
	def test_existing_work_request_priority_is_grandfathered_but_cannot_be_cleared(
		self, throw, translate
	):
		for previous, current, allowed in (
			(None, None, True),
			(None, "2", True),
			("1", None, False),
			("1", "4", True),
		):
			with self.subTest(previous=previous, current=current):
				doc = Mock(spec=WorkRequest, priority=current)
				doc.is_new.return_value = False
				doc.get_doc_before_save.return_value = frappe._dict(priority=previous)
				if allowed:
					WorkRequest.validate_priority(doc)
				else:
					with self.assertRaises(frappe.ValidationError):
						WorkRequest.validate_priority(doc)

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.get_mapped_doc")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_job_order_creation_eligibility")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.get_doc")
	def test_job_order_maps_only_approved_context_and_remains_unsaved(
		self, get_doc, validate_eligibility, get_mapped_doc
	):
		source_values = {
			"name": "WR-04",
			"company": "Cagayan Corn Products Corporation",
			"equipment": "EQ-1",
			"equipment_description": None,
			"asset_location": "Boiler House",
			"department": "Wet Mill Plant - CCPC",
			"maintenance_section": "Civil",
			"problem_description": "Repair piping support",
			"priority": "2",
			"requested_by": "Administrator",
			"status": "Open",
			"naming_series": "WR-.##",
			"job_order": None,
			"request_type": "Equipment",
			"date_reported": "2026-09-14 08:00:00",
			"equipment_asset_assignment": "EAA-00001",
			"asset": "ASSET-1",
		}
		source = Mock(doctype="Work Request", idx=0)
		source.name = source_values["name"]
		for fieldname, value in source_values.items():
			setattr(source, fieldname, value)
		source.get.side_effect = source_values.get
		source.meta.get.return_value = []
		source.check_permission = Mock()

		target_values = {
			"work_request": None,
			"company": None,
			"equipment": None,
			"equipment_description": None,
			"asset_location": None,
			"department": None,
			"maintenance_section": None,
			"problem_description": None,
			"priority": None,
			"requested_by": None,
			"status": None,
			"naming_series": None,
			"job_order": None,
			"equipment_asset_assignment": None,
			"asset": None,
		}
		target = Mock(doctype="Job Order", idx=0)
		target.is_new.return_value = True
		target.get.side_effect = target_values.get
		target.set.side_effect = target_values.__setitem__
		target.meta.get.side_effect = lambda fieldname, filters=None: (
			[frappe._dict(fieldname=name, fieldtype="Data") for name in target_values]
			if filters is None
			else []
		)
		get_doc.return_value = source

		def apply_mapping(from_doctype, source_name, table_maps, target_doc):
			map_doc(source, target, table_maps[from_doctype])
			return target

		get_mapped_doc.side_effect = apply_mapping
		before = dict(source_values)
		result = make_job_order("WR-04")

		self.assertIs(result, target)
		self.assertTrue(result.is_new())
		for fieldname in (
			"company",
			"equipment",
			"equipment_description",
			"asset_location",
			"department",
			"maintenance_section",
			"problem_description",
			"priority",
			"requested_by",
			"equipment_asset_assignment",
			"asset",
		):
			self.assertEqual(target_values[fieldname], source_values[fieldname])
		self.assertEqual(target_values["work_request"], "WR-04")
		self.assertIsNone(target_values["status"])
		self.assertIsNone(target_values["naming_series"])
		self.assertIsNone(target_values["job_order"])
		self.assertEqual(source_values, before)
		source.check_permission.assert_called_once_with("read")
		source.save.assert_not_called()
		target.save.assert_not_called()
		target.insert.assert_not_called()
		target.submit.assert_not_called()

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_department_company")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_equipment")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.get_cached_value", return_value="Cagayan Corn Products Corporation")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_new_job_order_relationship")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request._", side_effect=lambda value: value)
	def test_facility_work_request_with_blank_equipment_is_eligible_for_job_order(
		self, translate, validate_relationship, get_cached_value, validate_equipment, validate_department
	):
		doc = frappe._dict(
			name="WR-04",
			status="Open",
			job_order=None,
			company="Cagayan Corn Products Corporation",
			request_type="Facility",
			equipment=None,
			department="Wet Mill Plant - CCPC",
		)
		validate_job_order_creation_eligibility(doc)
		validate_equipment.assert_called_once_with(
			None,
			doc.company,
			require_active=True,
			context="Work Request",
		)
		validate_department.assert_called_once_with(
			doc.department, doc.company, "Work Request", require_eligible=True
		)

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.throw", side_effect=frappe.ValidationError)
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.get_cached_value", return_value="Cagayan Corn Products Corporation")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_new_job_order_relationship")
	def test_equipment_work_request_without_equipment_is_ineligible_for_job_order(
		self, validate_relationship, get_cached_value, throw, translate
	):
		with self.assertRaises(frappe.ValidationError):
			validate_job_order_creation_eligibility(
				frappe._dict(
					name="WR-08",
					status="Open",
					job_order=None,
					company="Cagayan Corn Products Corporation",
					request_type="Equipment",
					equipment=None,
					department="Wet Mill Plant - CCPC",
				)
			)

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_job_order_creation_eligibility")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.get_doc")
	def test_direct_job_order_mapper_call_cannot_bypass_policy(
		self, get_doc, validate_eligibility
	):
		source = Mock()
		source.check_permission = Mock()
		get_doc.return_value = source
		validate_eligibility.side_effect = frappe.ValidationError("blocked")
		with self.assertRaises(frappe.ValidationError):
			make_job_order("WR-04")

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.set_department_from_equipment")
	def test_work_request_before_validate_uses_shared_equipment_defaulting(self, set_department):
		doc = Mock(spec=WorkRequest)
		doc.is_new.return_value = True
		WorkRequest.before_validate(doc)
		set_department.assert_called_once_with(doc)

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_department_company")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_equipment")
	def test_validation_requests_full_eligibility_for_new_work_request(
		self, validate_equipment, validate_department, translate
	):
		doc = Mock(spec=WorkRequest)
		doc.request_type = "Facility"
		doc.equipment = None
		doc.company = "Cagayan Corn Products Corporation"
		doc.department = "Wet Mill Plant - CCPC"
		doc._requires_active_equipment.return_value = False
		doc._requires_eligible_department.return_value = True

		WorkRequest.validate(doc)

		validate_department.assert_called_once_with(
			doc.department, doc.company, "Work Request", require_eligible=True
		)

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_department_company")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_equipment")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.get_mapped_doc")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.get_doc")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.get_cached_value", return_value="Cagayan Corn Products Corporation")
	def test_material_request_preserves_draft_status_and_maps_only_approved_context(
		self, get_cached_value, get_doc, get_mapped_doc, validate_equipment, validate_department
	):
		source = Mock(
			doctype="Work Request",
			name="WR-03",
			request_type="Equipment",
			equipment="WD-401",
			company="Cagayan Corn Products Corporation",
			department="Wet Mill Plant - CCPC",
			status="Open",
			naming_series="WR-.##",
			idx=0,
		)
		source.name = "WR-03"
		source.check_permission = Mock()
		source.validate_derived_job_order = Mock()
		target = Mock(doctype="Material Request", idx=0)
		values = {
			"status": "Draft",
			"naming_series": "MAT-MR-.YYYY.-",
			"company": None,
			"equipment": None,
			"department": None,
			"work_request": None,
			"material_request_type": None,
			"items": [],
		}
		source_values = {
			"status": source.status,
			"naming_series": source.naming_series,
			"company": source.company,
			"equipment": source.equipment,
			"department": source.department,
			"name": source.name,
		}
		source.get.side_effect = source_values.get
		target.get.side_effect = values.get
		target.set.side_effect = values.__setitem__
		source.meta.get.return_value = []
		target.meta.get.side_effect = lambda fieldname, filters=None: (
			[frappe._dict(fieldname=name, fieldtype="Data") for name in values if name != "items"]
			if filters is None
			else []
		)
		get_doc.return_value = source

		def apply_mapping(from_doctype, source_name, table_maps, target_doc, postprocess):
			map_doc(source, target, table_maps[from_doctype])
			postprocess(source, target)
			return target

		get_mapped_doc.side_effect = apply_mapping
		with patch("ccpcerp.ccpc_erp.doctype.work_request.work_request._", side_effect=lambda value: value):
			result = make_material_request("WR-03")

		self.assertIs(result, target)
		self.assertEqual(values["status"], "Draft")
		self.assertEqual(values["naming_series"], "MAT-MR-.YYYY.-")
		self.assertNotEqual(values["naming_series"], source.naming_series)
		self.assertEqual(values["company"], source.company)
		self.assertEqual(values["equipment"], source.equipment)
		self.assertEqual(values["department"], source.department)
		self.assertEqual(values["work_request"], "WR-03")
		self.assertEqual(target.material_request_type, "Material Issue")
		self.assertEqual(values["items"], [])
		validate_department.assert_called_once_with(
			source.department, source.company, "Work Request", require_eligible=True
		)
		source.validate_derived_job_order.assert_called_once_with()

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_department_company")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_equipment")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.get_cached_value", return_value="Cagayan Corn Products Corporation")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request._", side_effect=lambda value: value)
	def test_all_approved_statuses_are_server_eligible(
		self, translate, get_cached_value, validate_equipment, validate_department
	):
		self.assertEqual(
			MATERIAL_REQUEST_ALLOWED_STATUSES,
			{"Open", "Converted to Job Order", "In Progress"},
		)
		for status in MATERIAL_REQUEST_ALLOWED_STATUSES:
			with self.subTest(status=status):
				doc = frappe._dict(
					name="WR-03",
					status=status,
					company="Cagayan Corn Products Corporation",
					request_type="Facility",
					equipment=None,
					department="Wet Mill Plant - CCPC",
					validate_derived_job_order=Mock(),
				)
				validate_material_request_eligibility(doc)

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.bold", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.throw", side_effect=frappe.ValidationError)
	def test_terminal_blank_and_unknown_statuses_are_server_blocked(self, throw, bold, translate):
		for status in ("Closed", "Cancelled", "", None, "Unexpected"):
			with self.subTest(status=status), self.assertRaises(frappe.ValidationError):
				validate_material_request_eligibility(
					frappe._dict(name="WR-03", status=status)
				)

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_material_request_eligibility")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.get_doc")
	def test_direct_mapper_call_cannot_bypass_server_policy(self, get_doc, validate_eligibility):
		source = Mock()
		source.check_permission = Mock()
		get_doc.return_value = source
		validate_eligibility.side_effect = frappe.ValidationError("blocked")
		with self.assertRaises(frappe.ValidationError):
			make_material_request("WR-03")

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.get_mapped_doc")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_material_request_eligibility")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.frappe.get_doc")
	def test_one_work_request_can_map_multiple_material_requests(
		self, get_doc, validate_eligibility, get_mapped_doc
	):
		source = Mock()
		source.check_permission = Mock()
		get_doc.return_value = source
		get_mapped_doc.side_effect = [Mock(name="first"), Mock(name="second")]
		self.assertIsNot(make_material_request("WR-03"), make_material_request("WR-03"))
		self.assertEqual(get_mapped_doc.call_count, 2)

	def test_new_and_reassigned_work_request_require_eligible_department(self):
		doc = Mock(spec=WorkRequest)
		doc.is_new.return_value = True
		self.assertTrue(WorkRequest._requires_eligible_department(doc))

		doc.is_new.return_value = False
		doc.company = "Cagayan Corn Products Corporation"
		doc.department = "Dry Mill Plant - CCPC"
		doc.get_doc_before_save.return_value = frappe._dict(
			company=doc.company, department="Wet Mill Plant - CCPC"
		)
		self.assertTrue(WorkRequest._requires_eligible_department(doc))

		doc.get_doc_before_save.return_value.department = doc.department
		self.assertFalse(WorkRequest._requires_eligible_department(doc))


class IntegrationTestWorkRequest(IntegrationTestCase):
	"""
	Integration tests for WorkRequest.
	Use this class for testing interactions between multiple components.
	"""

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_department_company")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_equipment")
	def test_equipment_request_requires_equipment(self, validate_equipment, validate_department):
		doc = WorkRequest({"doctype": "Work Request", "request_type": "Equipment", "priority": "1"})
		with self.assertRaises(frappe.ValidationError):
			doc.validate()

	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_department_company")
	@patch("ccpcerp.ccpc_erp.doctype.work_request.work_request.validate_equipment")
	def test_facility_request_allows_blank_equipment(self, validate_equipment, validate_department):
		doc = WorkRequest({"doctype": "Work Request", "request_type": "Facility", "priority": "1"})
		doc.validate()
		validate_equipment.assert_called_once_with(None, None, require_active=False)

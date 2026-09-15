# Copyright (c) 2026, CCPC and Contributors
# See license.txt

from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

import frappe
from frappe.tests import IntegrationTestCase

from ccpcerp.ccpc_erp.doctype.job_order.job_order import JobOrder
from ccpcerp.ccpc_erp.work_request_job_order import (
	clear_work_request,
	validate_new_job_order_relationship,
)


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class TestJobOrderDeleteRules(TestCase):
	@patch("ccpcerp.ccpc_erp.equipment_asset.get_current_equipment_asset")
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.set_department_from_equipment")
	def test_new_standalone_job_order_snapshots_current_asset(self, set_department, current):
		current.return_value = ("EAA-00001", "ASSET-1")
		doc = MagicMock(spec=JobOrder)
		doc.is_new.return_value = True
		doc.work_request = None
		doc.equipment = "EQ-1"
		JobOrder.before_validate(doc)
		self.assertEqual((doc.equipment_asset_assignment, doc.asset), ("EAA-00001", "ASSET-1"))
		current.assert_called_once_with("EQ-1")

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.frappe.throw", side_effect=frappe.ValidationError)
	def test_new_standalone_job_order_requires_priority(self, throw, translate):
		valid = MagicMock(spec=JobOrder)
		valid.is_new.return_value = True
		valid.priority = "1"
		JobOrder.validate_priority(valid)

		blank = MagicMock(spec=JobOrder)
		blank.is_new.return_value = True
		blank.priority = None
		with self.assertRaises(frappe.ValidationError):
			JobOrder.validate_priority(blank)

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.frappe.throw", side_effect=frappe.ValidationError)
	def test_existing_job_order_priority_is_grandfathered_but_cannot_be_cleared(
		self, throw, translate
	):
		for previous, current, allowed in (
			(None, None, True),
			(None, "3", True),
			("2", None, False),
			("2", "4", True),
		):
			with self.subTest(previous=previous, current=current):
				doc = MagicMock(spec=JobOrder)
				doc.is_new.return_value = False
				doc.priority = current
				doc.get_doc_before_save.return_value = frappe._dict(priority=previous)
				if allowed:
					JobOrder.validate_priority(doc)
				else:
					with self.assertRaises(frappe.ValidationError):
						JobOrder.validate_priority(doc)

	def test_historical_blank_linked_job_order_can_progress_through_lifecycle(self):
		for status in ("Assigned", "In Progress", "Completed", "Closed", "Cancelled"):
			with self.subTest(status=status):
				doc = MagicMock(spec=JobOrder)
				doc.is_new.return_value = False
				doc.status = status
				doc.priority = None
				doc.work_request = "WR-07"
				doc.get_doc_before_save.return_value = frappe._dict(priority=None)
				JobOrder.validate_priority(doc)

	@patch("ccpcerp.ccpc_erp.work_request_job_order._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.work_request_job_order.frappe")
	def test_new_relationship_requires_open_blank_unlinked_work_request(
		self, patch_frappe, translate
	):
		patch_frappe.db.exists.return_value = None
		patch_frappe.bold.side_effect = lambda value: value
		patch_frappe.throw.side_effect = frappe.ValidationError
		eligible = frappe._dict(name="WR-04", status="Open", job_order=None, priority="2")
		validate_new_job_order_relationship(eligible)

		for status in ("Converted to Job Order", "In Progress", "Closed", "Cancelled", "", None):
			with self.subTest(status=status), self.assertRaises(frappe.ValidationError):
				validate_new_job_order_relationship(
					frappe._dict(name="WR-04", status=status, job_order=None)
				)

		with self.assertRaises(frappe.ValidationError):
			validate_new_job_order_relationship(
				frappe._dict(name="WR-04", status="Open", job_order="6")
			)

	@patch("ccpcerp.ccpc_erp.work_request_job_order._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.work_request_job_order.frappe")
	def test_authoritative_job_order_blocks_new_relationship(
		self, patch_frappe, translate
	):
		patch_frappe.db.exists.return_value = 7
		patch_frappe.bold.side_effect = lambda value: value
		patch_frappe.throw.side_effect = frappe.ValidationError
		with self.assertRaises(frappe.ValidationError):
			validate_new_job_order_relationship(
				frappe._dict(name="WR-04", status="Open", job_order=None, priority="2")
			)

	@patch("ccpcerp.ccpc_erp.work_request_job_order._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.work_request_job_order.frappe")
	def test_blank_priority_work_request_cannot_establish_linked_job_order(
		self, patch_frappe, translate
	):
		def throw(message):
			raise frappe.ValidationError(message)

		patch_frappe.db.exists.return_value = None
		patch_frappe.bold.side_effect = lambda value: value
		patch_frappe.throw.side_effect = throw
		with self.assertRaisesRegex(frappe.ValidationError, "Priority is required on Work Request WR-04"):
			validate_new_job_order_relationship(
				frappe._dict(name="WR-04", status="Open", job_order=None, priority=None)
			)

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.validate_new_job_order_relationship")
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.frappe.get_doc")
	def test_creation_policy_applies_only_to_new_linked_job_orders(
		self, get_doc, validate_relationship
	):
		work_request = frappe._dict(
			name="WR-05",
			status="Converted to Job Order",
			company="Cagayan Corn Products Corporation",
			equipment="WP-401A",
			priority="2",
		)
		get_doc.return_value = work_request
		new = MagicMock(spec=JobOrder)
		new.work_request = "WR-05"
		new.name = None
		new.company = work_request.company
		new.equipment = work_request.equipment
		new.priority = work_request.priority
		new.is_new.return_value = True
		JobOrder.validate_work_request_context(new)
		validate_relationship.assert_called_once_with(work_request, None)

		validate_relationship.reset_mock()
		existing = MagicMock(spec=JobOrder)
		existing.work_request = "WR-05"
		existing.company = work_request.company
		existing.equipment = work_request.equipment
		existing.priority = None
		existing.is_new.return_value = False
		JobOrder.validate_work_request_context(existing)
		validate_relationship.assert_not_called()

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.frappe.throw", side_effect=frappe.ValidationError)
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.validate_new_job_order_relationship")
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.frappe.get_doc")
	def test_new_linked_job_order_priority_must_match_source(
		self, get_doc, validate_relationship, throw, translate
	):
		get_doc.return_value = frappe._dict(
			name="WR-10",
			company="Cagayan Corn Products Corporation",
			equipment=None,
			priority="2",
		)
		doc = MagicMock(spec=JobOrder)
		doc.is_new.return_value = True
		doc.name = None
		doc.work_request = "WR-10"
		doc.company = "Cagayan Corn Products Corporation"
		doc.equipment = None
		doc.priority = "3"
		with self.assertRaises(frappe.ValidationError):
			JobOrder.validate_work_request_context(doc)

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.frappe.get_doc")
	def test_server_context_copy_includes_facility_asset_location(self, get_doc):
		work_request = frappe._dict(
			company="Cagayan Corn Products Corporation",
			equipment=None,
			equipment_description=None,
			asset_location="Boiler House",
			department="Wet Mill Plant - CCPC",
			problem_description="Repair piping support",
			priority="2",
			maintenance_section="Civil",
			requested_by="Administrator",
			equipment_asset_assignment="EAA-00001",
			asset="ASSET-1",
		)
		get_doc.return_value = work_request
		doc = MagicMock(spec=JobOrder)
		doc.is_new.return_value = True
		doc.work_request = "WR-04"
		doc.get.side_effect = lambda fieldname: None
		JobOrder.copy_work_request_context(doc)
		doc.set.assert_any_call("asset_location", "Boiler House")
		doc.set.assert_any_call("priority", "2")
		doc.set.assert_any_call("equipment_asset_assignment", "EAA-00001")
		doc.set.assert_any_call("asset", "ASSET-1")

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.validate_new_job_order_relationship")
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.frappe.get_doc")
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.frappe.throw", side_effect=frappe.ValidationError)
	def test_new_linked_job_order_rejects_asset_snapshot_mismatch(
		self, throw, translate, get_doc, validate_relationship
	):
		get_doc.return_value = frappe._dict(
			name="WR-10", company="Company A", equipment="EQ-1", priority="2",
			equipment_asset_assignment="EAA-00001", asset="ASSET-1",
		)
		doc = MagicMock(spec=JobOrder)
		doc.is_new.return_value = True
		doc.name = None
		doc.work_request = "WR-10"
		doc.company = "Company A"
		doc.equipment = "EQ-1"
		doc.priority = "2"
		doc.get.side_effect = {
			"equipment_asset_assignment": "EAA-00002", "asset": "ASSET-2"
		}.get
		with self.assertRaises(frappe.ValidationError):
			JobOrder.validate_work_request_context(doc)

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.set_department_from_equipment")
	def test_standalone_job_order_uses_shared_defaulting_but_linked_job_order_does_not(
		self, set_department
	):
		standalone = MagicMock(spec=JobOrder)
		standalone.is_new.return_value = True
		standalone.work_request = None
		JobOrder.before_validate(standalone)
		set_department.assert_called_once_with(standalone)

		set_department.reset_mock()
		linked = MagicMock(spec=JobOrder)
		linked.is_new.return_value = True
		linked.work_request = "WR-05"
		JobOrder.before_validate(linked)
		set_department.assert_not_called()

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order._", side_effect=lambda value: value)
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.validate_department_company")
	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.validate_equipment")
	def test_validation_requests_full_eligibility_for_new_job_order(
		self, validate_equipment, validate_department, translate
	):
		doc = MagicMock(spec=JobOrder)
		doc.equipment = "WP-401A"
		doc.company = "Cagayan Corn Products Corporation"
		doc.department = "Wet Mill Plant - CCPC"
		doc._requires_active_equipment.return_value = True
		doc._requires_eligible_department.return_value = True

		JobOrder.validate(doc)

		validate_department.assert_called_once_with(
			doc.department, doc.company, "Job Order", require_eligible=True
		)

	def test_department_reassignment_and_operational_status_transition_require_eligibility(self):
		doc = MagicMock(spec=JobOrder)
		doc.is_new.return_value = False
		doc.company = "Cagayan Corn Products Corporation"
		doc.department = "Wet Mill Plant - CCPC"
		doc.status = "Assigned"
		doc.get_doc_before_save.return_value = frappe._dict(
			company=doc.company, department=doc.department, status="Open"
		)
		self.assertTrue(JobOrder._requires_eligible_department(doc))

		doc.get_doc_before_save.return_value.status = "Assigned"
		self.assertFalse(JobOrder._requires_eligible_department(doc))

		doc.department = "Dry Mill Plant - CCPC"
		self.assertTrue(JobOrder._requires_eligible_department(doc))

	@patch("ccpcerp.ccpc_erp.work_request_job_order.frappe.get_doc")
	def test_numeric_job_order_name_matches_and_clears_link_backlink(self, get_doc):
		work_request = frappe._dict(
			job_order="4",
			status="Converted to Job Order",
			flags=frappe._dict(),
			save=Mock(),
		)
		get_doc.return_value = work_request

		clear_work_request(frappe._dict(name=4, work_request="WR-05"))

		self.assertIsNone(work_request.job_order)
		self.assertEqual(work_request.status, "Open")
		self.assertTrue(work_request.flags.syncing_job_order)
		work_request.save.assert_called_once_with(ignore_permissions=True)

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.clear_work_request")
	def test_blank_and_open_linked_job_order_deletion_runs_cleanup(self, cleanup):
		for status in (None, "", "Open"):
			with self.subTest(status=status):
				doc = MagicMock(spec=JobOrder)
				doc.status = status
				JobOrder.on_trash(doc)
				cleanup.assert_called_once_with(doc)
				cleanup.reset_mock()

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.clear_work_request")
	def test_progressed_job_order_deletion_is_blocked(self, cleanup):
		with (
			patch("ccpcerp.ccpc_erp.doctype.job_order.job_order._", side_effect=lambda value: value),
			patch(
				"ccpcerp.ccpc_erp.doctype.job_order.job_order.frappe.throw",
				side_effect=frappe.ValidationError,
			),
		):
			for status in ("Assigned", "In Progress", "Completed", "Closed", "Cancelled"):
				with self.subTest(status=status), self.assertRaises(frappe.ValidationError):
					doc = MagicMock(spec=JobOrder)
					doc.status = status
					JobOrder.on_trash(doc)
		cleanup.assert_not_called()

	@patch("ccpcerp.ccpc_erp.work_request_job_order.frappe.get_doc")
	def test_standalone_blank_and_open_job_orders_need_no_work_request_cleanup(self, get_doc):
		for status in (None, "", "Open"):
			with self.subTest(status=status):
				doc = frappe._dict(name=4, work_request=None, status=status)
				JobOrder.on_trash(doc)
		get_doc.assert_not_called()

	@patch("ccpcerp.ccpc_erp.work_request_job_order.frappe.get_doc")
	def test_different_backlink_and_unrelated_work_request_are_unchanged(self, get_doc):
		linked_elsewhere = frappe._dict(
			job_order="5", status="In Progress", flags=frappe._dict(), save=Mock()
		)
		unrelated = frappe._dict(
			job_order="9", status="Converted to Job Order", flags=frappe._dict(), save=Mock()
		)
		get_doc.return_value = linked_elsewhere

		clear_work_request(frappe._dict(name=4, work_request="WR-05"))

		self.assertEqual((linked_elsewhere.job_order, linked_elsewhere.status), ("5", "In Progress"))
		linked_elsewhere.save.assert_not_called()
		self.assertEqual((unrelated.job_order, unrelated.status), ("9", "Converted to Job Order"))
		unrelated.save.assert_not_called()


class IntegrationTestJobOrder(IntegrationTestCase):
	"""
	Integration tests for JobOrder.
	Use this class for testing interactions between multiple components.
	"""

	def test_work_request_is_immutable_after_save(self):
		doc = MagicMock(spec=JobOrder)
		doc.is_new.return_value = False
		doc.work_request = "WR-02"
		doc.get_doc_before_save.return_value = frappe._dict(work_request="WR-01")
		with self.assertRaises(frappe.ValidationError):
			JobOrder.validate_work_request_immutable(doc)

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.clear_work_request")
	def test_open_job_order_deletion_is_allowed(self, clear_work_request):
		doc = MagicMock(spec=JobOrder)
		doc.status = "Open"
		JobOrder.on_trash(doc)
		clear_work_request.assert_called_once_with(doc)

	@patch("ccpcerp.ccpc_erp.doctype.job_order.job_order.clear_work_request")
	def test_progressed_job_order_deletion_is_blocked(self, clear_work_request):
		doc = MagicMock(spec=JobOrder)
		doc.status = "Assigned"
		with self.assertRaises(frappe.ValidationError):
			JobOrder.on_trash(doc)
		clear_work_request.assert_not_called()

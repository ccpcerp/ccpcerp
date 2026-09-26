# Copyright (c) 2026, CCPC and Contributors
# See license.txt

import json
from datetime import date
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp import setup
from ccpcerp.ccpc_erp import mrs_permissions
from ccpcerp.ccpc_erp.doctype.mrs.mrs import (
	MRS,
	_build_item_search_text,
	_default_date_needed,
	_request_item_snapshot,
)


class TestMRSWorkflow(TestCase):
	def test_date_needed_defaults_to_21_days_after_request_date(self):
		self.assertEqual(_default_date_needed("2026-09-26"), "2026-10-17")

	def test_date_needed_default_preserves_manual_date(self):
		doc = MagicMock(spec=MRS)
		doc.is_new.return_value = True
		doc.request_date = "2026-09-26"
		doc.items = [
			frappe._dict(date_needed=None),
			frappe._dict(date_needed="2026-09-30"),
		]
		MRS._set_date_needed_defaults(doc)
		self.assertEqual(doc.items[0].date_needed, "2026-10-17")
		self.assertEqual(doc.items[1].date_needed, "2026-09-30")

	def test_item_search_text_contains_code_name_and_description(self):
		search_text = _build_item_search_text(
			[
				frappe._dict(
					item_code="BRG-6205",
					item_name="Bearing 6205",
					description="<p>Deep groove bearing for pump</p>",
				)
			]
		)
		self.assertIn("BRG-6205", search_text)
		self.assertIn("Bearing 6205", search_text)
		self.assertIn("Deep groove bearing for pump", search_text)
		self.assertNotIn("<p>", search_text)

	def test_item_search_is_available_as_standard_list_filter(self):
		meta = json.loads(Path(__file__).with_name("mrs.json").read_text())
		field = next(row for row in meta["fields"] if row["fieldname"] == "item_search_text")
		self.assertEqual(field["label"], "Item Search")
		self.assertEqual(field["fieldtype"], "Small Text")
		self.assertEqual(field["hidden"], 1)
		self.assertEqual(field["in_standard_filter"], 1)
		self.assertEqual(field["read_only"], 1)

	def test_mrs_rebuilds_item_search_text_from_current_items(self):
		doc = MagicMock(spec=MRS)
		doc.items = [
			frappe._dict(
				item_code="ITEM-00010",
				item_name="Pump Bearing",
				description="Replacement bearing",
			)
		]
		MRS._set_item_search_text(doc)
		self.assertIn("ITEM-00010", doc.item_search_text)
		self.assertIn("Pump Bearing", doc.item_search_text)
		self.assertIn("Replacement bearing", doc.item_search_text)

	def test_department_managers_can_return_mrs_for_correction(self):
		states = dict(setup.MRS_WORKFLOW_STATES)
		self.assertEqual(states["Returned for Correction"], setup.MRS_REQUESTOR_ROLE)

		requesting_return = next(
			transition
			for transition in setup.MRS_WORKFLOW_TRANSITIONS
			if transition["state"] == "Pending Requesting Department Manager"
			and transition["action"] == "Return for Correction"
		)
		self.assertEqual(requesting_return["next_state"], "Returned for Correction")
		self.assertEqual(requesting_return["allowed"], setup.DEPARTMENT_MANAGER_ROLE)
		self.assertEqual(
			requesting_return["condition"],
			"frappe.session.user == doc.requesting_department_manager",
		)

		use_return = next(
			transition
			for transition in setup.MRS_WORKFLOW_TRANSITIONS
			if transition["state"] == "Pending Use Department Manager"
			and transition["action"] == "Return for Correction"
		)
		self.assertEqual(use_return["next_state"], "Returned for Correction")
		self.assertEqual(use_return["allowed"], setup.DEPARTMENT_MANAGER_ROLE)
		self.assertEqual(
			use_return["condition"],
			"frappe.session.user == doc.use_department_manager",
		)

	def test_only_original_requestor_can_resubmit_returned_mrs(self):
		resubmit = next(
			transition
			for transition in setup.MRS_WORKFLOW_TRANSITIONS
			if transition["state"] == "Returned for Correction"
			and transition["action"] == "Resubmit for Approval"
		)
		self.assertEqual(resubmit["next_state"], "Pending Requesting Department Manager")
		self.assertEqual(resubmit["allowed"], setup.MRS_REQUESTOR_ROLE)
		self.assertEqual(resubmit["condition"], "frappe.session.user == doc.prepared_by")

	@patch.object(mrs_permissions.frappe, "get_roles", return_value=["MRS Requestor", "Department Manager"])
	def test_returned_mrs_write_permission_is_requestor_only(self, _get_roles):
		doc = frappe._dict(
			status="Returned for Correction",
			prepared_by="requestor@example.com",
			requesting_department="Maintenance - CCPC",
			use_department="Maintenance - CCPC",
		)
		doc.is_new = lambda: False

		self.assertTrue(
			mrs_permissions.has_mrs_permission(
				doc,
				user="requestor@example.com",
				permission_type="write",
			)
		)
		self.assertFalse(
			mrs_permissions.has_mrs_permission(
				doc,
				user="manager@example.com",
				permission_type="write",
			)
		)

	@patch("ccpcerp.ccpc_erp.doctype.mrs.mrs.frappe.get_roles", return_value=["MRS Requestor"])
	@patch("ccpcerp.ccpc_erp.doctype.mrs.mrs.frappe.session", create=True)
	def test_only_original_requestor_passes_returned_edit_check(self, session, _get_roles):
		doc = MagicMock(spec=MRS)
		doc.prepared_by = "requestor@example.com"

		session.user = "requestor@example.com"
		self.assertTrue(MRS._can_edit_returned_request(doc))

		session.user = "manager@example.com"
		self.assertFalse(MRS._can_edit_returned_request(doc))

	def test_request_item_snapshot_normalizes_date_objects_and_strings(self):
		from_database = [
			frappe._dict(
				name="row-1",
				item_code="ITEM-00018",
				requested_qty=2,
				date_needed=date(2026, 10, 17),
				remarks=None,
			)
		]
		from_form = [
			frappe._dict(
				name="row-1",
				item_code="ITEM-00018",
				requested_qty=2.0,
				date_needed="2026-10-17",
				remarks="",
			)
		]
		self.assertEqual(
			_request_item_snapshot(from_database),
			_request_item_snapshot(from_form),
		)

	@patch("ccpcerp.ccpc_erp.doctype.mrs.mrs.frappe.session", create=True)
	def test_plant_manager_can_change_authorized_qty_without_request_lock_error(self, session):
		session.user = "plantmanager@ccpc.com"
		before = frappe._dict(
			status="Pending Plant Manager",
			items=[
				frappe._dict(
					name="row-1",
					item_code="ITEM-00018",
					requested_qty=2,
					date_needed=date(2026, 10, 17),
					remarks=None,
					authorized_qty=2,
				)
			],
		)
		doc = MagicMock(spec=MRS)
		doc.is_new.return_value = False
		doc.plant_manager = "plantmanager@ccpc.com"
		doc.coo = "coo@ccpc.com"
		doc.bypass_other_management_approval = 0
		doc.items = [
			frappe._dict(
				name="row-1",
				item_code="ITEM-00018",
				requested_qty=2,
				date_needed="2026-10-17",
				remarks=None,
				authorized_qty=1,
			)
		]
		doc.get_doc_before_save.return_value = before
		doc.get.side_effect = lambda fieldname: {
			"use_department": None,
			"request_category": None,
			"equipment": None,
			"job_order": None,
			"work_request": None,
			"physical_location": None,
			"justification": None,
			"bypass_other_management_approval": 0,
			"bypass_reason": None,
		}.get(fieldname)

		MRS._validate_request_lock(doc)
		MRS._validate_management_edits(doc)

	@patch(
		"ccpcerp.ccpc_erp.doctype.mrs.mrs.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_plant_manager_still_cannot_change_requested_qty(self, throw):
		before = frappe._dict(
			status="Pending Plant Manager",
			items=[
				frappe._dict(
					name="row-1",
					item_code="ITEM-00018",
					requested_qty=2,
					date_needed="2026-10-17",
					remarks=None,
				)
			],
		)
		doc = MagicMock(spec=MRS)
		doc.is_new.return_value = False
		doc.items = [
			frappe._dict(
				name="row-1",
				item_code="ITEM-00018",
				requested_qty=3,
				date_needed="2026-10-17",
				remarks=None,
			)
		]
		doc.get_doc_before_save.return_value = before
		doc.get.return_value = None

		with self.assertRaises(frappe.ValidationError):
			MRS._validate_request_lock(doc)

	@patch("ccpcerp.ccpc_erp.doctype.mrs.mrs.frappe.session", create=True)
	def test_request_lock_ignores_child_row_order_when_request_is_unchanged(self, session):
		session.user = "plantmanager@ccpc.com"
		row_1 = frappe._dict(
			name="row-1",
			item_code="ITEM-00018",
			requested_qty=2,
			date_needed="2026-10-17",
			remarks=None,
		)
		row_2 = frappe._dict(
			name="row-2",
			item_code="ITEM-00019",
			requested_qty=1,
			date_needed="2026-10-20",
			remarks="For pump",
		)
		before = frappe._dict(status="Pending Plant Manager", items=[row_1, row_2])
		doc = MagicMock(spec=MRS)
		doc.is_new.return_value = False
		doc.items = [row_2, row_1]
		doc.get_doc_before_save.return_value = before
		doc.get.return_value = None

		MRS._validate_request_lock(doc)

	@patch("ccpcerp.ccpc_erp.doctype.mrs.mrs.frappe.get_roles", return_value=["MRS Requestor"])
	@patch("ccpcerp.ccpc_erp.doctype.mrs.mrs.frappe.session", create=True)
	def test_returned_requestor_can_change_requested_qty_without_management_error(
		self, session, _get_roles
	):
		session.user = "requestor@example.com"
		before = frappe._dict(
			status="Returned for Correction",
			bypass_other_management_approval=0,
			bypass_reason=None,
			items=[frappe._dict(authorized_qty=2, requested_qty=2)],
		)
		doc = MagicMock(spec=MRS)
		doc.is_new.return_value = False
		doc.prepared_by = "requestor@example.com"
		doc.items = [frappe._dict(authorized_qty=3, requested_qty=3)]
		doc.get_doc_before_save.return_value = before
		doc.get.side_effect = lambda fieldname: {
			"bypass_other_management_approval": 0,
			"bypass_reason": None,
		}.get(fieldname)

		MRS._validate_management_edits(doc)

	@patch(
		"ccpcerp.ccpc_erp.doctype.mrs.mrs.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	@patch("ccpcerp.ccpc_erp.doctype.mrs.mrs.frappe.get_roles", return_value=["MRS Requestor"])
	@patch("ccpcerp.ccpc_erp.doctype.mrs.mrs.frappe.session", create=True)
	def test_returned_requestor_cannot_change_management_bypass(
		self, session, _get_roles, throw
	):
		session.user = "requestor@example.com"
		before = frappe._dict(
			status="Returned for Correction",
			bypass_other_management_approval=0,
			bypass_reason=None,
			items=[frappe._dict(authorized_qty=2, requested_qty=2)],
		)
		doc = MagicMock(spec=MRS)
		doc.is_new.return_value = False
		doc.prepared_by = "requestor@example.com"
		doc.items = [frappe._dict(authorized_qty=2, requested_qty=2)]
		doc.get_doc_before_save.return_value = before
		doc.get.side_effect = lambda fieldname: {
			"bypass_other_management_approval": 1,
			"bypass_reason": "Not allowed",
		}.get(fieldname)

		with self.assertRaises(frappe.ValidationError):
			MRS._validate_management_edits(doc)

	@patch("ccpcerp.ccpc_erp.doctype.mrs.mrs.now", return_value="2026-09-26 13:40:00")
	@patch("ccpcerp.ccpc_erp.doctype.mrs.mrs.frappe.session", create=True)
	def test_return_clears_prior_department_manager_approval_stamps(self, session, _now):
		session.user = "use_manager@example.com"
		doc = MagicMock(spec=MRS)
		doc.is_new.return_value = False
		doc.status = "Returned for Correction"
		doc.requesting_department_manager_approved_on = "2026-09-25 10:00:00"
		doc.use_department_manager_approved_on = None
		doc.get_doc_before_save.return_value = frappe._dict(status="Pending Use Department Manager")

		MRS._stamp_approval_transition(doc)

		self.assertIsNone(doc.requesting_department_manager_approved_on)
		self.assertIsNone(doc.use_department_manager_approved_on)

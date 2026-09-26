# Copyright (c) 2026, CCPC and Contributors
# See license.txt

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp import setup
from ccpcerp.ccpc_erp import mrs_permissions
from ccpcerp.ccpc_erp.doctype.mrs.mrs import MRS


class TestMRSWorkflow(TestCase):
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

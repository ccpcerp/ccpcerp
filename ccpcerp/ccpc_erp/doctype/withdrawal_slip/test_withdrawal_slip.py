# Copyright (c) 2026, CCPC and Contributors
# See license.txt

import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp import setup as app_setup
from ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip import (
	WithdrawalSlip,
	_get_default_source_warehouse,
)
from ccpcerp.ccpc_erp.withdrawal_slip_stock import (
	_require_stockroom_user,
	make_material_issue,
	sync_withdrawal_slip_issue_status_for_name,
	validate_withdrawal_slip_stock_entry,
)


class TestWithdrawalSlip(TestCase):
	def test_withdrawal_slip_requestor_role_drives_request_states(self):
		states = dict(app_setup.WORKFLOW_STATES)
		self.assertEqual(states["Draft"], app_setup.WITHDRAWAL_SLIP_REQUESTOR_ROLE)
		self.assertEqual(
			states["Returned for Correction"],
			app_setup.WITHDRAWAL_SLIP_REQUESTOR_ROLE,
		)
		requestor_transitions = {
			transition["state"]: transition
			for transition in app_setup.WORKFLOW_TRANSITIONS
			if transition["state"] in {"Draft", "Returned for Correction"}
		}
		self.assertEqual(
			requestor_transitions["Draft"]["allowed"],
			app_setup.WITHDRAWAL_SLIP_REQUESTOR_ROLE,
		)
		self.assertEqual(
			requestor_transitions["Returned for Correction"]["allowed"],
			app_setup.WITHDRAWAL_SLIP_REQUESTOR_ROLE,
		)

	def test_withdrawal_slip_requestor_permission_is_owner_scoped(self):
		meta = json.loads(Path(__file__).with_name("withdrawal_slip.json").read_text())
		permission = next(
			row
			for row in meta["permissions"]
			if row["role"] == app_setup.WITHDRAWAL_SLIP_REQUESTOR_ROLE
		)
		self.assertEqual(permission["create"], 1)
		self.assertEqual(permission["read"], 1)
		self.assertEqual(permission["write"], 1)
		self.assertEqual(permission["if_owner"], 1)

	def test_operational_roles_are_seeded_as_requestors(self):
		self.assertTrue(
			{
				app_setup.MAINTENANCE_SUPERVISOR_ROLE,
				app_setup.STOCKROOM_PERSONNEL_ROLE,
				app_setup.PURCHASING_PERSONNEL_ROLE,
				app_setup.PURCHASING_SUPERVISOR_ROLE,
				app_setup.DEPARTMENT_MANAGER_ROLE,
				app_setup.MRS_MANAGEMENT_APPROVER_ROLE,
			}.issubset(set(app_setup.OPERATIONAL_REQUESTOR_SOURCE_ROLES))
		)

	@patch("ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe")
	def test_default_source_warehouse_requires_unique_warehouse_1(self, patch_frappe):
		patch_frappe.get_all.return_value = ["Warehouse 1 - CCPC"]
		self.assertEqual(
			_get_default_source_warehouse("Cagayan Corn Products Corporation"),
			"Warehouse 1 - CCPC",
		)
		patch_frappe.get_all.return_value = []
		self.assertIsNone(_get_default_source_warehouse("Cagayan Corn Products Corporation"))
		patch_frappe.get_all.return_value = ["A", "B"]
		self.assertIsNone(_get_default_source_warehouse("Cagayan Corn Products Corporation"))

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip._",
		side_effect=lambda value: value,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_requested_quantity_cannot_exceed_available_stock(self, throw, translate):
		doc = MagicMock(spec=WithdrawalSlip)
		doc.items = [
			frappe._dict(idx=1, item_code="BEARING-6205", requested_qty=6, available_qty=10),
			frappe._dict(idx=2, item_code="BEARING-6205", requested_qty=5, available_qty=10),
		]
		doc.source_warehouse = "Warehouse 1 - CCPC"
		with self.assertRaises(frappe.ValidationError):
			WithdrawalSlip._validate_requested_quantities(doc)

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip._",
		side_effect=lambda value: value,
	)
	def test_duplicate_rows_are_allowed_when_total_is_within_stock(self, translate):
		doc = MagicMock(spec=WithdrawalSlip)
		doc.items = [
			frappe._dict(idx=1, item_code="BEARING-6205", requested_qty=4, available_qty=10),
			frappe._dict(idx=2, item_code="BEARING-6205", requested_qty=6, available_qty=10),
		]
		doc.source_warehouse = "Warehouse 1 - CCPC"
		WithdrawalSlip._validate_requested_quantities(doc)

	def test_approved_slip_does_not_revalidate_stock_cap_after_approval(self):
		doc = MagicMock(spec=WithdrawalSlip)
		doc.items = [
			frappe._dict(idx=1, item_code="BEARING-6205", requested_qty=8, available_qty=3),
		]
		doc.source_warehouse = "Warehouse 1 - CCPC"
		doc.is_new.return_value = False
		doc.get_doc_before_save.return_value = frappe._dict(status="Approved")
		WithdrawalSlip._validate_requested_quantities(doc)

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip._",
		side_effect=lambda value: value,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_approved_request_intent_is_locked(self, throw, translate):
		doc = MagicMock(spec=WithdrawalSlip)
		doc.is_new.return_value = False
		doc.get_doc_before_save.return_value = frappe._dict(status="Approved")
		doc._request_intent_changed.return_value = True
		with self.assertRaises(frappe.ValidationError):
			WithdrawalSlip._validate_request_intent_lock(doc)

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip._",
		side_effect=lambda value: value,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.get_roles",
		return_value=[],
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.session",
		create=True,
	)
	def test_returned_request_only_original_supervisor_can_correct(
		self, session, get_roles, throw, translate
	):
		doc = MagicMock(spec=WithdrawalSlip)
		doc.is_new.return_value = False
		doc._request_intent_changed.return_value = True
		before = frappe._dict(
			status="Returned for Correction",
			approving_supervisor="supervisor@example.com",
		)
		doc.get_doc_before_save.return_value = before
		doc._can_edit_returned_request.side_effect = (
			lambda previous: WithdrawalSlip._can_edit_returned_request(doc, previous)
		)

		session.user = "supervisor@example.com"
		WithdrawalSlip._validate_request_intent_lock(doc)

		session.user = "other-supervisor@example.com"
		with self.assertRaises(frappe.ValidationError):
			WithdrawalSlip._validate_request_intent_lock(doc)


	def test_request_intent_comparison_normalizes_equivalent_values(self):
		before = MagicMock()
		before_values = {
			"requestor": "Francis Cañada",
			"required_by": "2026-09-19",
			"job_order": None,
			"equipment": None,
			"department": "Wet Mill Plant - CCPC",
			"section": None,
			"physical_location": "Milling Section",
			"purpose": "Test withdrawal Slip review",
			"company": "Cagayan Corn Products Corporation",
		}
		before.get.side_effect = before_values.get
		before.items = [
			frappe._dict(item_code="ITEM-00010", requested_qty=1, remarks=None)
		]

		doc = MagicMock(spec=WithdrawalSlip)
		current_values = {
			**before_values,
			"job_order": "",
			"equipment": "",
			"section": "",
		}
		doc.get.side_effect = current_values.get
		doc.items = [
			frappe._dict(item_code="ITEM-00010", requested_qty=1.0, remarks="")
		]

		self.assertFalse(WithdrawalSlip._request_intent_changed(doc, before))

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip._",
		side_effect=lambda value: value,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_stockroom_fields_cannot_change_outside_stockroom_review(self, throw, translate):
		doc = MagicMock(spec=WithdrawalSlip)
		doc.is_new.return_value = False
		doc.get_doc_before_save.return_value = frappe._dict(status="Returned for Correction")
		doc._stockroom_controls_changed.return_value = True
		doc._can_edit_stockroom.return_value = False
		with self.assertRaises(frappe.ValidationError):
			WithdrawalSlip._validate_stockroom_controls(doc)

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.get_roles",
		return_value=["Stockroom Personnel"],
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.session",
		create=True,
	)
	def test_stockroom_personnel_can_edit_stockroom_review_fields(self, session, get_roles):
		session.user = "stockroom@example.com"
		doc = MagicMock(spec=WithdrawalSlip)
		before = frappe._dict(status="Stockroom Review")
		self.assertTrue(WithdrawalSlip._can_edit_stockroom(doc, before))

	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._get_submitted_qty_by_slip_item", return_value={})
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._require_stockroom_user")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_meta")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.new_doc")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_all", return_value=[])
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_doc")
	def test_intercompany_material_issue_does_not_copy_requesting_department(
		self, get_doc, get_all, new_doc, get_meta, require_stockroom, submitted_qty
	):
		slip = MagicMock()
		slip.name = "WS-INTERCOMPANY"
		slip.status = "Stockroom Review"
		slip.received_by = "RMC Receiver"
		slip.company = "Cagayan Corn Products Corporation"
		slip.requesting_company = "Ricor Mills Corporation"
		slip.department = "Maintenance Department - RMC"
		slip.work_for_department = "Maintenance Department - CCPC"
		slip.source_warehouse = "Warehouse 1 - CCPC"
		slip.equipment = None
		request_items = [
			frappe._dict(
				idx=1,
				name="WSI-1",
				item_code="ITEM-00001",
				requested_qty=1,
				issue_qty=1,
				uom="Nos",
				stockroom_remarks="",
			)
		]
		slip.get.side_effect = lambda key: request_items if key == "items" else (
			"Maintenance Department - CCPC" if key == "work_for_department" else None
		)
		get_doc.return_value = slip

		stock_entry = MagicMock()
		stock_entry.name = "MAT-STE-0001"
		stock_entry.meta.has_field.return_value = True
		stock_entry.items = []
		stock_entry.append.side_effect = lambda fieldname, values: stock_entry.items.append(
			frappe._dict(values)
		)
		new_doc.return_value = stock_entry

		detail_meta = MagicMock()
		detail_meta.has_field.return_value = True
		get_meta.return_value = detail_meta

		with (
			patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.has_permission", return_value=True),
			patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._lock_withdrawal_slip"),
			patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._get_issuable_slip", return_value=slip),
			patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._assert_no_submitted_material_issue"),
			patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._get_existing_draft_names", return_value=[]),
			patch("ccpcerp.ccpc_erp.withdrawal_issue_beneficiary.require_department_gl_dimension"),
			patch("ccpcerp.ccpc_erp.withdrawal_issue_beneficiary.validate_department_company"),
		):
			result = make_material_issue(slip.name)

		self.assertEqual(stock_entry.company, "Cagayan Corn Products Corporation")
		self.assertEqual(stock_entry.department, "Maintenance Department - CCPC")
		self.assertEqual(stock_entry.items[0].department, "Maintenance Department - CCPC")
		self.assertNotEqual(stock_entry.department, "Maintenance Department - RMC")
		self.assertEqual(result, {"name": "MAT-STE-0001", "existing": 0})


	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_roles",
		return_value=["Stockroom Personnel"],
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.session",
		create=True,
	)
	def test_stockroom_personnel_can_issue_withdrawal_slip(self, session, get_roles):
		session.user = "stockroom@example.com"
		_require_stockroom_user()

	def test_stockroom_review_defaults_issue_qty_to_requested_qty(self):
		doc = MagicMock(spec=WithdrawalSlip)
		doc.is_new.return_value = False
		doc.status = "Stockroom Review"
		doc.get_doc_before_save.return_value = frappe._dict(status="Approved")
		doc.items = [
			frappe._dict(idx=1, requested_qty=8, issue_qty=0),
			frappe._dict(idx=2, requested_qty=3, issue_qty=0),
		]
		WithdrawalSlip._set_issue_qty_defaults(doc)
		self.assertEqual(doc.items[0].issue_qty, 8)
		self.assertEqual(doc.items[1].issue_qty, 3)

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip._",
		side_effect=lambda value: value,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_issue_qty_cannot_exceed_requested_qty(self, throw, translate):
		doc = MagicMock(spec=WithdrawalSlip)
		doc.status = "Stockroom Review"
		doc.items = [
			frappe._dict(
				idx=1,
				requested_qty=2,
				issue_qty=3,
				issued_qty=0,
				returned_qty=0,
				physical_discrepancy=0,
				discrepancy_remarks="",
				stockroom_remarks="",
			)
		]
		with self.assertRaises(frappe.ValidationError):
			WithdrawalSlip._validate_issue_tracking(doc)

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip._",
		side_effect=lambda value: value,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_reduced_issue_qty_requires_shortage_reason(self, throw, translate):
		doc = MagicMock(spec=WithdrawalSlip)
		doc.status = "Stockroom Review"
		doc.items = [
			frappe._dict(
				idx=1,
				requested_qty=2,
				issue_qty=1,
				issued_qty=0,
				returned_qty=0,
				physical_discrepancy=0,
				discrepancy_remarks="",
				stockroom_remarks="",
			)
		]
		with self.assertRaises(frappe.ValidationError):
			WithdrawalSlip._validate_issue_tracking(doc)

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip._",
		side_effect=lambda value: value,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_physical_discrepancy_requires_remarks(self, throw, translate):
		doc = MagicMock(spec=WithdrawalSlip)
		doc.status = "Draft"
		doc.items = [
			frappe._dict(
				idx=1,
				requested_qty=2,
				issued_qty=0,
				returned_qty=0,
				physical_discrepancy=1,
				discrepancy_remarks="",
			)
		]
		with self.assertRaises(frappe.ValidationError):
			WithdrawalSlip._validate_issue_tracking(doc)

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip._",
		side_effect=lambda value: value,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_issued_qty_cannot_exceed_requested_qty(self, throw, translate):
		doc = MagicMock(spec=WithdrawalSlip)
		doc.status = "Draft"
		doc.items = [
			frappe._dict(
				idx=1,
				requested_qty=2,
				issued_qty=3,
				returned_qty=0,
				physical_discrepancy=0,
				discrepancy_remarks="",
			)
		]
		with self.assertRaises(frappe.ValidationError):
			WithdrawalSlip._validate_issue_tracking(doc)

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.get_cached_value"
	)
	def test_physical_location_default_preserves_manual_override(self, get_cached_value):
		get_cached_value.side_effect = lambda doctype, name, fieldname, **kwargs: {
			("Equipment Registry", "EQ-OLD", "physical_location"): "Old Area",
			("Equipment Registry", "EQ-NEW", "physical_location"): "New Area",
		}.get((doctype, name, fieldname))

		doc = MagicMock(spec=WithdrawalSlip)
		doc.equipment = "EQ-NEW"
		doc.physical_location = "Manual Location"
		doc.is_new.return_value = False
		doc.get_doc_before_save.return_value = frappe._dict(equipment="EQ-OLD")
		WithdrawalSlip._set_physical_location_from_equipment(doc)
		self.assertEqual(doc.physical_location, "Manual Location")

		doc.physical_location = "Old Area"
		WithdrawalSlip._set_physical_location_from_equipment(doc)
		self.assertEqual(doc.physical_location, "New Area")

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.get_cached_value"
	)
	def test_job_order_defaults_equipment_without_forcing_department_override(
		self, get_cached_value
	):
		get_cached_value.return_value = frappe._dict(
			company="Cagayan Corn Products Corporation",
			equipment="EQ-1",
			department="Maintenance - CCPC",
			asset_location="Plant 1",
		)
		doc = MagicMock(spec=WithdrawalSlip)
		doc.job_order = "12"
		doc.company = None
		doc.equipment = None
		doc.department = "Production - CCPC"
		doc.work_for_department = None
		doc.physical_location = None
		doc.is_new.return_value = True
		WithdrawalSlip._apply_job_order_defaults(doc)
		self.assertEqual(doc.company, "Cagayan Corn Products Corporation")
		self.assertEqual(doc.equipment, "EQ-1")
		self.assertEqual(doc.department, "Production - CCPC")

	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.session",
		create=True,
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.nowdate",
		return_value="2026-09-18",
	)
	def test_new_slip_captures_logged_in_supervisor_and_required_date(self, today, session):
		session.user = "supervisor@example.com"
		doc = MagicMock(spec=WithdrawalSlip)
		doc.is_new.return_value = True
		doc.approving_supervisor = None
		doc.required_by = None
		doc._set_company_default = MagicMock()
		doc._apply_job_order_defaults = MagicMock()
		doc._set_equipment_details = MagicMock()
		doc._set_physical_location_from_equipment = MagicMock()
		doc._set_source_warehouse_default = MagicMock()
		doc._set_item_defaults_and_availability = MagicMock()
		with patch.object(WithdrawalSlip, "_set_work_for_department_from_equipment"):
			WithdrawalSlip.before_validate(doc)
		self.assertEqual(doc.approving_supervisor, "supervisor@example.com")
		self.assertEqual(doc.required_by, "2026-09-18")

	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._require_stockroom_user")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._validate_provenance_immutable")
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_submitted_qty_by_slip_item",
		return_value={"WSI-1": 0.5},
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_actual_qty",
		return_value=10,
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_doc"
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_material_issue_rejects_existing_submitted_issue(
		self, throw, get_doc, actual_qty, submitted_qty, provenance, require_stockroom
	):
		get_doc.return_value = frappe._dict(
			name="WS-1",
			status="Stockroom Review",
			received_by="Pedro Santos",
			company="CCPC",
			source_warehouse="Warehouse 1 - CCPC",
			items=[
				frappe._dict(
					name="WSI-1",
					idx=1,
					item_code="ITEM-00010",
					requested_qty=1,
					issue_qty=1,
					stockroom_remarks="",
					returned_qty=0,
				)
			],
		)
		doc = frappe._dict(
			withdrawal_slip="WS-1",
			docstatus=1,
			company="CCPC",
			purpose="Material Issue",
			from_warehouse="Warehouse 1 - CCPC",
			items=[
				frappe._dict(
					idx=1,
					item_code="ITEM-00010",
					withdrawal_slip_item="WSI-1",
					s_warehouse="Warehouse 1 - CCPC",
					t_warehouse=None,
					qty=1,
					transfer_qty=1,
					conversion_factor=1,
				)
			],
		)
		with self.assertRaises(frappe.ValidationError):
			validate_withdrawal_slip_stock_entry(doc)

	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._require_stockroom_user")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._validate_provenance_immutable")
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_submitted_qty_by_slip_item",
		return_value={},
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_doc"
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_material_issue_must_match_stockroom_issue_qty(
		self, throw, get_doc, submitted_qty, provenance, require_stockroom
	):
		get_doc.return_value = frappe._dict(
			name="WS-1",
			status="Stockroom Review",
			received_by="Pedro Santos",
			company="CCPC",
			source_warehouse="Warehouse 1 - CCPC",
			items=[
				frappe._dict(
					name="WSI-1",
					idx=1,
					item_code="ITEM-00010",
					requested_qty=2,
					issue_qty=1,
					stockroom_remarks="Only one available",
					returned_qty=0,
				)
			],
		)
		doc = frappe._dict(
			withdrawal_slip="WS-1",
			docstatus=0,
			company="CCPC",
			purpose="Material Issue",
			from_warehouse="Warehouse 1 - CCPC",
			items=[
				frappe._dict(
					idx=1,
					item_code="ITEM-00010",
					withdrawal_slip_item="WSI-1",
					s_warehouse="Warehouse 1 - CCPC",
					t_warehouse=None,
					qty=2,
					transfer_qty=2,
					conversion_factor=1,
				)
			],
		)
		with self.assertRaises(frappe.ValidationError):
			validate_withdrawal_slip_stock_entry(doc)

	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._require_stockroom_user")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._validate_provenance_immutable")
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_submitted_qty_by_slip_item",
		return_value={},
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_actual_qty",
		return_value=1,
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_doc"
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_material_issue_cannot_exceed_current_actual_qty(
		self, throw, get_doc, actual_qty, submitted_qty, provenance, require_stockroom
	):
		get_doc.return_value = frappe._dict(
			name="WS-1",
			status="Stockroom Review",
			received_by="Pedro Santos",
			company="CCPC",
			source_warehouse="Warehouse 1 - CCPC",
			items=[
				frappe._dict(
					name="WSI-1",
					idx=1,
					item_code="ITEM-00010",
					requested_qty=5,
					issue_qty=5,
					stockroom_remarks="",
					returned_qty=0,
				)
			],
		)
		doc = frappe._dict(
			withdrawal_slip="WS-1",
			docstatus=1,
			company="CCPC",
			purpose="Material Issue",
			from_warehouse="Warehouse 1 - CCPC",
			items=[
				frappe._dict(
					idx=1,
					item_code="ITEM-00010",
					withdrawal_slip_item="WSI-1",
					s_warehouse="Warehouse 1 - CCPC",
					t_warehouse=None,
					qty=5,
					transfer_qty=5,
					conversion_factor=1,
				)
			],
		)
		with (
			patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._lock_withdrawal_slip", return_value=frappe._dict(
			status="Stockroom Review",
			received_by="Pedro Santos",
			company="CCPC",
			source_warehouse="Warehouse 1 - CCPC",
		)),
			patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._assert_independent_issuer"),
			patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._assert_no_submitted_material_issue"),
			self.assertRaises(frappe.ValidationError),
		):
			validate_withdrawal_slip_stock_entry(doc, method="before_submit")

	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._require_stockroom_user")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._validate_provenance_immutable")
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_submitted_qty_by_slip_item",
		return_value={},
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_actual_qty",
		return_value=10,
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_doc"
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.session",
		create=True,
	)
	def test_material_issue_before_submit_captures_stockroom_user(
		self, session, get_doc, actual_qty, submitted_qty, provenance, require_stockroom
	):
		session.user = "stockroom@personnel1.com"
		get_doc.return_value = frappe._dict(
			name="WS-1",
			status="Stockroom Review",
			received_by="Pedro Santos",
			company="CCPC",
			source_warehouse="Warehouse 1 - CCPC",
			items=[
				frappe._dict(
					name="WSI-1",
					idx=1,
					item_code="ITEM-00010",
					requested_qty=1,
					issue_qty=1,
					stockroom_remarks="",
					returned_qty=0,
				)
			],
		)
		doc = frappe._dict(
			withdrawal_slip="WS-1",
			withdrawal_slip_issued_by=None,
			docstatus=1,
			company="CCPC",
			purpose="Material Issue",
			from_warehouse="Warehouse 1 - CCPC",
			items=[
				frappe._dict(
					idx=1,
					item_code="ITEM-00010",
					withdrawal_slip_item="WSI-1",
					s_warehouse="Warehouse 1 - CCPC",
					t_warehouse=None,
					qty=1,
					transfer_qty=1,
					conversion_factor=1,
				)
			],
		)
		with (
			patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._lock_withdrawal_slip", return_value=frappe._dict(
			status="Stockroom Review",
			received_by="Pedro Santos",
			company="CCPC",
			source_warehouse="Warehouse 1 - CCPC",
		)),
			patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._assert_independent_issuer"),
			patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._assert_no_submitted_material_issue"),
		):
			validate_withdrawal_slip_stock_entry(doc, method="before_submit")
		self.assertEqual(doc.withdrawal_slip_issued_by, "stockroom@personnel1.com")

	@patch("frappe.utils.CallbackManager.add")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.clear_cache")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._get_user_full_name", return_value="Stock Person")
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_latest_submitted_issue",
		return_value=frappe._dict(
			posting_date="2026-09-22",
			posting_time="09:00:00",
			withdrawal_slip_issued_by="stockroom@personnel1.com",
		),
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_submitted_qty_by_slip_item",
		return_value={"WSI-1": 6},
	)
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.db.set_value")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_doc")
	def test_reduced_issue_marks_withdrawal_slip_issued_with_shortage(
		self, get_doc, set_value, submitted_qty, latest, full_name, clear_cache, after_commit
	):
		get_doc.return_value = frappe._dict(
			name="WS-1",
			items=[
				frappe._dict(
					name="WSI-1",
					requested_qty=8,
					returned_qty=0,
				),
			],
		)

		sync_withdrawal_slip_issue_status_for_name("WS-1")

		child_update = next(
			call.args[2]
			for call in set_value.call_args_list
			if call.args[0] == "Withdrawal Slip Item"
		)
		self.assertEqual(child_update["issued_qty"], 6)
		self.assertEqual(child_update["shortage_qty"], 2)

		parent_update = next(
			call.args[2]
			for call in set_value.call_args_list
			if call.args[0] == "Withdrawal Slip"
		)
		self.assertEqual(parent_update["status"], "Issued with Shortage")
		# The SQL status update may still be rolled back; never clear Redis early.
		clear_cache.assert_not_called()
		after_commit.assert_called_once()
		after_commit.call_args.args[0]()
		clear_cache.assert_called_once_with(doctype="Withdrawal Slip")

	@patch("frappe.utils.CallbackManager.add")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.clear_cache")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock._get_user_full_name", return_value="Stock Person")
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_latest_submitted_issue",
		return_value=frappe._dict(
			posting_date="2026-09-19",
			posting_time="14:30:00",
			withdrawal_slip_issued_by="stockroom@personnel1.com",
		),
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_submitted_qty_by_slip_item",
		return_value={"WSI-1": 2, "WSI-2": 3},
	)
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.db.set_value")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_doc")
	def test_full_issue_marks_withdrawal_slip_fully_issued(
		self, get_doc, set_value, submitted_qty, latest, full_name, clear_cache, after_commit
	):
		get_doc.return_value = frappe._dict(
			name="WS-1",
			items=[
				frappe._dict(
					name="WSI-1",
					requested_qty=2,
					returned_qty=0,
				),
				frappe._dict(
					name="WSI-2",
					requested_qty=3,
					returned_qty=0,
				),
			],
		)

		sync_withdrawal_slip_issue_status_for_name("WS-1")

		child_updates = {
			call.args[1]: call.args[2]
			for call in set_value.call_args_list
			if call.args[0] == "Withdrawal Slip Item"
		}
		self.assertEqual(child_updates["WSI-1"]["issued_qty"], 2)
		self.assertEqual(child_updates["WSI-1"]["shortage_qty"], 0)
		self.assertEqual(child_updates["WSI-2"]["issued_qty"], 3)
		self.assertEqual(child_updates["WSI-2"]["shortage_qty"], 0)

		parent_update = next(
			call.args[2]
			for call in set_value.call_args_list
			if call.args[0] == "Withdrawal Slip"
		)
		self.assertEqual(parent_update["status"], "Fully Issued")
		self.assertEqual(parent_update["issued_by"], "stockroom@personnel1.com")
		self.assertEqual(parent_update["issued_by_name"], "Stock Person")
		# The SQL status update may still be rolled back; never clear Redis early.
		clear_cache.assert_not_called()
		after_commit.assert_called_once()
		after_commit.call_args.args[0]()
		clear_cache.assert_called_once_with(doctype="Withdrawal Slip")


	@patch("frappe.utils.CallbackManager.add")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.clear_cache")
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock._get_submitted_qty_by_slip_item",
		return_value={},
	)
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.db.set_value",
		side_effect=RuntimeError("simulated SQL failure"),
	)
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_doc")
	def test_issue_status_failed_sql_write_does_not_schedule_cache_clear(
		self, get_doc, set_value, submitted_qty, clear_cache, after_commit
	):
		get_doc.return_value = frappe._dict(
			name="WS-1",
			items=[frappe._dict(name="WSI-1", requested_qty=2, returned_qty=0)],
		)
		with self.assertRaisesRegex(RuntimeError, "simulated SQL failure"):
			sync_withdrawal_slip_issue_status_for_name("WS-1")
		set_value.assert_called_once()
		clear_cache.assert_not_called()
		after_commit.assert_not_called()


	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.logging.getLogger")
	@patch(
		"ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.clear_cache",
		side_effect=RuntimeError("simulated Redis outage"),
	)
	def test_cache_refresh_outage_does_not_raise_after_commit(self, clear_cache, logger):
		from ccpcerp.ccpc_erp.withdrawal_slip_stock import (
			_clear_withdrawal_slip_cache_after_commit,
		)

		# A post-commit callback error must not turn a committed issue into an
		# apparent failed POST which users might retry.
		_clear_withdrawal_slip_cache_after_commit()
		clear_cache.assert_called_once_with(doctype="Withdrawal Slip")
		logger.assert_called_once_with("ccpcerp.ccpc_erp.withdrawal_slip_stock")
		logger.return_value.exception.assert_called_once()


	def test_withdrawal_slip_has_separate_issuing_and_requesting_company(self):
		meta = json.loads(Path(__file__).with_name("withdrawal_slip.json").read_text())
		fields = {row["fieldname"]: row for row in meta["fields"]}
		self.assertEqual(fields["company"]["label"], "Issuing Company")
		self.assertEqual(fields["requesting_company"]["options"], "Company")
		self.assertEqual(fields["department"]["label"], "Requesting Department")
		self.assertEqual(fields["section"]["options"], "CCPC Section")
		self.assertEqual(fields["work_for_section"]["options"], "CCPC Section")

	def test_confirmed_company_section_structure_is_seeded(self):
		structure = app_setup.WITHDRAWAL_SECTION_STRUCTURE
		self.assertIn("Cagayan Corn Products Corporation", structure)
		self.assertIn("Ricor Mills Corporation", structure)
		self.assertIn(
			"Starch Drying Section",
			structure["Cagayan Corn Products Corporation"]["Wet Mill Plant"],
		)
		self.assertIn(
			"Liquefaction Section",
			structure["Ricor Mills Corporation"]["Glucose Plant"],
		)

	@patch("ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.db.exists")
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.throw",
		side_effect=frappe.ValidationError,
	)
	def test_configured_department_requires_section_on_new_slip(self, throw, exists):
		exists.return_value = True
		doc = MagicMock(spec=WithdrawalSlip)
		doc.is_new.return_value = True
		with self.assertRaises(frappe.ValidationError):
			WithdrawalSlip._validate_section_requirement(
				doc,
				"Wet Mill Plant - CCPC",
				None,
				"Requesting Section",
				"department",
				"section",
			)


class TestWithdrawalSlipEquipmentWorkForDefaults(TestCase):
	@patch("ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.get_cached_value")
	def test_rmc_equipment_defaults_work_for_department_and_section(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(
			department="Glucose Plant - RMC",
			section="Filtration Section - Glucose Plant - RMC",
		)
		doc = MagicMock(spec=WithdrawalSlip)
		doc.equipment = "G-FP-401A"
		doc.work_for_department = None
		doc.work_for_section = None

		WithdrawalSlip._set_work_for_department_from_equipment(doc)

		self.assertEqual(doc.work_for_department, "Glucose Plant - RMC")
		self.assertEqual(doc.work_for_section, "Filtration Section - Glucose Plant - RMC")


class TestWithdrawalSlipPersonnelDefaults(TestCase):
	@patch("ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.db.get_value")
	def test_new_slip_defaults_requesting_context_from_preparer_personnel(self, get_value):
		get_value.return_value = frappe._dict(personnel_name="RMC Supervisor", company="Ricor Mills Corporation", department="Maintenance Department - RMC", section="Electrical Section - Maintenance Department - RMC")
		doc = MagicMock(spec=WithdrawalSlip)
		doc.approving_supervisor = "rmc.user@example.com"
		doc.prepared_by_name = None
		doc.requesting_company = None
		doc.department = None
		doc.section = None
		doc.is_new.return_value = True
		WithdrawalSlip._set_preparer_defaults(doc)
		self.assertEqual(doc.prepared_by_name, "RMC Supervisor")
		self.assertEqual(doc.requesting_company, "Ricor Mills Corporation")
		self.assertEqual(doc.department, "Maintenance Department - RMC")
		self.assertEqual(doc.section, "Electrical Section - Maintenance Department - RMC")


class TestWithdrawalSlipFormDefaults(TestCase):
	@patch("ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip._get_default_source_warehouse", return_value="Warehouse 1 - CCPC")
	@patch("ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.defaults.get_user_default", return_value="Cagayan Corn Products Corporation")
	@patch("ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.db.get_value")
	@patch("ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.session", create=True)
	def test_form_defaults_keep_issuer_but_use_personnel_requester(self, session, get_value, user_default, warehouse):
		from ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip import get_form_defaults
		session.user = "rmc.user@example.com"
		get_value.return_value = frappe._dict(
			personnel_name="Dindo Anticamara",
			company="Ricor Mills Corporation",
			department="Maintenance Department - RMC",
			section="Electrical Section - Maintenance Department - RMC",
		)
		defaults = get_form_defaults()
		self.assertEqual(defaults["company"], "Cagayan Corn Products Corporation")
		self.assertEqual(defaults["source_warehouse"], "Warehouse 1 - CCPC")
		self.assertEqual(defaults["prepared_by_name"], "Dindo Anticamara")
		self.assertEqual(defaults["requesting_company"], "Ricor Mills Corporation")
		self.assertEqual(defaults["department"], "Maintenance Department - RMC")
		self.assertEqual(defaults["section"], "Electrical Section - Maintenance Department - RMC")


class TestWithdrawalSlipClientDefaults(TestCase):
	def test_new_form_initializer_overrides_legacy_requesting_company_default(self):
		script = Path(__file__).with_name("withdrawal_slip.js").read_text()
		self.assertIn(
			"frm.doc.requesting_company !== defaults.requesting_company",
			script,
		)
		self.assertNotIn(
			"!frm.doc.requesting_company && defaults.requesting_company",
			script,
		)

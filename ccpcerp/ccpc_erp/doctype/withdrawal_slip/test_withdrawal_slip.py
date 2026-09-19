# Copyright (c) 2026, CCPC and Contributors
# See license.txt

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip import (
	WithdrawalSlip,
	_get_default_source_warehouse,
)
from ccpcerp.ccpc_erp.withdrawal_slip_stock import (
	sync_withdrawal_slip_issue_status_for_name,
	validate_withdrawal_slip_stock_entry,
)


class TestWithdrawalSlip(TestCase):
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
		return_value=["Withdrawal Slip Stockroom"],
	)
	@patch(
		"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.frappe.session",
		create=True,
	)
	def test_withdrawal_slip_stockroom_can_edit_stockroom_review_fields(self, session, get_roles):
		session.user = "stockroom@example.com"
		doc = MagicMock(spec=WithdrawalSlip)
		before = frappe._dict(status="Stockroom Review")
		self.assertTrue(WithdrawalSlip._can_edit_stockroom(doc, before))

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
		doc._set_company_from_equipment = MagicMock()
		doc._set_equipment_details = MagicMock()
		doc._set_physical_location_from_equipment = MagicMock()
		doc._set_source_warehouse_default = MagicMock()
		doc._set_item_defaults_and_availability = MagicMock()
		with patch(
			"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.set_department_from_equipment"
		):
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
	def test_material_issue_cannot_exceed_remaining_requested_qty(
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
					qty=0.75,
					transfer_qty=0.75,
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
					returned_qty=0,
				)
			],
		)
		doc = frappe._dict(
			withdrawal_slip="WS-1",
			withdrawal_slip_issued_by=None,
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
					qty=1,
					transfer_qty=1,
					conversion_factor=1,
				)
			],
		)
		validate_withdrawal_slip_stock_entry(doc, method="before_submit")
		self.assertEqual(doc.withdrawal_slip_issued_by, "stockroom@personnel1.com")

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
		return_value={"WSI-1": 1},
	)
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.db.set_value")
	@patch("ccpcerp.ccpc_erp.withdrawal_slip_stock.frappe.get_doc")
	def test_partial_issue_marks_all_unfulfilled_rows_as_shortage(
		self, get_doc, set_value, submitted_qty, latest, full_name, clear_cache
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
		self.assertEqual(child_updates["WSI-1"]["issued_qty"], 1)
		self.assertEqual(child_updates["WSI-1"]["shortage_qty"], 1)
		self.assertEqual(child_updates["WSI-2"]["issued_qty"], 0)
		self.assertEqual(child_updates["WSI-2"]["shortage_qty"], 3)

		parent_update = next(
			call.args[2]
			for call in set_value.call_args_list
			if call.args[0] == "Withdrawal Slip"
		)
		self.assertEqual(parent_update["status"], "Partially Issued")
		self.assertEqual(parent_update["issued_by"], "stockroom@personnel1.com")
		self.assertEqual(parent_update["issued_by_name"], "Stock Person")

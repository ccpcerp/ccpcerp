# Copyright (c) 2026, CCPC and contributors
# See license.txt

"""Focused mocked tests; live database concurrency and posting remain to be tested."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp.ccpc_erp.withdrawal_slip_stock import (
	_assert_no_submitted_material_issue,
	_get_existing_draft_names,
	_get_issuable_slip,
	_lock_withdrawal_slip,
	issue_withdrawal_slip,
	make_material_issue,
	validate_withdrawal_slip_stock_entry,
)


MODULE = "ccpcerp.ccpc_erp.withdrawal_slip_stock"


class TestWithdrawalIssueSafety(TestCase):
	def setUp(self):
		# These mocked tests intentionally avoid frappe.init()/connect().
		# Replace the module's translation helper so fail-closed branches
		# do not attempt to load Redis translations or initialize a logger.
		translator = patch(f"{MODULE}._", side_effect=lambda message: message)
		translator.start()
		self.addCleanup(translator.stop)

	@patch(f"{MODULE}.frappe.db.sql")
	def test_parent_lock_uses_current_read(self, sql):
		sql.return_value = [frappe._dict(status="Stockroom Review")]
		self.assertEqual(_lock_withdrawal_slip("WS-1").status, "Stockroom Review")
		query, args = sql.call_args.args
		self.assertIn("FOR UPDATE", query)
		self.assertIn("`tabWithdrawal Slip`", query)
		self.assertEqual(args, ("WS-1",))
		self.assertTrue(sql.call_args.kwargs["as_dict"])

	@patch(f"{MODULE}.frappe.db.sql")
	def test_draft_lookup_uses_current_read(self, sql):
		sql.return_value = [frappe._dict(name="MAT-STE-1")]
		self.assertEqual(_get_existing_draft_names("WS-1"), ["MAT-STE-1"])
		self.assertIn("FOR UPDATE", sql.call_args.args[0])
		self.assertIn("docstatus = 0", sql.call_args.args[0])

	@patch(f"{MODULE}.frappe.throw", side_effect=frappe.ValidationError)
	@patch(f"{MODULE}.frappe.db.sql")
	def test_already_submitted_guard_is_a_locking_read(self, sql, throw):
		sql.return_value = [frappe._dict(name="MAT-STE-POSTED")]
		with self.assertRaises(frappe.ValidationError):
			_assert_no_submitted_material_issue("WS-1")
		query, args = sql.call_args.args
		self.assertIn("FOR UPDATE", query)
		self.assertIn("docstatus = 1", query)
		self.assertEqual(args, ("WS-1",))
		throw.assert_called_once()

	@patch(f"{MODULE}._lock_withdrawal_slip")
	@patch(f"{MODULE}._require_stockroom_user", side_effect=frappe.PermissionError)
	def test_non_stockroom_user_denied_before_lock(self, stockroom, lock):
		with self.assertRaises(frappe.PermissionError):
			issue_withdrawal_slip("WS-1")
		lock.assert_not_called()

	@patch(f"{MODULE}.frappe.get_doc")
	@patch(f"{MODULE}._lock_withdrawal_slip", return_value=frappe._dict(status="Stockroom Review"))
	@patch(f"{MODULE}._require_stockroom_user")
	def test_slip_write_permission_is_enforced(self, stockroom, lock, get_doc):
		slip = MagicMock()
		slip.check_permission.side_effect = frappe.PermissionError
		get_doc.return_value = slip
		with self.assertRaises(frappe.PermissionError):
			issue_withdrawal_slip("WS-1")
		slip.check_permission.assert_called_once_with("write")

	@patch(f"{MODULE}._get_existing_draft_names", return_value=[])
	@patch(f"{MODULE}._assert_no_submitted_material_issue")
	@patch(f"{MODULE}._get_issuable_slip")
	@patch(f"{MODULE}._lock_withdrawal_slip")
	@patch(f"{MODULE}._require_stockroom_user")
	@patch(f"{MODULE}._build_material_issue")
	def test_only_scoped_constructed_issue_uses_bypassed_permissions(
		self, build, stockroom, lock, get_slip, submitted, drafts
	):
		get_slip.return_value = frappe._dict(name="WS-1")
		stock_entry = MagicMock()
		stock_entry.name = "MAT-STE-1"
		stock_entry.flags = frappe._dict()
		build.return_value = stock_entry

		result = issue_withdrawal_slip("WS-1")

		stockroom.assert_called_once()
		lock.assert_called_once_with("WS-1")
		submitted.assert_called_once_with("WS-1")
		build.assert_called_once_with(get_slip.return_value)
		self.assertTrue(stock_entry.flags.ignore_permissions)
		stock_entry.insert.assert_called_once_with(ignore_permissions=True)
		stock_entry.submit.assert_called_once_with()
		self.assertEqual(result, {"name": "MAT-STE-1", "submitted": 1})

	@patch(f"{MODULE}.frappe.throw", side_effect=frappe.ValidationError)
	@patch(f"{MODULE}._build_material_issue")
	@patch(f"{MODULE}._get_existing_draft_names", return_value=["MAT-STE-1"])
	@patch(f"{MODULE}._assert_no_submitted_material_issue")
	@patch(f"{MODULE}._get_issuable_slip")
	@patch(f"{MODULE}._lock_withdrawal_slip")
	@patch(f"{MODULE}._require_stockroom_user")
	def test_single_preexisting_draft_is_never_privilege_submitted(
		self, stockroom, lock, get_slip, submitted, drafts, build, throw
	):
		get_slip.return_value = frappe._dict(name="WS-1")

		with self.assertRaises(frappe.ValidationError):
			issue_withdrawal_slip("WS-1")

		build.assert_not_called()
		throw.assert_called_once()

	@patch(f"{MODULE}.frappe.throw", side_effect=frappe.ValidationError)
	@patch(f"{MODULE}._build_material_issue")
	@patch(f"{MODULE}._get_existing_draft_names", return_value=["DRAFT-1", "DRAFT-2"])
	@patch(f"{MODULE}._assert_no_submitted_material_issue")
	@patch(f"{MODULE}._get_issuable_slip")
	@patch(f"{MODULE}._lock_withdrawal_slip")
	@patch(f"{MODULE}._require_stockroom_user")
	def test_multiple_existing_drafts_fail_closed(
		self, stockroom, lock, get_slip, submitted, drafts, build, throw
	):
		get_slip.return_value = frappe._dict(name="WS-1")
		with self.assertRaises(frappe.ValidationError):
			issue_withdrawal_slip("WS-1")
		build.assert_not_called()

	@patch(f"{MODULE}._get_existing_draft_names", return_value=[])
	@patch(f"{MODULE}._assert_no_submitted_material_issue")
	@patch(f"{MODULE}._get_issuable_slip")
	@patch(f"{MODULE}._lock_withdrawal_slip")
	@patch(f"{MODULE}._require_stockroom_user")
	@patch(f"{MODULE}._build_material_issue")
	def test_submit_exception_is_propagated_for_request_rollback(
		self, build, stockroom, lock, get_slip, submitted, drafts
	):
		get_slip.return_value = frappe._dict(name="WS-1")
		entry = MagicMock()
		entry.flags = frappe._dict()
		entry.submit.side_effect = frappe.ValidationError("Stock ledger rejected issue")
		build.return_value = entry

		with self.assertRaises(frappe.ValidationError):
			issue_withdrawal_slip("WS-1")
		entry.insert.assert_called_once_with(ignore_permissions=True)
		entry.submit.assert_called_once()

	@patch(f"{MODULE}.frappe.throw", side_effect=frappe.PermissionError)
	@patch(f"{MODULE}._lock_withdrawal_slip")
	@patch(f"{MODULE}.frappe.has_permission", return_value=False)
	@patch(f"{MODULE}._require_stockroom_user")
	def test_draft_endpoint_does_not_elevate_stock_entry_rights(
		self, stockroom, has_permission, lock, throw
	):
		with self.assertRaises(frappe.PermissionError):
			make_material_issue("WS-1")
		has_permission.assert_called_once_with("Stock Entry", "create")
		lock.assert_not_called()

	@patch(f"{MODULE}.frappe.get_doc")
	@patch(f"{MODULE}._assert_no_submitted_material_issue", side_effect=frappe.ValidationError)
	@patch(f"{MODULE}._lock_withdrawal_slip")
	@patch(f"{MODULE}._require_stockroom_user")
	def test_before_submit_rechecks_submitted_issue_under_parent_lock(
		self, stockroom, lock, submitted, get_doc
	):
		current = {
			"status": "Stockroom Review",
			"received_by": "Receiver",
			"company": "CCPC",
			"source_warehouse": "Stores - CCPC",
		}
		lock.return_value = frappe._dict(current)
		slip = frappe._dict(current)
		slip.name = "WS-1"
		get_doc.return_value = slip
		entry = frappe._dict(withdrawal_slip="WS-1", docstatus=1)

		with self.assertRaises(frappe.ValidationError):
			validate_withdrawal_slip_stock_entry(entry, method="before_submit")

		lock.assert_called_once_with("WS-1")
		submitted.assert_called_once_with("WS-1")

	@patch(f"{MODULE}.frappe.throw", side_effect=frappe.ValidationError)
	@patch(f"{MODULE}.frappe.get_doc")
	def test_stale_slip_snapshot_is_rejected_after_lock(self, get_doc, throw):
		fresh = frappe._dict(
			status="Fully Issued", received_by="Receiver",
			company="CCPC", source_warehouse="Stores - CCPC",
		)
		stale = MagicMock()
		stale.get.side_effect = {
			"status": "Stockroom Review",
			"received_by": "Receiver",
			"company": "CCPC",
			"source_warehouse": "Stores - CCPC",
		}.get
		get_doc.return_value = stale
		with self.assertRaises(frappe.ValidationError):
			_get_issuable_slip("WS-1", fresh)
		stale.check_permission.assert_called_once_with("write")

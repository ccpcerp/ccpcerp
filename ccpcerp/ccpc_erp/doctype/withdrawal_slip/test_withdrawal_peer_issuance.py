# Copyright (c) 2026, CCPC and contributors
# See license.txt

"""Site-free mocked checks for different-preparer/issuer policy."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp.ccpc_erp.withdrawal_slip_stock import (
    _assert_independent_issuer,
    _get_issuable_slip,
    issue_withdrawal_slip,
    make_material_issue,
    validate_withdrawal_slip_stock_entry,
)
from ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip import WithdrawalSlip

MODULE = "ccpcerp.ccpc_erp.withdrawal_slip_stock"
CONTROLLER = "ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip"


class TestWithdrawalPeerIssuance(TestCase):
    def setUp(self):
        tr = patch(f"{MODULE}._", side_effect=lambda message: message)
        tr.start()
        self.addCleanup(tr.stop)
        throwing = patch(f"{MODULE}.frappe.throw", side_effect=frappe.PermissionError)
        throwing.start()
        self.addCleanup(throwing.stop)
        session = patch(f"{MODULE}.frappe.session", create=True)
        self.session = session.start()
        self.addCleanup(session.stop)
        roles = patch(f"{MODULE}.frappe.get_roles", return_value=["Stockroom Personnel"])
        roles.start()
        self.addCleanup(roles.stop)

    @staticmethod
    def locked(preparer="juan@example.test"):
        return frappe._dict(
            status="Stockroom Review", received_by="Juan",
            company="TISS", source_warehouse="Stores - TISS",
            owner=preparer, approving_supervisor=preparer,
        )

    def test_self_issue_is_denied(self):
        self.session.user = "juan@example.test"
        with self.assertRaises(frappe.PermissionError):
            _assert_independent_issuer(self.locked())

    def test_peer_issue_is_allowed(self):
        self.session.user = "pedro@example.test"
        _assert_independent_issuer(self.locked())

    def test_supervisor_prepared_request_is_allowed_for_stockroom_peer(self):
        self.session.user = "pedro@example.test"
        _assert_independent_issuer(self.locked("supervisor@example.test"))

    def test_guest_and_unset_identity_are_denied(self):
        for issuer in (None, "Guest"):
            with self.subTest(issuer=issuer):
                self.session.user = issuer
                with self.assertRaises(frappe.PermissionError):
                    _assert_independent_issuer(self.locked())

    def test_missing_creator_identity_fails_closed(self):
        self.session.user = "pedro@example.test"
        for key in ("owner", "approving_supervisor"):
            with self.subTest(key=key):
                locked = self.locked()
                locked[key] = None
                with self.assertRaises(frappe.PermissionError):
                    _assert_independent_issuer(locked)

    def test_mismatch_creator_and_prepared_by_fails_closed(self):
        self.session.user = "pedro@example.test"
        locked = self.locked()
        locked.approving_supervisor = "another@example.test"
        with self.assertRaises(frappe.PermissionError):
            _assert_independent_issuer(locked)

    def test_administrator_is_not_automatically_exempt(self):
        self.session.user = "Administrator"
        with self.assertRaises(frappe.PermissionError):
            _assert_independent_issuer(self.locked("Administrator"))

    def test_administrator_cannot_issue_someone_elses_slip(self):
        self.session.user = "Administrator"
        with self.assertRaises(frappe.PermissionError):
            _assert_independent_issuer(self.locked("juan@example.test"))

    def test_unassigned_manager_cannot_issue_under_peer_policy(self):
        self.session.user = "manager@example.test"
        with patch(f"{MODULE}.frappe.get_roles", return_value=["System Manager"]):
            with self.assertRaises(frappe.PermissionError):
                _assert_independent_issuer(self.locked("juan@example.test"))

    @patch(f"{MODULE}.frappe.get_doc")
    def test_self_issuance_fails_before_stock_entry_is_created(self, get_doc):
        self.session.user = "juan@example.test"
        slip = frappe._dict(self.locked())
        slip.name = "WS-1"
        slip.check_permission = MagicMock()
        get_doc.return_value = slip
        with (patch(f"{MODULE}._require_stockroom_user"),
              patch(f"{MODULE}._lock_withdrawal_slip", return_value=self.locked()),
              patch(f"{MODULE}._build_material_issue") as build):
            with self.assertRaises(frappe.PermissionError):
                issue_withdrawal_slip("WS-1")
            build.assert_not_called()

    @patch(f"{MODULE}.frappe.get_doc")
    def test_draft_endpoint_also_blocks_self_issue(self, get_doc):
        self.session.user = "juan@example.test"
        slip = frappe._dict(self.locked())
        slip.name = "WS-1"
        slip.check_permission = MagicMock()
        get_doc.return_value = slip
        with (patch(f"{MODULE}._require_stockroom_user"),
              patch(f"{MODULE}.frappe.has_permission", return_value=True),
              patch(f"{MODULE}._lock_withdrawal_slip", return_value=self.locked()),
              patch(f"{MODULE}._build_material_issue") as build):
            with self.assertRaises(frappe.PermissionError):
                make_material_issue("WS-1")
            build.assert_not_called()

    @patch(f"{MODULE}.frappe.get_doc")
    def test_before_submit_blocks_self_issue_before_stock_checks(self, get_doc):
        self.session.user = "juan@example.test"
        slip = frappe._dict(self.locked())
        slip.name = "WS-1"
        get_doc.return_value = slip
        stock_entry = frappe._dict(withdrawal_slip="WS-1", docstatus=1)
        with (patch(f"{MODULE}._require_stockroom_user"),
              patch(f"{MODULE}._lock_withdrawal_slip", return_value=self.locked()),
              patch(f"{MODULE}._assert_no_submitted_material_issue") as duplicate):
            with self.assertRaises(frappe.PermissionError):
                validate_withdrawal_slip_stock_entry(stock_entry, method="before_submit")
            duplicate.assert_not_called()

    @patch(f"{MODULE}.frappe.get_doc")
    def test_typed_receiver_does_not_override_actual_preparer(self, get_doc):
        self.session.user = "juan@example.test"
        slip = frappe._dict(self.locked())
        slip.name = "WS-1"
        slip.requestor = "Some other employee"
        slip.received_by = "Someone else"
        slip.check_permission = MagicMock()
        get_doc.return_value = slip
        locked = self.locked()
        locked.received_by = "Someone else"
        with patch(f"{MODULE}._lock_withdrawal_slip", return_value=locked):
            with self.assertRaises(frappe.PermissionError):
                _get_issuable_slip("WS-1", locked)

    @patch(f"{MODULE}.frappe.get_doc")
    def test_stale_creator_identity_fails_closed(self, get_doc):
        self.session.user = "pedro@example.test"
        slip = frappe._dict(self.locked())
        slip.name = "WS-1"
        slip.owner = "different@example.test"
        slip.check_permission = MagicMock()
        get_doc.return_value = slip
        with self.assertRaises(frappe.PermissionError):
            # Patch the stale snapshot rejection into the same isolated type
            # used by the legacy unit tests, avoiding a live Frappe site.
            with patch(f"{MODULE}.frappe.throw", side_effect=frappe.PermissionError):
                _get_issuable_slip("WS-1", self.locked())

    @patch(f"{CONTROLLER}.frappe.throw", side_effect=frappe.ValidationError)
    def test_saved_slip_owner_cannot_be_changed(self, throw):
        doc = MagicMock(spec=WithdrawalSlip)
        doc.is_new.return_value = False
        doc.owner = "tampered@example.test"
        doc.approving_supervisor = "juan@example.test"
        doc.get_doc_before_save.return_value = frappe._dict(
            owner="juan@example.test", approving_supervisor="juan@example.test"
        )
        with self.assertRaises(frappe.ValidationError):
            WithdrawalSlip._validate_approving_supervisor_immutable(doc)
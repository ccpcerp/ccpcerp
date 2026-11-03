"""Unit guards for approved, but UNDEPLOYED, billing authorization policy.

This suite uses patched Frappe interfaces; it does not prove live HTTP,
MariaDB row locking or real-user DocPerm effectiveness. That is a separate gate.
"""
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp.ccpc_erp.intercompany_billing_authority import (
    PREPARER_ROLE,
    CHECKER_ROLE,
    require_billing_actor,
    require_distinct_billing_review,
)
from ccpcerp.ccpc_erp.doctype.intercompany_material_billing_batch.intercompany_material_billing_batch import (
    IntercompanyMaterialBillingBatch as Batch,
)

MODULE = "ccpcerp.ccpc_erp.intercompany_billing_authority"
BATCH = (
    "ccpcerp.ccpc_erp.doctype.intercompany_material_billing_batch."
    "intercompany_material_billing_batch"
)
SELLER = "_Test Company"
BUYER = "_Test Company 5"
PREPARER = "ci-billing-preparer@invalid.test"
CHECKER = "ci-finance-checker@invalid.test"


class TestBillingAuthorizationControls(TestCase):
    def check_actor(self, actor=PREPARER, role=PREPARER_ROLE,
                    grants=(SELLER, BUYER), assigned=None, stockroom=False,
                    enabled=1):
        if assigned is None:
            assigned = role

        def has_role(doctype, filters):
            self.assertEqual(doctype, "Has Role")
            self.assertEqual(filters["parent"], actor)
            if filters["role"] == assigned:
                return "HR-1"
            if filters["role"] == "Stockroom Personnel" and stockroom:
                return "HR-STOCK"
            return None

        def scope_sql(query, args, as_dict=False):
            self.assertIn("apply_to_all_doctypes=1", query)
            self.assertIn("IFNULL(applicable_for, '')=''", query)
            self.assertEqual(args[0], actor)
            return [{"name": "UP-1"}] if args[1] in grants else []

        with (
            patch(f"{MODULE}.frappe.session", frappe._dict(user=actor)),
            patch(f"{MODULE}.frappe.db.get_value", return_value=enabled),
            patch(f"{MODULE}.frappe.db.exists", side_effect=has_role),
            patch(f"{MODULE}.frappe.db.sql", side_effect=scope_sql) as sql,
        ):
            value = require_billing_actor(role, SELLER, BUYER)
        self.assertEqual(value, actor)
        self.assertEqual(sql.call_count, 2)

    def test_named_preparer_two_company_grants_accepted(self):
        self.check_actor()

    def test_named_finance_checker_two_company_grants_accepted(self):
        self.check_actor(actor=CHECKER, role=CHECKER_ROLE)

    def test_missing_one_company_denied(self):
        with self.assertRaises(frappe.PermissionError):
            self.check_actor(grants=(SELLER,))

    def test_wrong_role_denied_without_any_company_check(self):
        with self.assertRaises(frappe.PermissionError):
            self.check_actor(assigned="Accounts User")

    def test_stockroom_role_denied(self):
        with self.assertRaises(frappe.PermissionError):
            self.check_actor(stockroom=True)

    def test_disabled_user_denied(self):
        with self.assertRaises(frappe.PermissionError):
            self.check_actor(enabled=0)

    def test_administrator_guest_and_no_user_denied(self):
        for actor in ("Administrator", "Guest", ""):
            with (
                self.subTest(actor=actor),
                patch(f"{MODULE}.frappe.session", frappe._dict(user=actor)),
                patch(f"{MODULE}.frappe.db.get_value") as read,
                self.assertRaises(frappe.PermissionError),
            ):
                require_billing_actor(CHECKER_ROLE, SELLER, BUYER)
                read.assert_not_called()

    def test_self_approval_and_casefold_alias_denied(self):
        for maker, checker in (
            (PREPARER, PREPARER),
            (PREPARER, PREPARER.upper()),
            ("Administrator", CHECKER),
            (PREPARER, "Guest"),
            ("", CHECKER),
        ):
            with self.subTest(maker=maker, checker=checker):
                with self.assertRaises(frappe.PermissionError):
                    require_distinct_billing_review(maker, checker)


class TestCancelledPairCorrectionControls(TestCase):
    def batch(self):
        doc = MagicMock(spec=Batch)
        doc.name = "ICMB-CI-1"
        doc.status = "Approved"
        doc.issuing_company = SELLER
        doc.requesting_company = BUYER
        doc.sales_invoice = "SINV-CI-1"
        doc.purchase_invoice = "PINV-CI-1"
        return doc

    def test_partial_cancelled_pair_denied(self):
        doc = self.batch()
        with (
            patch(f"{BATCH}.frappe.db.get_value", side_effect=[
                frappe._dict(company=SELLER, docstatus=2),
                frappe._dict(company=BUYER, docstatus=1),
            ]),
            self.assertRaises(frappe.ValidationError),
        ):
            Batch._require_cancelled_invoice_pair(doc)

    def test_fully_cancelled_pair_allowed_without_rebilling(self):
        doc = self.batch()
        with patch(f"{BATCH}.frappe.db.get_value", side_effect=[
            frappe._dict(company=SELLER, docstatus=2),
            frappe._dict(company=BUYER, docstatus=2),
        ]):
            Batch._require_cancelled_invoice_pair(doc)

    def test_request_requires_human_reason_and_never_changes_links(self):
        doc = self.batch()
        doc._require_billing_preparer.return_value = PREPARER
        doc._locked_correction_case_name.return_value = None
        with patch(f"{BATCH}.create_correction_case",
                   return_value=frappe._dict(name="ICCR-CI-1", status="Requested")) as created:
            result = Batch.request_cancelled_invoice_correction(
                doc, "Original invoice pair cancelled after review."
            )
        self.assertFalse(result["automatic_rebilling"])
        self.assertEqual(result["status"], "Requested")
        self.assertEqual(doc.sales_invoice, "SINV-CI-1")
        self.assertEqual(doc.purchase_invoice, "PINV-CI-1")
        self.assertEqual(doc.status, "Approved")
        created.assert_called_once()
        doc._require_cancelled_invoice_pair.assert_called_once_with()

    def test_duplicate_correction_request_denied(self):
        doc = self.batch()
        doc._require_billing_preparer.return_value = PREPARER
        doc._locked_correction_case_name.return_value = "ICCR-CI-ORIGINAL"
        with (
            patch(f"{BATCH}.create_correction_case") as create,
            self.assertRaises(frappe.ValidationError),
        ):
            Batch.request_cancelled_invoice_correction(
                doc, "Do not create another correction for the same source."
            )
        create.assert_not_called()

    def test_reviewer_must_not_be_original_requester(self):
        doc = self.batch()
        doc._require_finance_approver.return_value = CHECKER
        doc._locked_correction_case_name.return_value = "ICCR-CI-1"
        case = frappe._dict(
            name="ICCR-CI-1", status="Requested", billing_batch=doc.name,
            issuing_company=SELLER, requesting_company=BUYER,
            sales_invoice=doc.sales_invoice, purchase_invoice=doc.purchase_invoice,
            requested_by=CHECKER,
        )
        with (
            patch(f"{BATCH}.frappe.get_doc", return_value=case),
            patch(f"{BATCH}.decide_correction_case") as decide,
            self.assertRaises(frappe.PermissionError),
        ):
            Batch.review_cancelled_invoice_correction(
                doc, "Approve", "Synthetic finance decision with an audit note."
            )
        decide.assert_not_called()

    def test_review_decision_is_audit_only(self):
        doc = self.batch()
        doc._require_finance_approver.return_value = CHECKER
        doc._locked_correction_case_name.return_value = "ICCR-CI-1"
        case = frappe._dict(
            name="ICCR-CI-1", status="Requested", billing_batch=doc.name,
            issuing_company=SELLER, requesting_company=BUYER,
            sales_invoice=doc.sales_invoice, purchase_invoice=doc.purchase_invoice,
            requested_by=PREPARER,
        )
        with (
            patch(f"{BATCH}.frappe.get_doc", return_value=case),
            patch(f"{BATCH}.decide_correction_case",
                  return_value=frappe._dict(name="ICCR-CI-1",
                                            status="Approved for Manual Correction")) as decide,
        ):
            result = Batch.review_cancelled_invoice_correction(
                doc, "Approve", "Accounting will review the correction manually."
            )
        self.assertFalse(result["automatic_rebilling"])
        self.assertEqual(doc.status, "Approved")
        self.assertEqual(doc.sales_invoice, "SINV-CI-1")
        self.assertEqual(doc.purchase_invoice, "PINV-CI-1")
        decide.assert_called_once()

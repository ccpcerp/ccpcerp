# Copyright (c) 2026, CCPC and contributors
# See license.txt

from datetime import datetime
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe
from erpnext.utilities.transaction_base import TransactionBase
from frappe.utils import getdate

from ccpcerp.ccpc_erp.doctype.intercompany_material_billing_batch.intercompany_material_billing_batch import (
    IntercompanyMaterialBillingBatch,
)


MODULE = (
    "ccpcerp.ccpc_erp.doctype.intercompany_material_billing_batch."
    "intercompany_material_billing_batch"
)


def raise_frappe_exception(message, exc=frappe.ValidationError, **kwargs):
    raise exc(message)


def billing_row(**values):
    defaults = {
        "idx": 1,
        "stock_entry": "MAT-STE-2026-00019",
        "stock_entry_detail": "SED-1",
        "withdrawal_slip": "WS-2026-00006",
        "withdrawal_slip_item": "WSI-1",
        "posting_date": getdate("2026-10-06"),
        "posting_time": "14:37:36",
        "item_code": "ITEM-00010",
        "item_name": "Test Item",
        "quantity": 1,
        "stock_uom": "Nos",
        "frozen_basic_rate": 10000,
        "frozen_basic_amount": 10000,
        "valuation_rate_snapshot": 10000,
        "amount_snapshot": 10000,
        "requesting_department": "Maintenance - RMC",
        "requesting_section": "Mechanical - RMC",
        "work_for_department": "Maintenance - RMC",
        "work_for_section": "Mechanical - RMC",
        "equipment": "EQ-1",
    }
    defaults.update(values)
    return frappe._dict(defaults)


def source_context(**overrides):
    detail = frappe._dict(
        parent="MAT-STE-2026-00019",
        parenttype="Stock Entry",
        item_code="ITEM-00010",
        item_name="Test Item",
        transfer_qty=1,
        stock_uom="Nos",
        basic_rate=10000,
        basic_amount=10000,
        valuation_rate=10000,
        amount=10000,
        withdrawal_slip_item="WSI-1",
    )
    stock_entry = frappe._dict(
        name="MAT-STE-2026-00019",
        docstatus=1,
        purpose="Material Issue",
        company="Cagayan Corn Products Corporation",
        posting_date=getdate("2026-10-06"),
        posting_time="14:37:36",
        withdrawal_slip="WS-2026-00006",
    )
    withdrawal_slip = frappe._dict(
        name="WS-2026-00006",
        company="Cagayan Corn Products Corporation",
        requesting_company="Ricor Mills Corporation",
        department="Maintenance - RMC",
        section="Mechanical - RMC",
        work_for_department="Maintenance - RMC",
        work_for_section="Mechanical - RMC",
        equipment="EQ-1",
    )

    for key, value in overrides.items():
        target, fieldname = key.split("__", 1)
        {
            "detail": detail,
            "stock_entry": stock_entry,
            "withdrawal_slip": withdrawal_slip,
        }[target][fieldname] = value

    return frappe._dict(
        detail=detail,
        stock_entry=stock_entry,
        withdrawal_slip=withdrawal_slip,
    )


def batch(**values):
    defaults = {
        "name": "ICMB-2026-00001",
        "issuing_company": "Cagayan Corn Products Corporation",
        "requesting_company": "Ricor Mills Corporation",
        "billing_month": getdate("2026-10-01"),
        "period_start": getdate("2026-10-01"),
        "period_end": getdate("2026-10-31"),
        "status": "Draft",
        "modified": None,
        "sales_invoice": None,
        "purchase_invoice": None,
        "approved_by": None,
        "approved_on": None,
        "last_refreshed_on": None,
        "finalized_on": None,
        "finalized_by": None,
        "items": [billing_row()],
        "line_count": 0,
        "total_amount": 0,
    }
    defaults.update(values)

    doc = MagicMock(spec=IntercompanyMaterialBillingBatch)
    doc.flags = frappe._dict(for_update=False)
    for fieldname, value in defaults.items():
        setattr(doc, fieldname, value)

    doc.get.side_effect = lambda fieldname: getattr(doc, fieldname)
    doc.meta.get_label.side_effect = lambda fieldname: fieldname
    doc._workflow_transition_is_authorized.side_effect = (
        lambda previous_status, current_status: IntercompanyMaterialBillingBatch._workflow_transition_is_authorized(
            doc, previous_status, current_status
        )
    )
    doc._throw_protected_approval_fields.side_effect = (
        lambda fieldnames: IntercompanyMaterialBillingBatch._throw_protected_approval_fields(
            doc, fieldnames
        )
    )
    doc._finalization_audit_update_is_authorized.side_effect = (
        lambda before: IntercompanyMaterialBillingBatch._finalization_audit_update_is_authorized(
            doc, before
        )
    )
    doc._throw_protected_finalization_audit_fields.side_effect = (
        lambda fieldnames: IntercompanyMaterialBillingBatch._throw_protected_finalization_audit_fields(
            doc, fieldnames
        )
    )
    doc._get_locked_deletion_state.side_effect = (
        lambda: IntercompanyMaterialBillingBatch._get_locked_deletion_state(doc)
    )
    return doc


class TestIntercompanyMaterialBillingBatch(TestCase):
    def setUp(self):
        # Standalone unittest does not bind Frappe's LocalProxy database.
        # Replace it only for this test and restore it on cleanup so
        # nested patch(...frappe.db.sql/get_value) works without a site.
        original_db = frappe.__dict__["db"]
        frappe.__dict__["db"] = MagicMock()
        self.addCleanup(frappe.__dict__.__setitem__, "db", original_db)

        translation = patch(f"{MODULE}._", side_effect=lambda value: value)
        translation.start()
        self.addCleanup(translation.stop)

        throw = patch(
            f"{MODULE}.frappe.throw",
            side_effect=raise_frappe_exception,
        )
        throw.start()
        self.addCleanup(throw.stop)

    @staticmethod
    def deletion_state(**values):
        state = {
            "status": "Draft",
            "docstatus": 0,
            "sales_invoice": None,
            "purchase_invoice": None,
            "approved_by": None,
            "approved_on": None,
            "last_refreshed_on": None,
            "finalized_on": None,
            "finalized_by": None,
        }
        state.update(values)
        return frappe._dict(state)

    def test_empty_unprocessed_draft_can_be_deleted(self):
        doc = batch(items=[])

        with patch(
            f"{MODULE}.frappe.db.sql",
            side_effect=[[self.deletion_state()], []],
        ) as sql:
            IntercompanyMaterialBillingBatch.on_trash(doc)

        self.assertEqual(sql.call_count, 2)
        parent_query = " ".join(sql.call_args_list[0].args[0].split()).upper()
        child_query = " ".join(sql.call_args_list[1].args[0].split()).upper()
        self.assertIn("WHERE NAME = %S", parent_query)
        self.assertIn("FOR UPDATE", parent_query)
        self.assertEqual(sql.call_args_list[0].args[1], (doc.name,))
        self.assertTrue(sql.call_args_list[0].kwargs["as_dict"])
        self.assertIn("WHERE PARENT = %(PARENT)S", child_query)
        self.assertIn("PARENTTYPE = %(PARENTTYPE)S", child_query)
        self.assertIn("PARENTFIELD = %(PARENTFIELD)S", child_query)
        self.assertIn("LIMIT 1 FOR UPDATE", child_query)
        self.assertEqual(
            sql.call_args_list[1].args[1],
            {
                "parent": doc.name,
                "parenttype": "Intercompany Material Billing Batch",
                "parentfield": "items",
            },
        )
        self.assertTrue(sql.call_args_list[1].kwargs["as_dict"])
        doc._acquire_billing_scope_lock.assert_not_called()
        doc._lock_and_reload_for_mutation.assert_not_called()

    def test_draft_with_billing_item_cannot_be_deleted(self):
        doc = batch(items=[])

        with (
            patch(
                f"{MODULE}.frappe.db.sql",
                side_effect=[
                    [self.deletion_state()],
                    [frappe._dict(name="ICMBI-1")],
                ],
            ),
            self.assertRaisesRegex(
                frappe.ValidationError,
                "Only an empty, unprocessed Draft",
            ),
        ):
            IntercompanyMaterialBillingBatch.on_trash(doc)

    def test_financial_fields_prevent_deletion(self):
        financial_values = {
            "sales_invoice": "SINV-2026-00001",
            "purchase_invoice": "PINV-2026-00001",
            "approved_by": "finance@example.com",
            "approved_on": "2026-11-01 09:00:00",
        }
        cases = [
            {fieldname: value}
            for fieldname, value in financial_values.items()
        ]
        cases.append(financial_values)

        for populated in cases:
            with (
                self.subTest(populated=tuple(populated)),
                patch(
                    f"{MODULE}.frappe.db.sql",
                    side_effect=[
                        [self.deletion_state(**populated)],
                        [],
                    ],
                ),
                self.assertRaisesRegex(
                    frappe.ValidationError,
                    "Only an empty, unprocessed Draft",
                ),
            ):
                IntercompanyMaterialBillingBatch.on_trash(batch(items=[]))

    def test_refresh_and_finalization_fields_prevent_deletion(self):
        for fieldname, value in {
            "last_refreshed_on": "2026-10-20 10:00:00",
            "finalized_on": "2026-11-01 08:00:00",
            "finalized_by": "finance@example.com",
        }.items():
            with (
                self.subTest(fieldname=fieldname),
                patch(
                    f"{MODULE}.frappe.db.sql",
                    side_effect=[
                        [self.deletion_state(**{fieldname: value})],
                        [],
                    ],
                ),
                self.assertRaisesRegex(
                    frappe.ValidationError,
                    "Only an empty, unprocessed Draft",
                ),
            ):
                IntercompanyMaterialBillingBatch.on_trash(batch(items=[]))

    def test_non_draft_custom_statuses_cannot_be_deleted(self):
        for status in ("Under Review", "Approved", "Invoiced", "Cancelled"):
            with (
                self.subTest(status=status),
                patch(
                    f"{MODULE}.frappe.db.sql",
                    side_effect=[
                        [self.deletion_state(status=status)],
                        [],
                    ],
                ),
                self.assertRaisesRegex(
                    frappe.ValidationError,
                    "Only an empty, unprocessed Draft",
                ),
            ):
                IntercompanyMaterialBillingBatch.on_trash(batch(items=[]))

    def test_nonzero_docstatus_prevents_deletion(self):
        with (
            patch(
                f"{MODULE}.frappe.db.sql",
                side_effect=[[self.deletion_state(docstatus=2)], []],
            ),
            self.assertRaisesRegex(
                frappe.ValidationError,
                "Only an empty, unprocessed Draft",
            ),
        ):
            IntercompanyMaterialBillingBatch.on_trash(batch(items=[]))

    def test_missing_persisted_state_prevents_deletion(self):
        doc = batch(items=[])

        with (
            patch(f"{MODULE}.frappe.db.sql", return_value=[]) as sql,
            self.assertRaisesRegex(
                frappe.ValidationError,
                "persisted billing batch state could not be verified",
            ),
        ):
            IntercompanyMaterialBillingBatch.on_trash(doc)

        sql.assert_called_once()

    def test_locked_persisted_state_is_deletion_authority(self):
        stale_draft = batch(status="Draft", items=[])
        with (
            patch(
                f"{MODULE}.frappe.db.sql",
                side_effect=[
                    [self.deletion_state(status="Approved")],
                    [],
                ],
            ),
            self.assertRaises(frappe.ValidationError),
        ):
            IntercompanyMaterialBillingBatch.on_trash(stale_draft)

        stale_processed = batch(
            status="Approved",
            sales_invoice="SINV-STALE",
            items=[billing_row()],
        )
        with patch(
            f"{MODULE}.frappe.db.sql",
            side_effect=[[self.deletion_state()], []],
        ):
            IntercompanyMaterialBillingBatch.on_trash(stale_processed)

    def test_ignore_permissions_flag_does_not_bypass_deletion_guard(self):
        doc = batch(items=[])
        doc.flags.ignore_permissions = True

        with (
            patch(
                f"{MODULE}.frappe.db.sql",
                side_effect=[
                    [self.deletion_state(status="Approved")],
                    [],
                ],
            ),
            self.assertRaises(frappe.ValidationError),
        ):
            IntercompanyMaterialBillingBatch.on_trash(doc)

    def test_deletion_locking_error_propagates(self):
        lock_error = RuntimeError("locking read failed")
        doc = batch(items=[])

        with patch(
            f"{MODULE}.frappe.db.sql",
            side_effect=lock_error,
        ):
            with self.assertRaises(RuntimeError) as raised:
                IntercompanyMaterialBillingBatch.on_trash(doc)

        self.assertIs(raised.exception, lock_error)

    def test_child_deletion_locking_error_propagates(self):
        lock_error = RuntimeError("child locking read failed")
        doc = batch(items=[])

        with patch(
            f"{MODULE}.frappe.db.sql",
            side_effect=[[self.deletion_state()], lock_error],
        ):
            with self.assertRaises(RuntimeError) as raised:
                IntercompanyMaterialBillingBatch.on_trash(doc)

        self.assertIs(raised.exception, lock_error)

    def test_only_none_and_exact_empty_string_are_empty_deletion_values(self):
        empty_values = {
            "sales_invoice": "",
            "purchase_invoice": None,
            "approved_by": "",
            "approved_on": None,
            "last_refreshed_on": "",
            "finalized_on": None,
            "finalized_by": "",
        }
        with patch(
            f"{MODULE}.frappe.db.sql",
            side_effect=[[self.deletion_state(**empty_values)], []],
        ):
            IntercompanyMaterialBillingBatch.on_trash(batch(items=[]))

        for value in (0, False, " ", "0", object()):
            with (
                self.subTest(value=value),
                patch(
                    f"{MODULE}.frappe.db.sql",
                    side_effect=[
                        [self.deletion_state(sales_invoice=value)],
                        [],
                    ],
                ),
                self.assertRaisesRegex(
                    frappe.ValidationError,
                    "Only an empty, unprocessed Draft",
                ),
            ):
                IntercompanyMaterialBillingBatch.on_trash(batch(items=[]))

    def test_standard_discard_is_rejected_for_every_billing_status(self):
        for status in ("Draft", "Under Review", "Approved", "Invoiced", "Cancelled"):
            with (
                self.subTest(status=status),
                self.assertRaisesRegex(
                    frappe.ValidationError,
                    "cannot use the standard Discard action",
                ),
            ):
                IntercompanyMaterialBillingBatch.before_discard(
                    batch(status=status)
                )

    def test_billing_month_is_normalized_to_calendar_month(self):
        doc = batch(
            billing_month=getdate("2026-10-17"),
            period_start=None,
            period_end=None,
        )

        IntercompanyMaterialBillingBatch._set_billing_period(doc)

        self.assertEqual(getdate(doc.billing_month), getdate("2026-10-01"))
        self.assertEqual(getdate(doc.period_start), getdate("2026-10-01"))
        self.assertEqual(getdate(doc.period_end), getdate("2026-10-31"))

    def test_current_month_cannot_be_approved(self):
        doc = batch(
            status="Approved",
            period_end=getdate("2026-10-31"),
        )

        with (
            patch(f"{MODULE}.today", return_value="2026-10-20"),
            self.assertRaises(frappe.ValidationError),
        ):
            IntercompanyMaterialBillingBatch._validate_month_end_approval(doc)

    def test_month_end_day_itself_cannot_be_approved(self):
        doc = batch(
            status="Approved",
            period_end=getdate("2026-10-31"),
        )

        with (
            patch(f"{MODULE}.today", return_value="2026-10-31"),
            self.assertRaises(frappe.ValidationError),
        ):
            IntercompanyMaterialBillingBatch._validate_month_end_approval(doc)

    def test_completed_month_can_be_approved(self):
        doc = batch(
            status="Approved",
            period_end=getdate("2026-10-31"),
        )

        with patch(f"{MODULE}.today", return_value="2026-11-01"):
            IntercompanyMaterialBillingBatch._validate_month_end_approval(doc)

    def test_draft_preview_is_allowed_during_current_month(self):
        doc = batch(
            status="Draft",
            period_end=getdate("2026-10-31"),
        )

        with patch(f"{MODULE}.today", return_value="2026-10-20"):
            IntercompanyMaterialBillingBatch._validate_month_end_approval(doc)

    def test_under_review_requires_final_refresh(self):
        doc = batch(
            status="Under Review",
            finalized_on=None,
            finalized_by=None,
        )

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_finalization_state(doc)

    def test_approved_requires_final_refresh(self):
        doc = batch(
            status="Approved",
            finalized_on=None,
            finalized_by=None,
        )

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_finalization_state(doc)

    def test_final_refresh_must_be_after_period_end(self):
        doc = batch(
            status="Under Review",
            finalized_on="2026-10-31 23:59:59",
            finalized_by="finance@example.com",
        )

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_finalization_state(doc)

    def test_finalized_by_is_required(self):
        doc = batch(
            status="Under Review",
            finalized_on="2026-11-01 08:00:00",
            finalized_by=None,
        )

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_finalization_state(doc)

    def test_post_month_end_final_refresh_allows_under_review(self):
        doc = batch(
            status="Under Review",
            finalized_on="2026-11-01 08:00:00",
            finalized_by="finance@example.com",
        )

        IntercompanyMaterialBillingBatch._validate_finalization_state(doc)

    def test_preview_refresh_does_not_count_as_finalization(self):
        doc = batch(
            status="Under Review",
            last_refreshed_on="2026-10-20 10:00:00",
            finalized_on=None,
            finalized_by=None,
        )

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_finalization_state(doc)

    def test_non_draft_batch_cannot_be_refreshed(self):
        doc = batch(status="Under Review")

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._require_draft_refresh(doc)

    def test_active_batch_claims_source_detail(self):
        matches = [
            frappe._dict(parent="ICMB-2026-00002")
        ]
        doc = batch()
        doc._get_active_source_claim.side_effect = (
            lambda stock_entry_detail: IntercompanyMaterialBillingBatch._get_active_source_claim(
                doc, stock_entry_detail
            )
        )

        with patch(f"{MODULE}.frappe.db.sql", return_value=matches):
            claimed = IntercompanyMaterialBillingBatch._source_row_is_claimed_elsewhere(
                doc,
                "SED-1",
            )

        self.assertTrue(claimed)

    def test_cancelled_batch_does_not_claim_source_detail(self):
        doc = batch()
        doc._get_active_source_claim.side_effect = (
            lambda stock_entry_detail: IntercompanyMaterialBillingBatch._get_active_source_claim(
                doc, stock_entry_detail
            )
        )

        with patch(f"{MODULE}.frappe.db.sql", return_value=[]):
            claimed = IntercompanyMaterialBillingBatch._source_row_is_claimed_elsewhere(
                doc,
                "SED-1",
            )

        self.assertFalse(claimed)

    def test_eligible_source_query_is_scoped_and_excludes_claimed_rows(self):
        rows = [
            frappe._dict(stock_entry_detail="SED-1"),
            frappe._dict(stock_entry_detail="SED-2"),
        ]
        doc = batch()
        doc._source_row_is_claimed_elsewhere.side_effect = [False, True]

        with patch(
            f"{MODULE}.frappe.db.sql",
            return_value=rows,
        ) as sql:
            result = IntercompanyMaterialBillingBatch._get_eligible_source_rows(doc)

        self.assertEqual(result, [rows[0]])

        params = sql.call_args.args[1]
        self.assertEqual(
            params["issuing_company"],
            "Cagayan Corn Products Corporation",
        )
        self.assertEqual(
            params["requesting_company"],
            "Ricor Mills Corporation",
        )
        self.assertEqual(
            getdate(params["period_start"]),
            getdate("2026-10-01"),
        )
        self.assertEqual(
            getdate(params["period_end"]),
            getdate("2026-10-31"),
        )
        self.assertTrue(sql.call_args.kwargs["as_dict"])

    def test_rebuild_items_maps_frozen_source_snapshot(self):
        source = frappe._dict(
            stock_entry="MAT-STE-2026-00019",
            posting_date=getdate("2026-10-06"),
            posting_time="14:37:36",
            stock_entry_detail="SED-1",
            withdrawal_slip_item="WSI-1",
            item_code="ITEM-00010",
            item_name="Test Item",
            transfer_qty=1,
            stock_uom="Nos",
            basic_rate=10000,
            basic_amount=10000,
            valuation_rate=10000,
            amount=10000,
            withdrawal_slip="WS-2026-00006",
            requesting_department="Maintenance - RMC",
            requesting_section="Mechanical - RMC",
            work_for_department="Maintenance - RMC",
            work_for_section="Mechanical - RMC",
            equipment="EQ-1",
        )

        doc = batch(items=[])
        doc._get_eligible_source_rows.return_value = [source]

        count = IntercompanyMaterialBillingBatch._rebuild_items_from_sources(doc)

        self.assertEqual(count, 1)
        doc.set.assert_called_once_with("items", [])
        doc.append.assert_called_once()

        child = doc.append.call_args.args[1]
        self.assertEqual(child["stock_entry"], "MAT-STE-2026-00019")
        self.assertEqual(child["stock_entry_detail"], "SED-1")
        self.assertEqual(child["withdrawal_slip"], "WS-2026-00006")
        self.assertEqual(child["withdrawal_slip_item"], "WSI-1")
        self.assertEqual(child["item_code"], "ITEM-00010")
        self.assertEqual(child["quantity"], 1)
        self.assertEqual(child["stock_uom"], "Nos")
        self.assertEqual(child["frozen_basic_rate"], 10000)
        self.assertEqual(child["frozen_basic_amount"], 10000)
        self.assertEqual(child["valuation_rate_snapshot"], 10000)
        self.assertEqual(child["amount_snapshot"], 10000)
        self.assertEqual(child["requesting_department"], "Maintenance - RMC")
        self.assertEqual(child["requesting_section"], "Mechanical - RMC")
        self.assertEqual(child["work_for_department"], "Maintenance - RMC")
        self.assertEqual(child["work_for_section"], "Mechanical - RMC")
        self.assertEqual(child["equipment"], "EQ-1")
        doc._set_totals.assert_called_once_with()

    def test_preview_refresh_rebuilds_and_clears_finalization(self):
        doc = batch(
            status="Draft",
            last_refreshed_on=None,
            finalized_on="2026-11-01 08:00:00",
            finalized_by="finance@example.com",
            total_amount=10000,
        )
        doc._rebuild_items_from_sources.return_value = 1

        with patch(
            f"{MODULE}.now_datetime",
            return_value="2026-10-20 10:00:00",
        ):
            result = IntercompanyMaterialBillingBatch.refresh_preview(doc)

        doc._require_billing_preparer.assert_called_once_with()
        doc._require_draft_refresh.assert_called_once_with()
        doc._set_billing_period.assert_called_once_with()
        doc._rebuild_items_from_sources.assert_called_once_with()
        doc._save_authorized_item_rebuild.assert_called_once_with()

        self.assertEqual(doc.last_refreshed_on, "2026-10-20 10:00:00")
        self.assertIsNone(doc.finalized_on)
        self.assertIsNone(doc.finalized_by)
        self.assertEqual(result["line_count"], 1)
        self.assertEqual(result["total_amount"], 10000)

    def test_final_refresh_is_blocked_until_month_has_ended(self):
        doc = batch(
            status="Draft",
            period_end=getdate("2026-10-31"),
        )

        with (
            patch(f"{MODULE}.today", return_value="2026-10-31"),
            self.assertRaises(frappe.ValidationError),
        ):
            IntercompanyMaterialBillingBatch.finalize_month(doc)

        doc._rebuild_items_from_sources.assert_not_called()
        doc._save_authorized_item_rebuild.assert_not_called()
        doc._save_authorized_workflow_transition.assert_not_called()

    def test_final_refresh_moves_batch_to_under_review(self):
        doc = batch(
            status="Draft",
            period_end=getdate("2026-10-31"),
            last_refreshed_on=None,
            finalized_on=None,
            finalized_by=None,
            total_amount=10000,
        )
        doc._rebuild_items_from_sources.return_value = 1

        with (
            patch(f"{MODULE}.today", return_value="2026-11-01"),
            patch(
                f"{MODULE}.now_datetime",
                return_value="2026-11-01 08:00:00",
            ),
            patch(
                f"{MODULE}.frappe.session",
                frappe._dict(user="finance@example.com"),
            ),
        ):
            result = IntercompanyMaterialBillingBatch.finalize_month(doc)

        self.assertEqual(doc.status, "Under Review")
        self.assertEqual(doc.last_refreshed_on, "2026-11-01 08:00:00")
        self.assertEqual(doc.finalized_on, "2026-11-01 08:00:00")
        self.assertEqual(doc.finalized_by, "finance@example.com")
        doc._rebuild_items_from_sources.assert_called_once_with()
        doc._save_authorized_workflow_transition.assert_called_once_with(
            "Draft",
            "Under Review",
            save_method=doc._save_authorized_item_rebuild,
        )

        self.assertEqual(result["status"], "Under Review")
        self.assertEqual(result["line_count"], 1)
        self.assertEqual(result["total_amount"], 10000)
        self.assertEqual(result["finalized_by"], "finance@example.com")


    def test_billing_scope_lock_uses_company_row_for_update(self):
        doc = batch()

        with patch(
            f"{MODULE}.frappe.db.sql",
            return_value=[
                frappe._dict(
                    name="Cagayan Corn Products Corporation"
                )
            ],
        ) as sql:
            result = (
                IntercompanyMaterialBillingBatch._acquire_billing_scope_lock(
                    doc,
                    "Cagayan Corn Products Corporation",
                )
            )

        self.assertEqual(
            result,
            "Cagayan Corn Products Corporation",
        )

        query = sql.call_args.args[0]
        params = sql.call_args.args[1]

        self.assertIn(
            "FOR UPDATE",
            " ".join(query.split()).upper(),
        )
        self.assertEqual(
            params,
            ("Cagayan Corn Products Corporation",),
        )
        self.assertTrue(sql.call_args.kwargs["as_dict"])

    def test_lock_and_reload_uses_canonical_issuing_company(self):
        doc = batch()
        doc.is_new.return_value = False

        with (
            patch(
                f"{MODULE}.frappe.db.get_value",
                return_value="Cagayan Corn Products Corporation",
            ) as get_value,
            patch(
                f"{MODULE}.frappe.db.sql",
                return_value=[
                    frappe._dict(
                        issuing_company=doc.issuing_company,
                        modified=doc.modified,
                        status=doc.status,
                        sales_invoice=doc.sales_invoice,
                        purchase_invoice=doc.purchase_invoice,
                    )
                ],
            ),
        ):
            IntercompanyMaterialBillingBatch._lock_and_reload_for_mutation(
                doc
            )

        get_value.assert_called_once_with(
            "Intercompany Material Billing Batch",
            "ICMB-2026-00001",
            "issuing_company",
        )
        doc._acquire_billing_scope_lock.assert_called_once_with(
            "Cagayan Corn Products Corporation"
        )
        doc.reload.assert_called_once_with()

        method_names = [
            call[0]
            for call in doc.method_calls
            if call[0] in {
                "_acquire_billing_scope_lock",
                "reload",
            }
        ]
        self.assertEqual(
            method_names,
            [
                "_acquire_billing_scope_lock",
                "reload",
            ],
        )

    def test_lock_and_reload_rechecks_permission_after_stable_issuer_reload(self):
        doc = batch()
        doc.is_new.return_value = False

        events = []

        doc._acquire_billing_scope_lock.side_effect = (
            lambda company: events.append(("lock", company))
        )
        doc.reload.side_effect = lambda: events.append(("reload", None))
        doc.check_permission.side_effect = (
            lambda permission: events.append(("permission", permission))
        )

        with (
            patch(
                f"{MODULE}.frappe.db.get_value",
                return_value="Cagayan Corn Products Corporation",
            ),
            patch(
                f"{MODULE}.frappe.db.sql",
                return_value=[
                    frappe._dict(
                        issuing_company=doc.issuing_company,
                        modified=doc.modified,
                        status=doc.status,
                        sales_invoice=doc.sales_invoice,
                        purchase_invoice=doc.purchase_invoice,
                    )
                ],
            ),
        ):
            IntercompanyMaterialBillingBatch._lock_and_reload_for_mutation(
                doc
            )

        self.assertEqual(
            events,
            [
                (
                    "lock",
                    "Cagayan Corn Products Corporation",
                ),
                ("reload", None),
                ("permission", "write"),
            ],
        )

    def test_lock_and_reload_enables_for_update_during_reload(self):
        doc = batch()
        doc.is_new.return_value = False
        doc.flags = frappe._dict(for_update=False)

        observed_flags = []
        doc.reload.side_effect = lambda: observed_flags.append(
            doc.flags.for_update
        )

        with (
            patch(
                f"{MODULE}.frappe.db.get_value",
                return_value="Cagayan Corn Products Corporation",
            ),
            patch(
                f"{MODULE}.frappe.db.sql",
                return_value=[
                    frappe._dict(
                        issuing_company=doc.issuing_company,
                        modified=doc.modified,
                        status=doc.status,
                        sales_invoice=doc.sales_invoice,
                        purchase_invoice=doc.purchase_invoice,
                    )
                ],
            ),
        ):
            IntercompanyMaterialBillingBatch._lock_and_reload_for_mutation(
                doc
            )

        self.assertEqual(observed_flags, [True])
        self.assertFalse(doc.flags.for_update)

    def test_lock_and_reload_restores_existing_for_update_flag(self):
        doc = batch()
        doc.is_new.return_value = False
        doc.flags = frappe._dict(for_update=True)

        with (
            patch(
                f"{MODULE}.frappe.db.get_value",
                return_value="Cagayan Corn Products Corporation",
            ),
            patch(
                f"{MODULE}.frappe.db.sql",
                return_value=[
                    frappe._dict(
                        issuing_company=doc.issuing_company,
                        modified=doc.modified,
                        status=doc.status,
                        sales_invoice=doc.sales_invoice,
                        purchase_invoice=doc.purchase_invoice,
                    )
                ],
            ),
        ):
            IntercompanyMaterialBillingBatch._lock_and_reload_for_mutation(
                doc
            )

        doc.reload.assert_called_once_with()
        self.assertTrue(doc.flags.for_update)

    def test_lock_and_reload_restores_for_update_if_reload_raises(self):
        doc = batch()
        doc.is_new.return_value = False
        doc.flags = frappe._dict(for_update=False)
        reload_error = RuntimeError("reload failed")

        def fail_reload():
            self.assertTrue(doc.flags.for_update)
            raise reload_error

        doc.reload.side_effect = fail_reload

        with (
            patch(
                f"{MODULE}.frappe.db.get_value",
                return_value="Cagayan Corn Products Corporation",
            ),
            patch(
                f"{MODULE}.frappe.db.sql",
                return_value=[
                    frappe._dict(
                        issuing_company=doc.issuing_company,
                        modified=doc.modified,
                        status=doc.status,
                        sales_invoice=doc.sales_invoice,
                        purchase_invoice=doc.purchase_invoice,
                    )
                ],
            ),
            self.assertRaises(RuntimeError) as raised,
        ):
            IntercompanyMaterialBillingBatch._lock_and_reload_for_mutation(
                doc
            )

        self.assertIs(raised.exception, reload_error)
        self.assertFalse(doc.flags.for_update)
        doc.check_permission.assert_not_called()

    def test_lock_and_reload_aborts_if_issuer_changes_during_reload(self):
        doc = batch()
        doc.is_new.return_value = False

        def reload_with_changed_issuer():
            doc.issuing_company = "Ricor Mills Corporation"

        doc.reload.side_effect = reload_with_changed_issuer

        with (
            patch(
                f"{MODULE}.frappe.db.get_value",
                return_value="Cagayan Corn Products Corporation",
            ),
            patch(
                f"{MODULE}.frappe.db.sql",
                return_value=[
                    frappe._dict(
                        issuing_company="Cagayan Corn Products Corporation",
                        modified=doc.modified,
                        status=doc.status,
                        sales_invoice=doc.sales_invoice,
                        purchase_invoice=doc.purchase_invoice,
                    )
                ],
            ),
            self.assertRaisesRegex(
                frappe.ValidationError,
                "changed in another transaction",
            ),
        ):
            IntercompanyMaterialBillingBatch._lock_and_reload_for_mutation(
                doc
            )

        doc._acquire_billing_scope_lock.assert_called_once_with(
            "Cagayan Corn Products Corporation"
        )
        doc.reload.assert_called_once_with()
        doc.check_permission.assert_not_called()

    def test_new_batch_validate_locks_before_uniqueness_and_item_claim_validation(self):
        doc = batch()
        events = []

        doc._validate_companies.side_effect = (
            lambda: events.append("companies")
        )
        doc._validate_billing_scope_immutability.side_effect = (
            lambda: events.append("scope")
        )
        doc._validate_status_transition.side_effect = (
            lambda: events.append("status")
        )
        doc._validate_finalization_audit_immutability.side_effect = (
            lambda: events.append("finalization_audit")
        )
        doc._validate_approval_fields_immutability.side_effect = (
            lambda: events.append("approval_fields")
        )
        doc._validate_source_membership_immutability.side_effect = (
            lambda: events.append("membership")
        )
        doc._source_membership_may_change.side_effect = (
            lambda: events.append("claim_mutation") or True
        )
        doc._acquire_billing_scope_lock.side_effect = (
            lambda: events.append("lock")
        )
        doc._validate_batch_uniqueness.side_effect = (
            lambda: events.append("uniqueness")
        )
        doc._validate_month_end_approval.side_effect = (
            lambda: events.append("month_end")
        )
        doc._validate_finalization_state.side_effect = (
            lambda: events.append("finalization")
        )
        doc._validate_items.side_effect = (
            lambda **kwargs: events.append(("items", kwargs))
        )
        doc._validate_nonempty_finalized_batch.side_effect = (
            lambda: events.append("nonempty")
        )
        doc._set_totals.side_effect = (
            lambda: events.append("totals")
        )

        IntercompanyMaterialBillingBatch.validate(doc)

        self.assertEqual(
            events,
            [
                "companies",
                "scope",
                "status",
                "finalization_audit",
                "approval_fields",
                "membership",
                "claim_mutation",
                "lock",
                "uniqueness",
                "month_end",
                "finalization",
                ("items", {"validate_source_claims": True}),
                "nonempty",
                "totals",
            ],
        )

    def test_ordinary_existing_save_skips_company_and_cross_batch_claim_locks(self):
        doc = batch()
        doc._source_membership_may_change.return_value = False

        IntercompanyMaterialBillingBatch.validate(doc)

        doc._acquire_billing_scope_lock.assert_not_called()
        doc._validate_items.assert_called_once_with(
            validate_source_claims=False
        )

    def test_ordinary_existing_save_still_validates_source_context_and_snapshot(self):
        doc = batch()
        source = source_context()
        doc._get_source_context.return_value = source

        IntercompanyMaterialBillingBatch._validate_items(
            doc,
            validate_source_claims=False,
        )

        doc._validate_not_billed_elsewhere.assert_not_called()
        doc._get_source_context.assert_called_once_with(doc.items[0])
        doc._validate_source_context.assert_called_once_with(
            doc.items[0], source
        )
        doc._validate_snapshot.assert_called_once_with(doc.items[0], source)

    def test_new_batch_source_membership_may_change(self):
        doc = batch()
        doc.is_new.return_value = True

        self.assertTrue(
            IntercompanyMaterialBillingBatch._source_membership_may_change(doc)
        )

    def test_api_supplied_rebuild_flag_does_not_enable_claim_mutation(self):
        doc = batch()
        doc.is_new.return_value = False
        doc.flags._intercompany_material_billing_item_rebuild = True

        self.assertFalse(
            IntercompanyMaterialBillingBatch._source_membership_may_change(doc)
        )

    def test_authorized_rebuild_save_retains_company_and_claim_checks(self):
        doc = batch()
        doc.is_new.return_value = False
        doc._source_membership_may_change.side_effect = (
            lambda: IntercompanyMaterialBillingBatch._source_membership_may_change(
                doc
            )
        )
        doc.save.side_effect = lambda: IntercompanyMaterialBillingBatch.validate(
            doc
        )

        IntercompanyMaterialBillingBatch._save_authorized_item_rebuild(doc)

        doc._acquire_billing_scope_lock.assert_called_once_with()
        doc._validate_items.assert_called_once_with(
            validate_source_claims=True
        )

    def test_missing_previous_state_fails_closed(self):
        doc = batch()
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = None
        doc._throw_unverified_saved_state.side_effect = (
            lambda: IntercompanyMaterialBillingBatch._throw_unverified_saved_state(
                doc
            )
        )

        for validator in (
            IntercompanyMaterialBillingBatch._validate_billing_scope_immutability,
            IntercompanyMaterialBillingBatch._validate_status_transition,
            IntercompanyMaterialBillingBatch._validate_finalization_audit_immutability,
            IntercompanyMaterialBillingBatch._validate_approval_fields_immutability,
            IntercompanyMaterialBillingBatch._validate_source_membership_immutability,
        ):
            with (
                self.subTest(validator=validator.__name__),
                self.assertRaisesRegex(
                    frappe.ValidationError,
                    "previous billing batch state could not be verified",
                ),
            ):
                validator(doc)

    def test_preview_refresh_reloads_before_draft_status_guard(self):
        doc = batch(
            status="Draft",
            finalized_on=None,
            finalized_by=None,
        )

        def lock_and_reload():
            doc.status = "Under Review"

        def require_draft():
            IntercompanyMaterialBillingBatch._require_draft_refresh(doc)

        doc._lock_and_reload_for_mutation.side_effect = lock_and_reload
        doc._require_draft_refresh.side_effect = require_draft

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch.refresh_preview(doc)

        doc._lock_and_reload_for_mutation.assert_called_once_with()
        doc._rebuild_items_from_sources.assert_not_called()
        doc._save_authorized_item_rebuild.assert_not_called()

    def test_final_refresh_reloads_before_draft_status_guard(self):
        doc = batch(
            status="Draft",
            finalized_on=None,
            finalized_by=None,
        )

        def lock_and_reload():
            doc.status = "Under Review"

        def require_draft():
            IntercompanyMaterialBillingBatch._require_draft_refresh(doc)

        doc._lock_and_reload_for_mutation.side_effect = lock_and_reload
        doc._require_draft_refresh.side_effect = require_draft

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch.finalize_month(doc)

        doc._lock_and_reload_for_mutation.assert_called_once_with()
        doc._rebuild_items_from_sources.assert_not_called()
        doc._save_authorized_item_rebuild.assert_not_called()

    def test_approval_reloads_before_status_guard_and_creates_no_duplicate_invoices(self):
        doc = batch(
            status="Under Review",
            sales_invoice=None,
            purchase_invoice=None,
        )

        def lock_and_reload():
            doc.status = "Approved"

        def require_under_review():
            IntercompanyMaterialBillingBatch._require_under_review_for_approval(
                doc
            )

        doc._lock_and_reload_for_mutation.side_effect = lock_and_reload
        doc._require_under_review_for_approval.side_effect = (
            require_under_review
        )

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch.approve_and_create_invoices(
                doc
            )

        doc._lock_and_reload_for_mutation.assert_called_once_with()
        doc._build_sales_invoice.assert_not_called()
        doc._build_purchase_invoice.assert_not_called()
        doc.save.assert_not_called()

    def test_finance_approver_requires_named_company_scoped_checker(self):
        doc = batch(status="Under Review")
        doc.finalized_by = "billing-preparer@example.com"
        with (
            patch(f"{MODULE}.frappe.session", frappe._dict(user="checker@example.com")),
            patch(f"{MODULE}.require_billing_actor", return_value="checker@example.com") as actor,
            patch(f"{MODULE}.require_distinct_billing_review") as independent,
        ):
            result = IntercompanyMaterialBillingBatch._require_finance_approver(doc)
        self.assertEqual(result, "checker@example.com")
        doc.check_permission.assert_called_once_with("write")
        actor.assert_called_once_with(
            "Finance Controller", doc.issuing_company, doc.requesting_company,
        )
        independent.assert_called_once_with(
            "billing-preparer@example.com", "checker@example.com",
        )

    def test_non_finance_user_cannot_approve(self):
        doc = batch(status="Under Review")
        with (
            patch(f"{MODULE}.require_billing_actor",
                  side_effect=frappe.PermissionError("explicit Finance checker role required")),
            self.assertRaises(frappe.PermissionError),
        ):
            IntercompanyMaterialBillingBatch._require_finance_approver(doc)
        doc.check_permission.assert_called_once_with("write")

    def test_approval_requires_under_review_status(self):
        doc = batch(
            status="Draft",
            sales_invoice=None,
            purchase_invoice=None,
        )

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._require_under_review_for_approval(
                doc
            )

    def test_approval_rejects_existing_invoice_references(self):
        for sales_invoice, purchase_invoice in (
            ("SINV-1", None),
            (None, "PINV-1"),
        ):
            with self.subTest(
                sales_invoice=sales_invoice,
                purchase_invoice=purchase_invoice,
            ):
                doc = batch(
                    status="Under Review",
                    sales_invoice=sales_invoice,
                    purchase_invoice=purchase_invoice,
                )

                with self.assertRaises(frappe.ValidationError):
                    IntercompanyMaterialBillingBatch._require_under_review_for_approval(
                        doc
                    )

    def test_internal_party_requires_exactly_one_enabled_match(self):
        doc = batch()

        with patch(
            f"{MODULE}.frappe.get_all",
            return_value=["Ricor Mills Corporation"],
        ) as get_all:
            party = IntercompanyMaterialBillingBatch._get_internal_party(
                doc,
                "Customer",
                "Ricor Mills Corporation",
            )

        self.assertEqual(party, "Ricor Mills Corporation")
        get_all.assert_called_once_with(
            "Customer",
            filters={
                "is_internal_customer": 1,
                "represents_company": "Ricor Mills Corporation",
                "disabled": 0,
            },
            pluck="name",
        )

        for matches in ([], ["PARTY-1", "PARTY-2"]):
            with (
                self.subTest(matches=matches),
                patch(
                    f"{MODULE}.frappe.get_all",
                    return_value=matches,
                ),
                self.assertRaises(frappe.ValidationError),
            ):
                IntercompanyMaterialBillingBatch._get_internal_party(
                    doc,
                    "Customer",
                    "Ricor Mills Corporation",
                )

    def test_company_invoice_defaults_use_dedicated_accounts(self):
        doc = batch()

        company_info = frappe._dict(
            abbr="RMC",
            default_currency="PHP",
            cost_center="Main - RMC",
        )
        cost_center = frappe._dict(
            company="Ricor Mills Corporation",
            is_group=0,
            disabled=0,
        )

        with patch(
            f"{MODULE}.frappe.db.get_value",
            side_effect=[company_info, cost_center],
        ):
            defaults = (
                IntercompanyMaterialBillingBatch._get_company_invoice_defaults(
                    doc,
                    "Ricor Mills Corporation",
                )
            )

        self.assertEqual(defaults.currency, "PHP")
        self.assertEqual(defaults.cost_center, "Main - RMC")
        self.assertEqual(
            defaults.receivable_account,
            "1312 - Intercompany Receivable - RMC",
        )
        self.assertEqual(
            defaults.payable_account,
            "2121 - Intercompany Payable - RMC",
        )
        self.assertEqual(
            defaults.expense_account,
            "5150 - Intercompany Materials Expense - RMC",
        )
        self.assertEqual(
            defaults.income_account,
            "6050 - Intercompany Material Charges - RMC",
        )

        self.assertEqual(doc._validate_invoice_account.call_count, 4)

    def test_sales_invoice_builder_uses_frozen_values_without_stock_or_taxes(self):
        doc = batch(
            status="Under Review",
            total_amount=10000,
        )

        defaults = frappe._dict(
            currency="PHP",
            cost_center="Main - CCPC",
            receivable_account="1312 - Intercompany Receivable - CCPC",
            income_account="6050 - Intercompany Material Charges - CCPC",
        )

        invoice = MagicMock()

        with patch(
            f"{MODULE}.frappe.get_doc",
            return_value=invoice,
        ) as get_doc:
            result = IntercompanyMaterialBillingBatch._build_sales_invoice(
                doc,
                "Ricor Mills Corporation",
                defaults,
            )

        self.assertIs(result, invoice)

        values = get_doc.call_args.args[0]
        self.assertEqual(values["doctype"], "Sales Invoice")
        self.assertEqual(
            values["company"],
            "Cagayan Corn Products Corporation",
        )
        self.assertEqual(values["customer"], "Ricor Mills Corporation")
        self.assertEqual(
            getdate(values["posting_date"]),
            getdate("2026-10-31"),
        )
        # Native ERPNext otherwise resets Draft posting_date to Today.
        self.assertEqual(values["set_posting_time"], 1)
        self.assertEqual(
            values["debit_to"],
            "1312 - Intercompany Receivable - CCPC",
        )
        self.assertEqual(values["update_stock"], 0)
        self.assertIsNone(values["taxes_and_charges"])
        self.assertEqual(values["taxes"], [])

        self.assertEqual(len(values["items"]), 1)
        item = values["items"][0]
        self.assertEqual(item["item_code"], "ITEM-00010")
        self.assertEqual(item["qty"], 1)
        self.assertEqual(item["uom"], "Nos")
        self.assertEqual(item["rate"], 10000)
        self.assertEqual(
            item["income_account"],
            "6050 - Intercompany Material Charges - CCPC",
        )
        self.assertEqual(item["cost_center"], "Main - CCPC")
        self.assertNotIn("department", item)
        self.assertNotIn("equipment", item)
        self.assertNotIn("warehouse", item)

    def test_purchase_invoice_builder_uses_frozen_values_and_requesting_dimensions(self):
        doc = batch(
            status="Under Review",
            total_amount=10000,
        )

        defaults = frappe._dict(
            currency="PHP",
            cost_center="Main - RMC",
            payable_account="2121 - Intercompany Payable - RMC",
            expense_account="5150 - Intercompany Materials Expense - RMC",
        )

        invoice = MagicMock()

        with patch(
            f"{MODULE}.frappe.get_doc",
            return_value=invoice,
        ) as get_doc:
            result = IntercompanyMaterialBillingBatch._build_purchase_invoice(
                doc,
                "Cagayan Corn Products Corporation",
                defaults,
                "SINV-2026-00001",
            )

        self.assertIs(result, invoice)

        values = get_doc.call_args.args[0]
        self.assertEqual(values["doctype"], "Purchase Invoice")
        self.assertEqual(
            values["company"],
            "Ricor Mills Corporation",
        )
        self.assertEqual(
            values["supplier"],
            "Cagayan Corn Products Corporation",
        )
        self.assertEqual(
            getdate(values["posting_date"]),
            getdate("2026-10-31"),
        )
        # Native ERPNext otherwise resets Draft posting_date to Today.
        self.assertEqual(values["set_posting_time"], 1)
        self.assertEqual(
            values["credit_to"],
            "2121 - Intercompany Payable - RMC",
        )
        self.assertEqual(values["update_stock"], 0)
        self.assertIsNone(values["taxes_and_charges"])
        self.assertEqual(values["taxes"], [])
        self.assertEqual(
            values["inter_company_invoice_reference"],
            "SINV-2026-00001",
        )

        self.assertEqual(len(values["items"]), 1)
        item = values["items"][0]
        self.assertEqual(item["item_code"], "ITEM-00010")
        self.assertEqual(item["qty"], 1)
        self.assertEqual(item["uom"], "Nos")
        self.assertEqual(item["rate"], 10000)
        self.assertEqual(
            item["expense_account"],
            "5150 - Intercompany Materials Expense - RMC",
        )
        self.assertEqual(item["cost_center"], "Main - RMC")
        self.assertEqual(item["department"], "Maintenance - RMC")
        self.assertEqual(item["equipment"], "EQ-1")
        self.assertNotIn("warehouse", item)

    def test_native_posting_time_validation_retains_both_invoice_month_end_dates(self):
        # Exercise the real ERPNext date validator, not just the mocked
        # frappe.get_doc input. This catches its default-to-Today behavior.
        doc = batch(status="Under Review")
        issuer = frappe._dict(
            currency="PHP",
            cost_center="Main - CCPC",
            receivable_account="1312 - Intercompany Receivable - CCPC",
            income_account="6050 - Intercompany Material Charges - CCPC",
        )
        buyer = frappe._dict(
            currency="PHP",
            cost_center="Main - RMC",
            payable_account="2121 - Intercompany Payable - RMC",
            expense_account="5150 - Intercompany Materials Expense - RMC",
        )
        with patch(f"{MODULE}.frappe.get_doc", side_effect=lambda data: frappe._dict(data)):
            invoices = (
                IntercompanyMaterialBillingBatch._build_sales_invoice(
                    doc, "Ricor Mills Corporation", issuer
                ),
                IntercompanyMaterialBillingBatch._build_purchase_invoice(
                    doc, "Cagayan Corn Products Corporation", buyer, "SINV-2026-00001"
                ),
            )

        with (
            patch(
                "erpnext.utilities.transaction_base.frappe.flags",
                frappe._dict(in_import=False),
            ),
            patch(
                "erpnext.utilities.transaction_base.now_datetime",
                return_value=datetime(2026, 11, 4, 9, 0, 0),
            ),
        ):
            for invoice in invoices:
                with self.subTest(doctype=invoice.doctype):
                    self.assertEqual(invoice.set_posting_time, 1)
                    invoice.flags = frappe._dict(from_restore=False)
                    invoice.posting_time = "12:00:00"
                    TransactionBase.validate_posting_time(invoice)
                    self.assertEqual(getdate(invoice.posting_date), getdate("2026-10-31"))

                    # Negative control demonstrates the native overwrite risk.
                    unprotected = frappe._dict(invoice.copy())
                    unprotected.set_posting_time = 0
                    TransactionBase.validate_posting_time(unprotected)
                    self.assertEqual(
                        getdate(unprotected.posting_date), getdate("2026-11-04")
                    )

    def test_generated_invoice_guard_rejects_invalid_generated_document(self):
        doc = batch(total_amount=10000)

        invoice = frappe._dict(
            doctype="Sales Invoice",
            name="SINV-1",
            docstatus=0,
            update_stock=0,
            taxes=[],
            posting_date=getdate("2026-10-31"),
            grand_total=10000,
        )

        IntercompanyMaterialBillingBatch._validate_generated_invoice(
            doc,
            invoice,
        )

        cases = (
            ("submitted", {"docstatus": 1}),
            ("stock movement", {"update_stock": 1}),
            ("automatic taxes", {"taxes": [frappe._dict(charge_type="On Net Total")]}),
            ("posting date changed to Today", {"posting_date": getdate("2026-11-04")}),
            ("missing posting date", {"posting_date": None}),
            ("wrong total", {"grand_total": 9999}),
        )

        for label, changes in cases:
            with self.subTest(label=label):
                candidate = frappe._dict(invoice.copy())
                candidate.update(changes)

                with self.assertRaises(frappe.ValidationError):
                    IntercompanyMaterialBillingBatch._validate_generated_invoice(
                        doc,
                        candidate,
                    )

    def test_draft_invoice_pair_rejects_missing_or_unsaved_reciprocal_links(self):
        doc = batch()
        sales_invoice = frappe._dict(
            name="SINV-2026-00001",
            inter_company_invoice_reference="PINV-2026-00001",
        )
        purchase_invoice = frappe._dict(
            name="PINV-2026-00001",
            inter_company_invoice_reference="SINV-2026-00001",
        )

        expected = {
            "Sales Invoice": purchase_invoice.name,
            "Purchase Invoice": sales_invoice.name,
        }
        with patch(
            f"{MODULE}.frappe.db.get_value",
            side_effect=lambda dt, name, field: expected[dt],
        ) as stored:
            IntercompanyMaterialBillingBatch._validate_draft_invoice_pair(
                doc, sales_invoice, purchase_invoice
            )
            self.assertEqual(stored.call_count, 2)

        for label, changed in (
            ("unsaved SI reference", {"si_ref": None}),
            ("unsaved PI reference", {"pi_ref": None}),
            ("persisted SI reference missing", {"stored_si_ref": None}),
            ("persisted PI reference missing", {"stored_pi_ref": None}),
        ):
            with self.subTest(label=label):
                si = frappe._dict(sales_invoice.copy())
                pi = frappe._dict(purchase_invoice.copy())
                si.inter_company_invoice_reference = changed.get(
                    "si_ref", purchase_invoice.name
                )
                pi.inter_company_invoice_reference = changed.get(
                    "pi_ref", sales_invoice.name
                )
                stored_refs = {
                    "Sales Invoice": changed.get("stored_si_ref", purchase_invoice.name),
                    "Purchase Invoice": changed.get("stored_pi_ref", sales_invoice.name),
                }
                with (
                    patch(
                        f"{MODULE}.frappe.db.get_value",
                        side_effect=lambda dt, name, field: stored_refs[dt],
                    ),
                    self.assertRaisesRegex(
                        frappe.ValidationError, "must reference each other"
                    ),
                ):
                    IntercompanyMaterialBillingBatch._validate_draft_invoice_pair(
                        doc, si, pi
                    )

    def test_approval_creates_cross_linked_draft_invoices_and_marks_batch_approved(self):
        doc = batch(
            status="Under Review",
            finalized_on="2026-11-01 08:00:00",
            finalized_by="finance@example.com",
            total_amount=10000,
            sales_invoice=None,
            purchase_invoice=None,
            approved_by=None,
            approved_on=None,
        )

        issuing_defaults = frappe._dict(
            currency="PHP",
            cost_center="Main - CCPC",
            receivable_account="1312 - Intercompany Receivable - CCPC",
            income_account="6050 - Intercompany Material Charges - CCPC",
        )
        requesting_defaults = frappe._dict(
            currency="PHP",
            cost_center="Main - RMC",
            payable_account="2121 - Intercompany Payable - RMC",
            expense_account="5150 - Intercompany Materials Expense - RMC",
        )

        doc._get_company_invoice_defaults.side_effect = [
            issuing_defaults,
            requesting_defaults,
        ]
        doc._get_internal_party.side_effect = [
            "Ricor Mills Corporation",
            "Cagayan Corn Products Corporation",
        ]

        sales_invoice = MagicMock()
        sales_invoice.name = "SINV-2026-00001"

        purchase_invoice = MagicMock()
        purchase_invoice.name = "PINV-2026-00001"

        doc._build_sales_invoice.return_value = sales_invoice
        doc._build_purchase_invoice.return_value = purchase_invoice

        with (
            patch(
                f"{MODULE}.frappe.session",
                frappe._dict(user="finance@example.com"),
            ),
            patch(
                f"{MODULE}.now_datetime",
                return_value="2026-11-01 09:00:00",
            ),
        ):
            result = (
                IntercompanyMaterialBillingBatch.approve_and_create_invoices(
                    doc
                )
            )

        doc._require_finance_approver.assert_called_once_with()
        doc._require_under_review_for_approval.assert_called_once_with()
        doc._validate_finalization_state.assert_called_once_with()
        doc._validate_items.assert_called_once_with()
        doc._validate_nonempty_finalized_batch.assert_called_once_with()
        doc._set_totals.assert_called_once_with()

        self.assertEqual(
            doc._get_company_invoice_defaults.call_args_list[0].args,
            ("Cagayan Corn Products Corporation",),
        )
        self.assertEqual(
            doc._get_company_invoice_defaults.call_args_list[1].args,
            ("Ricor Mills Corporation",),
        )

        doc._build_sales_invoice.assert_called_once_with(
            "Ricor Mills Corporation",
            issuing_defaults,
        )
        doc._build_purchase_invoice.assert_called_once_with(
            "Cagayan Corn Products Corporation",
            requesting_defaults,
            "SINV-2026-00001",
        )

        sales_invoice.insert.assert_called_once_with(
            ignore_permissions=True
        )
        purchase_invoice.insert.assert_called_once_with(
            ignore_permissions=True
        )

        self.assertEqual(
            sales_invoice.inter_company_invoice_reference,
            "PINV-2026-00001",
        )
        sales_invoice.save.assert_called_once_with(
            ignore_permissions=True
        )
        doc._validate_draft_invoice_pair.assert_called_once_with(
            sales_invoice, purchase_invoice
        )

        self.assertEqual(doc.status, "Approved")
        self.assertEqual(doc.approved_by, "finance@example.com")
        self.assertEqual(doc.approved_on, "2026-11-01 09:00:00")
        self.assertEqual(doc.sales_invoice, "SINV-2026-00001")
        self.assertEqual(doc.purchase_invoice, "PINV-2026-00001")
        doc._save_authorized_workflow_transition.assert_called_once_with(
            "Under Review",
            "Approved",
        )
        doc.save.assert_not_called()

        self.assertEqual(result["status"], "Approved")
        self.assertEqual(
            result["sales_invoice"],
            "SINV-2026-00001",
        )
        self.assertEqual(
            result["purchase_invoice"],
            "PINV-2026-00001",
        )
        self.assertEqual(
            getdate(result["posting_date"]),
            getdate("2026-10-31"),
        )
        self.assertEqual(result["total_amount"], 10000)

    def test_draft_pair_link_failure_rolls_back_without_approving_batch(self):
        doc = batch(
            status="Under Review",
            finalized_by="maker@example.com",
            finalized_on="2026-11-01 08:00:00",
            approved_by=None,
            approved_on=None,
            sales_invoice=None,
            purchase_invoice=None,
        )
        doc._get_company_invoice_defaults.side_effect = [
            frappe._dict(currency="PHP"),
            frappe._dict(currency="PHP"),
        ]
        doc._get_internal_party.side_effect = ["Internal Customer", "Internal Supplier"]
        sales_invoice = MagicMock(name="Sales Invoice")
        sales_invoice.name = "SINV-2026-00001"
        purchase_invoice = MagicMock(name="Purchase Invoice")
        purchase_invoice.name = "PINV-2026-00001"
        doc._build_sales_invoice.return_value = sales_invoice
        doc._build_purchase_invoice.return_value = purchase_invoice
        doc._validate_draft_invoice_pair.side_effect = frappe.ValidationError(
            "Both Draft intercompany invoices must reference each other before approval."
        )

        with (
            patch(f"{MODULE}.frappe.session", frappe._dict(user="checker@example.com")),
            self.assertRaisesRegex(
                frappe.ValidationError, "must reference each other"
            ),
        ):
            IntercompanyMaterialBillingBatch.approve_and_create_invoices(doc)

        frappe.db.savepoint.assert_called_once()
        self.assertTrue(frappe.db.rollback.called)
        self.assertEqual(
            frappe.db.rollback.call_args.kwargs,
            {"save_point": frappe.db.savepoint.call_args.args[0]},
        )
        self.assertEqual(doc.status, "Under Review")
        self.assertIsNone(doc.sales_invoice)
        self.assertIsNone(doc.purchase_invoice)
        self.assertIsNone(doc.approved_by)
        self.assertIsNone(doc.approved_on)
        doc._save_authorized_workflow_transition.assert_not_called()

    def test_empty_draft_batch_is_allowed(self):
        doc = batch(status="Draft", items=[])

        IntercompanyMaterialBillingBatch._validate_nonempty_finalized_batch(doc)

    def test_empty_under_review_batch_is_rejected(self):
        doc = batch(status="Under Review", items=[])

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_nonempty_finalized_batch(doc)

    def test_same_company_is_rejected(self):
        doc = batch(
            issuing_company="Cagayan Corn Products Corporation",
            requesting_company="Cagayan Corn Products Corporation",
        )

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_companies(doc)

    def test_each_billing_scope_field_is_immutable_after_insert(self):
        before = frappe._dict(
            issuing_company="Cagayan Corn Products Corporation",
            requesting_company="Ricor Mills Corporation",
            billing_month=getdate("2026-10-01"),
        )

        changes = {
            "issuing_company": "GSL Realty Corporation",
            "requesting_company": "Woodland Farms Corporation",
            "billing_month": getdate("2026-11-01"),
        }

        for fieldname, value in changes.items():
            with self.subTest(fieldname=fieldname):
                doc = batch(**{fieldname: value})
                doc.is_new.return_value = False
                doc.get_doc_before_save.return_value = before

                with self.assertRaisesRegex(
                    frappe.ValidationError,
                    "cannot be changed after the billing batch is created",
                ):
                    IntercompanyMaterialBillingBatch._validate_billing_scope_immutability(
                        doc
                    )

    def test_unchanged_billing_scope_is_allowed_after_insert(self):
        doc = batch()
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            issuing_company=doc.issuing_company,
            requesting_company=doc.requesting_company,
            billing_month=doc.billing_month,
        )

        IntercompanyMaterialBillingBatch._validate_billing_scope_immutability(
            doc
        )

    def test_equivalent_billing_month_values_are_allowed_after_insert(self):
        doc = batch(billing_month=getdate("2026-10-01"))
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            issuing_company=doc.issuing_company,
            requesting_company=doc.requesting_company,
            billing_month="2026-10-01",
        )

        IntercompanyMaterialBillingBatch._validate_billing_scope_immutability(
            doc
        )

    def test_ordinary_status_transition_matrix(self):
        statuses = ("Draft", "Under Review", "Approved", "Invoiced", "Cancelled")
        allowed = {
            ("Draft", "Draft"),
            ("Draft", "Cancelled"),
            ("Under Review", "Under Review"),
            ("Under Review", "Cancelled"),
            ("Approved", "Approved"),
            ("Invoiced", "Invoiced"),
            ("Cancelled", "Cancelled"),
        }

        for previous_status in statuses:
            for current_status in statuses:
                with self.subTest(
                    previous_status=previous_status,
                    current_status=current_status,
                ):
                    doc = batch(status=current_status)
                    doc.is_new.return_value = False
                    doc.get_doc_before_save.return_value = frappe._dict(
                        status=previous_status
                    )

                    if (previous_status, current_status) in allowed:
                        IntercompanyMaterialBillingBatch._validate_status_transition(
                            doc
                        )
                    else:
                        with self.assertRaises(frappe.ValidationError):
                            IntercompanyMaterialBillingBatch._validate_status_transition(
                                doc
                            )

    def test_approved_and_invoiced_batches_cannot_be_cancelled(self):
        for previous_status in ("Approved", "Invoiced"):
            with self.subTest(previous_status=previous_status):
                doc = batch(status="Cancelled")
                doc.is_new.return_value = False
                doc.get_doc_before_save.return_value = frappe._dict(
                    status=previous_status
                )

                with self.assertRaisesRegex(
                    frappe.ValidationError,
                    "status cannot be changed",
                ):
                    IntercompanyMaterialBillingBatch._validate_status_transition(
                        doc
                    )

    def test_preapproval_batches_without_financial_fields_can_be_cancelled(self):
        for previous_status in ("Draft", "Under Review"):
            with self.subTest(previous_status=previous_status):
                doc = batch(status="Cancelled")
                doc.is_new.return_value = False
                doc.get_doc_before_save.return_value = frappe._dict(
                    status=previous_status,
                    sales_invoice=None,
                    purchase_invoice=None,
                    approved_by=None,
                    approved_on=None,
                )

                IntercompanyMaterialBillingBatch._validate_status_transition(doc)

    def test_financial_fields_prevent_preapproval_cancellation(self):
        financial_values = {
            "sales_invoice": "SINV-2026-00001",
            "purchase_invoice": "PINV-2026-00001",
            "approved_by": "finance@example.com",
            "approved_on": "2026-11-01 09:00:00",
        }
        cases = [
            {fieldname: value}
            for fieldname, value in financial_values.items()
        ]
        cases.append(financial_values)

        for previous_status in ("Draft", "Under Review"):
            for populated in cases:
                with self.subTest(
                    previous_status=previous_status,
                    populated=tuple(populated),
                ):
                    current_values = {
                        fieldname: None
                        for fieldname in financial_values
                    }
                    current_values.update(populated)
                    doc = batch(status="Cancelled", **current_values)
                    doc.is_new.return_value = False
                    doc.get_doc_before_save.return_value = frappe._dict(
                        status=previous_status,
                        **current_values,
                    )

                    with self.assertRaisesRegex(
                        frappe.ValidationError,
                        "cannot be cancelled",
                    ):
                        IntercompanyMaterialBillingBatch._validate_status_transition(
                            doc
                        )

    def test_clearing_saved_financial_field_while_cancelling_is_rejected(self):
        for fieldname, saved_value in {
            "sales_invoice": "SINV-2026-00001",
            "purchase_invoice": "PINV-2026-00001",
            "approved_by": "finance@example.com",
            "approved_on": "2026-11-01 09:00:00",
        }.items():
            with self.subTest(fieldname=fieldname):
                doc = batch(status="Cancelled", **{fieldname: None})
                doc.is_new.return_value = False
                saved_values = {
                    "sales_invoice": None,
                    "purchase_invoice": None,
                    "approved_by": None,
                    "approved_on": None,
                }
                saved_values[fieldname] = saved_value
                doc.get_doc_before_save.return_value = frappe._dict(
                    status="Under Review",
                    **saved_values,
                )

                with self.assertRaisesRegex(
                    frappe.ValidationError,
                    "cannot be cancelled",
                ):
                    IntercompanyMaterialBillingBatch._validate_status_transition(
                        doc
                    )

    def test_new_draft_batch_status_is_allowed(self):
        doc = batch(status="Draft")
        doc.is_new.return_value = True

        IntercompanyMaterialBillingBatch._validate_status_transition(doc)

        doc.get_doc_before_save.assert_not_called()

    def test_new_batch_requires_draft_status(self):
        for status in (
            "Under Review",
            "Approved",
            "Invoiced",
            "Cancelled",
            None,
            "",
            "Invalid Status",
        ):
            with self.subTest(status=status):
                doc = batch(status=status)
                doc.is_new.return_value = True

                with self.assertRaisesRegex(
                    frappe.ValidationError,
                    "must start in Draft status",
                ):
                    IntercompanyMaterialBillingBatch._validate_status_transition(
                        doc
                    )

                doc.get_doc_before_save.assert_not_called()

    def test_api_supplied_workflow_authorization_is_rejected(self):
        doc = batch(status="Under Review")
        doc.is_new.return_value = False
        doc.flags._intercompany_material_billing_workflow_transition = (
            True,
            "Draft",
            "Under Review",
        )
        doc.get_doc_before_save.return_value = frappe._dict(status="Draft")

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_status_transition(doc)

    def test_new_draft_requires_empty_finalization_audit_fields(self):
        IntercompanyMaterialBillingBatch._validate_finalization_audit_immutability(
            batch()
        )

        for fieldname, value in {
            "last_refreshed_on": "2026-10-20 10:00:00",
            "finalized_on": "2026-11-01 08:00:00",
            "finalized_by": "finance@example.com",
        }.items():
            with self.subTest(fieldname=fieldname):
                doc = batch(**{fieldname: value})
                doc.is_new.return_value = True

                with self.assertRaisesRegex(
                    frappe.ValidationError,
                    "cannot be changed directly",
                ):
                    IntercompanyMaterialBillingBatch._validate_finalization_audit_immutability(
                        doc
                    )

    def test_ordinary_save_cannot_change_finalization_audit_fields(self):
        saved_values = {
            "last_refreshed_on": "2026-10-20 10:00:00",
            "finalized_on": "2026-11-01 08:00:00",
            "finalized_by": "finance@example.com",
        }

        for fieldname in saved_values:
            for replacement in (None, f"replacement-{fieldname}"):
                with self.subTest(fieldname=fieldname, replacement=replacement):
                    current_values = saved_values.copy()
                    current_values[fieldname] = replacement
                    doc = batch(status="Under Review", **current_values)
                    doc.is_new.return_value = False
                    doc.get_doc_before_save.return_value = frappe._dict(
                        status="Under Review",
                        **saved_values,
                    )

                    with self.assertRaisesRegex(
                        frappe.ValidationError,
                        "cannot be changed directly",
                    ):
                        IntercompanyMaterialBillingBatch._validate_finalization_audit_immutability(
                            doc
                        )

        for fieldname, value in saved_values.items():
            with self.subTest(fieldname=fieldname, change="set"):
                doc = batch(status="Draft", **{fieldname: value})
                doc.is_new.return_value = False
                doc.get_doc_before_save.return_value = frappe._dict(
                    status="Draft",
                    last_refreshed_on=None,
                    finalized_on=None,
                    finalized_by=None,
                )

                with self.assertRaisesRegex(
                    frappe.ValidationError,
                    "cannot be changed directly",
                ):
                    IntercompanyMaterialBillingBatch._validate_finalization_audit_immutability(
                        doc
                    )

    def test_unchanged_historical_finalization_audit_values_are_allowed(self):
        values = {
            "last_refreshed_on": "2026-10-20 10:00:00",
            "finalized_on": "2026-11-01 08:00:00",
            "finalized_by": None,
        }
        doc = batch(status="Under Review", **values)
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            status="Under Review",
            **values,
        )

        IntercompanyMaterialBillingBatch._validate_finalization_audit_immutability(
            doc
        )

    def test_forged_authorization_cannot_change_finalization_audit_fields(self):
        doc = batch(
            last_refreshed_on="2026-10-20 10:00:00",
        )
        doc.is_new.return_value = False
        doc.flags._intercompany_material_billing_item_rebuild = True
        doc.flags._intercompany_material_billing_workflow_transition = (
            True,
            "Draft",
            "Under Review",
        )
        doc.get_doc_before_save.return_value = frappe._dict(
            status="Draft",
            last_refreshed_on=None,
            finalized_on=None,
            finalized_by=None,
        )

        with self.assertRaisesRegex(
            frappe.ValidationError,
            "cannot be changed directly",
        ):
            IntercompanyMaterialBillingBatch._validate_finalization_audit_immutability(
                doc
            )

    def test_authorized_preview_can_update_refresh_audit_only(self):
        doc = batch(last_refreshed_on="2026-10-20 10:00:00")
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            status="Draft",
            last_refreshed_on=None,
            finalized_on="2026-10-01 08:00:00",
            finalized_by="legacy@example.com",
        )
        doc.save.side_effect = lambda: IntercompanyMaterialBillingBatch._validate_finalization_audit_immutability(
            doc
        )

        IntercompanyMaterialBillingBatch._save_authorized_item_rebuild(doc)

    def test_authorized_preview_must_clear_finalization_fields(self):
        doc = batch(
            last_refreshed_on="2026-10-20 10:00:00",
            finalized_on="2026-10-20 10:00:00",
            finalized_by="finance@example.com",
        )
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            status="Draft",
            last_refreshed_on=None,
            finalized_on=None,
            finalized_by=None,
        )
        doc.save.side_effect = lambda: IntercompanyMaterialBillingBatch._validate_finalization_audit_immutability(
            doc
        )

        with self.assertRaisesRegex(
            frappe.ValidationError,
            "must leave Finalized On and Finalized By empty",
        ):
            IntercompanyMaterialBillingBatch._save_authorized_item_rebuild(doc)

    def test_authorized_finalize_can_update_finalization_audit_fields(self):
        doc = batch(
            status="Under Review",
            last_refreshed_on="2026-11-01 08:00:00",
            finalized_on="2026-11-01 08:00:00",
            finalized_by="finance@example.com",
        )
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            status="Draft",
            last_refreshed_on=None,
            finalized_on=None,
            finalized_by=None,
        )
        doc.save.side_effect = lambda: IntercompanyMaterialBillingBatch._validate_finalization_audit_immutability(
            doc
        )
        doc._save_authorized_item_rebuild.side_effect = (
            lambda: IntercompanyMaterialBillingBatch._save_authorized_item_rebuild(
                doc
            )
        )

        IntercompanyMaterialBillingBatch._save_authorized_workflow_transition(
            doc,
            "Draft",
            "Under Review",
            save_method=doc._save_authorized_item_rebuild,
        )

    def test_finance_authorization_cannot_change_finalization_audit_fields(self):
        doc = batch(
            status="Approved",
            finalized_by="replacement@example.com",
        )
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            status="Under Review",
            last_refreshed_on=None,
            finalized_on="2026-11-01 08:00:00",
            finalized_by="finance@example.com",
        )
        doc.save.side_effect = lambda: IntercompanyMaterialBillingBatch._validate_finalization_audit_immutability(
            doc
        )

        with self.assertRaisesRegex(
            frappe.ValidationError,
            "cannot be changed directly",
        ):
            IntercompanyMaterialBillingBatch._save_authorized_workflow_transition(
                doc,
                "Under Review",
                "Approved",
            )

    def test_authorized_finalization_transition_is_allowed(self):
        doc = batch(status="Under Review")
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(status="Draft")
        doc.save.side_effect = (
            lambda: IntercompanyMaterialBillingBatch._validate_status_transition(
                doc
            )
        )

        IntercompanyMaterialBillingBatch._save_authorized_workflow_transition(
            doc,
            "Draft",
            "Under Review",
        )

        self.assertNotIn(
            "_intercompany_material_billing_workflow_transition",
            doc.flags,
        )

    def test_authorized_finance_transition_and_fields_are_allowed(self):
        protected_values = {
            "sales_invoice": "SINV-2026-00001",
            "purchase_invoice": "PINV-2026-00001",
            "approved_by": "finance@example.com",
            "approved_on": "2026-11-01 09:00:00",
        }
        doc = batch(status="Approved", **protected_values)
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            status="Under Review",
            sales_invoice=None,
            purchase_invoice=None,
            approved_by=None,
            approved_on=None,
        )

        def validate_workflow_changes():
            IntercompanyMaterialBillingBatch._validate_status_transition(doc)
            IntercompanyMaterialBillingBatch._validate_approval_fields_immutability(
                doc
            )

        doc.save.side_effect = validate_workflow_changes

        IntercompanyMaterialBillingBatch._save_authorized_workflow_transition(
            doc,
            "Under Review",
            "Approved",
        )

        self.assertNotIn(
            "_intercompany_material_billing_workflow_transition",
            doc.flags,
        )

    def test_workflow_authorization_is_cleared_after_save_exception(self):
        doc = batch(status="Approved")
        save_error = RuntimeError("save failed")
        doc.save.side_effect = save_error

        with self.assertRaises(RuntimeError) as raised:
            IntercompanyMaterialBillingBatch._save_authorized_workflow_transition(
                doc,
                "Under Review",
                "Approved",
            )

        self.assertIs(raised.exception, save_error)
        self.assertNotIn(
            "_intercompany_material_billing_workflow_transition",
            doc.flags,
        )

    def test_workflow_authorization_restores_preexisting_flag(self):
        for save_error in (None, RuntimeError("save failed")):
            with self.subTest(save_error=save_error):
                doc = batch(status="Approved")
                previous_authorization = object()
                doc.flags._intercompany_material_billing_workflow_transition = (
                    previous_authorization
                )
                if save_error:
                    doc.save.side_effect = save_error

                if save_error:
                    with self.assertRaises(RuntimeError):
                        IntercompanyMaterialBillingBatch._save_authorized_workflow_transition(
                            doc,
                            "Under Review",
                            "Approved",
                        )
                else:
                    IntercompanyMaterialBillingBatch._save_authorized_workflow_transition(
                        doc,
                        "Under Review",
                        "Approved",
                    )

                self.assertIs(
                    doc.flags._intercompany_material_billing_workflow_transition,
                    previous_authorization,
                )

    def test_ordinary_save_cannot_clear_or_replace_protected_approval_fields(self):
        saved_values = {
            "sales_invoice": "SINV-2026-00001",
            "purchase_invoice": "PINV-2026-00001",
            "approved_by": "finance@example.com",
            "approved_on": "2026-11-01 09:00:00",
        }

        for fieldname, saved_value in saved_values.items():
            for replacement in (None, f"replacement-{fieldname}"):
                with self.subTest(fieldname=fieldname, replacement=replacement):
                    current_values = saved_values.copy()
                    current_values[fieldname] = replacement
                    doc = batch(status="Approved", **current_values)
                    doc.is_new.return_value = False
                    doc.get_doc_before_save.return_value = frappe._dict(
                        status="Approved",
                        **saved_values,
                    )

                    with self.assertRaisesRegex(
                        frappe.ValidationError,
                        "cannot be changed directly",
                    ):
                        IntercompanyMaterialBillingBatch._validate_approval_fields_immutability(
                            doc
                        )

    def test_ordinary_save_cannot_set_blank_protected_approval_fields(self):
        for fieldname in (
            "sales_invoice",
            "purchase_invoice",
            "approved_by",
            "approved_on",
        ):
            with self.subTest(fieldname=fieldname):
                doc = batch(status="Under Review", **{fieldname: "unauthorized"})
                doc.is_new.return_value = False
                doc.get_doc_before_save.return_value = frappe._dict(
                    status="Under Review",
                    sales_invoice=None,
                    purchase_invoice=None,
                    approved_by=None,
                    approved_on=None,
                )

                with self.assertRaisesRegex(
                    frappe.ValidationError,
                    "cannot be changed directly",
                ):
                    IntercompanyMaterialBillingBatch._validate_approval_fields_immutability(
                        doc
                    )

    def test_unchanged_partial_historical_approval_fields_are_allowed(self):
        doc = batch(
            status="Approved",
            sales_invoice="SINV-LEGACY",
            purchase_invoice=None,
            approved_by=None,
            approved_on=None,
        )
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            status="Approved",
            sales_invoice="SINV-LEGACY",
            purchase_invoice=None,
            approved_by=None,
            approved_on=None,
        )

        IntercompanyMaterialBillingBatch._validate_approval_fields_immutability(
            doc
        )

    def test_ordinary_draft_save_rejects_added_source_row(self):
        doc = batch(
            items=[billing_row(stock_entry_detail="SED-1"), billing_row(stock_entry_detail="SED-2")]
        )
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            items=[billing_row(stock_entry_detail="SED-1")]
        )

        with self.assertRaisesRegex(frappe.ValidationError, "membership cannot be changed"):
            IntercompanyMaterialBillingBatch._validate_source_membership_immutability(doc)

    def test_ordinary_draft_save_rejects_removed_source_row(self):
        doc = batch(items=[billing_row(stock_entry_detail="SED-1")])
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            items=[billing_row(stock_entry_detail="SED-1"), billing_row(stock_entry_detail="SED-2")]
        )

        with self.assertRaisesRegex(frappe.ValidationError, "membership cannot be changed"):
            IntercompanyMaterialBillingBatch._validate_source_membership_immutability(doc)

    def test_ordinary_draft_save_rejects_replaced_source_row(self):
        doc = batch(items=[billing_row(stock_entry_detail="SED-2")])
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            items=[billing_row(stock_entry_detail="SED-1")]
        )

        with self.assertRaisesRegex(frappe.ValidationError, "membership cannot be changed"):
            IntercompanyMaterialBillingBatch._validate_source_membership_immutability(doc)

    def test_duplicate_membership_cannot_bypass_validation(self):
        doc = batch(
            items=[billing_row(stock_entry_detail="SED-1"), billing_row(stock_entry_detail="SED-1")]
        )
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            items=[billing_row(stock_entry_detail="SED-1"), billing_row(stock_entry_detail="SED-2")]
        )

        with self.assertRaisesRegex(frappe.ValidationError, "membership cannot be changed"):
            IntercompanyMaterialBillingBatch._validate_source_membership_immutability(doc)

    def test_unchanged_source_membership_is_allowed(self):
        doc = batch(
            items=[billing_row(stock_entry_detail="SED-2"), billing_row(stock_entry_detail="SED-1")]
        )
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(
            items=[billing_row(stock_entry_detail="SED-1"), billing_row(stock_entry_detail="SED-2")]
        )

        IntercompanyMaterialBillingBatch._validate_source_membership_immutability(doc)

    def test_api_supplied_item_rebuild_flag_is_not_authorized(self):
        doc = batch(items=[billing_row(stock_entry_detail="SED-2")])
        doc.is_new.return_value = False
        doc.flags._intercompany_material_billing_item_rebuild = True
        doc.get_doc_before_save.return_value = frappe._dict(
            items=[billing_row(stock_entry_detail="SED-1")]
        )

        with self.assertRaisesRegex(frappe.ValidationError, "membership cannot be changed"):
            IntercompanyMaterialBillingBatch._validate_source_membership_immutability(doc)

    def test_authorized_item_rebuild_flag_is_cleared_after_save(self):
        doc = batch()
        doc.save.side_effect = (
            lambda: IntercompanyMaterialBillingBatch._validate_source_membership_immutability(
                doc
            )
        )
        doc.is_new.return_value = False
        doc.get_doc_before_save.return_value = frappe._dict(items=[])

        IntercompanyMaterialBillingBatch._save_authorized_item_rebuild(doc)

        self.assertNotIn("_intercompany_material_billing_item_rebuild", doc.flags)

    def test_authorized_item_rebuild_flag_is_cleared_after_save_exception(self):
        doc = batch()
        save_error = RuntimeError("save failed")
        doc.save.side_effect = save_error

        with self.assertRaises(RuntimeError) as raised:
            IntercompanyMaterialBillingBatch._save_authorized_item_rebuild(doc)

        self.assertIs(raised.exception, save_error)
        self.assertNotIn("_intercompany_material_billing_item_rebuild", doc.flags)

    def test_item_rebuild_authorization_restores_preexisting_flag(self):
        for save_error in (None, RuntimeError("save failed")):
            with self.subTest(save_error=save_error):
                doc = batch()
                previous_authorization = object()
                doc.flags._intercompany_material_billing_item_rebuild = (
                    previous_authorization
                )
                if save_error:
                    doc.save.side_effect = save_error

                if save_error:
                    with self.assertRaises(RuntimeError):
                        IntercompanyMaterialBillingBatch._save_authorized_item_rebuild(
                            doc
                        )
                else:
                    IntercompanyMaterialBillingBatch._save_authorized_item_rebuild(
                        doc
                    )

                self.assertIs(
                    doc.flags._intercompany_material_billing_item_rebuild,
                    previous_authorization,
                )

    def test_new_batch_source_membership_validation_is_unchanged(self):
        doc = batch()
        doc.is_new.return_value = True

        IntercompanyMaterialBillingBatch._validate_source_membership_immutability(doc)

        doc.get_doc_before_save.assert_not_called()

    @patch(f"{MODULE}.frappe.db.get_value")
    def test_creation_time_uniqueness_uses_locking_read(self, get_value):
        get_value.return_value = None
        doc = batch()
        doc.is_new.return_value = True

        IntercompanyMaterialBillingBatch._validate_batch_uniqueness(doc)

        get_value.assert_called_once_with(
            "Intercompany Material Billing Batch",
            {
                "issuing_company": doc.issuing_company,
                "requesting_company": doc.requesting_company,
                "billing_month": doc.billing_month,
                "status": ["!=", "Cancelled"],
                "name": ["!=", doc.name],
            },
            "name",
            order_by=None,
            for_update=True,
        )

    @patch(f"{MODULE}.frappe.db.get_value")
    def test_duplicate_company_pair_and_month_is_rejected(self, get_value):
        get_value.return_value = "ICMB-2026-00002"
        doc = batch()
        doc.is_new.return_value = True

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_batch_uniqueness(doc)

        get_value.assert_called_once()

    @patch(f"{MODULE}.frappe.db.get_value")
    def test_cancelled_historical_batch_allows_replacement(self, get_value):
        get_value.return_value = None
        doc = batch(status="Draft")
        doc.is_new.return_value = True

        IntercompanyMaterialBillingBatch._validate_batch_uniqueness(doc)

        filters = get_value.call_args.args[1]
        self.assertEqual(filters["status"], ["!=", "Cancelled"])

    @patch(f"{MODULE}.frappe.db.get_value")
    def test_cancelled_new_batch_skips_active_uniqueness(self, get_value):
        doc = batch(status="Cancelled")
        doc.is_new.return_value = True

        IntercompanyMaterialBillingBatch._validate_batch_uniqueness(doc)

        get_value.assert_not_called()

    @patch(f"{MODULE}.frappe.db.get_value")
    def test_existing_batch_skips_creation_time_uniqueness(self, get_value):
        doc = batch()
        doc.is_new.return_value = False

        IntercompanyMaterialBillingBatch._validate_batch_uniqueness(doc)

        get_value.assert_not_called()

    @patch(f"{MODULE}.frappe.db.get_value")
    def test_checkread_during_uniqueness_becomes_retry_error(self, get_value):
        database_error = RuntimeError(1020, "Record has changed")
        wrapped_error = frappe.QueryDeadlockError(database_error)
        wrapped_error.__cause__ = database_error
        get_value.side_effect = wrapped_error
        doc = batch()
        doc.is_new.return_value = True

        with self.assertRaisesRegex(
            frappe.ValidationError,
            "reload and retry in a new request",
        ):
            IntercompanyMaterialBillingBatch._validate_batch_uniqueness(doc)

    @patch(f"{MODULE}.frappe.db.get_value")
    def test_unrelated_deadlock_during_uniqueness_propagates(self, get_value):
        database_error = RuntimeError(1213, "Deadlock found")
        wrapped_error = frappe.QueryDeadlockError(database_error)
        wrapped_error.__cause__ = database_error
        get_value.side_effect = wrapped_error
        doc = batch()
        doc.is_new.return_value = True

        with self.assertRaises(frappe.QueryDeadlockError) as raised:
            IntercompanyMaterialBillingBatch._validate_batch_uniqueness(doc)

        self.assertIs(raised.exception, wrapped_error)

    def test_duplicate_detail_inside_batch_is_rejected(self):
        doc = batch(
            items=[
                billing_row(idx=1, stock_entry_detail="SED-1"),
                billing_row(idx=2, stock_entry_detail="SED-1"),
            ]
        )

        with (
            patch.object(doc, "_validate_not_billed_elsewhere"),
            patch.object(doc, "_get_source_context", return_value=source_context()),
            patch.object(doc, "_validate_source_context"),
            patch.object(doc, "_validate_snapshot"),
            self.assertRaises(frappe.ValidationError),
        ):
            IntercompanyMaterialBillingBatch._validate_items(doc)

    def test_detail_already_billed_elsewhere_is_rejected(self):
        matches = [
            frappe._dict(
                name="ROW-EXISTING",
                parent="ICMB-2026-00002",
            )
        ]

        doc = batch()
        doc._get_active_source_claim.side_effect = (
            lambda stock_entry_detail: IntercompanyMaterialBillingBatch._get_active_source_claim(
                doc, stock_entry_detail
            )
        )

        with (
            patch(f"{MODULE}.frappe.db.sql", return_value=matches),
            self.assertRaises(frappe.ValidationError),
        ):
            IntercompanyMaterialBillingBatch._validate_not_billed_elsewhere(
                doc,
                billing_row(),
            )

    def test_cancelled_batch_releases_detail_for_rebilling(self):
        doc = batch()
        doc._get_active_source_claim.side_effect = (
            lambda stock_entry_detail: IntercompanyMaterialBillingBatch._get_active_source_claim(
                doc, stock_entry_detail
            )
        )

        with patch(f"{MODULE}.frappe.db.sql", return_value=[]):
            IntercompanyMaterialBillingBatch._validate_not_billed_elsewhere(
                doc,
                billing_row(),
            )

    def test_source_claim_query_is_locking_and_scoped(self):
        doc = batch()

        with patch(f"{MODULE}.frappe.db.sql", return_value=[]) as sql:
            claimed_by = IntercompanyMaterialBillingBatch._get_active_source_claim(
                doc, "SED-1"
            )

        self.assertIsNone(claimed_by)
        query = " ".join(sql.call_args.args[0].split()).upper()
        params = sql.call_args.args[1]
        self.assertIn("FOR UPDATE", query)
        self.assertIn("BATCH.STATUS != 'CANCELLED'", query)
        self.assertIn("ITEM.PARENT != %(CURRENT_BATCH)S", query)
        self.assertEqual(params["stock_entry_detail"], "SED-1")
        self.assertEqual(params["current_batch"], doc.name)
        self.assertTrue(sql.call_args.kwargs["as_dict"])

    def test_current_batch_source_rows_do_not_conflict_with_themselves(self):
        doc = batch()
        doc._get_active_source_claim.side_effect = (
            lambda stock_entry_detail: IntercompanyMaterialBillingBatch._get_active_source_claim(
                doc, stock_entry_detail
            )
        )

        with patch(f"{MODULE}.frappe.db.sql", return_value=[]) as sql:
            self.assertFalse(
                IntercompanyMaterialBillingBatch._source_row_is_claimed_elsewhere(
                    doc, "SED-1"
                )
            )

        self.assertEqual(sql.call_args.args[1]["current_batch"], doc.name)

    def test_checkread_during_source_claim_becomes_retry_error(self):
        database_error = RuntimeError(1020, "Record has changed")
        wrapped_error = frappe.QueryDeadlockError(database_error)
        wrapped_error.__cause__ = database_error
        doc = batch()

        with (
            patch(f"{MODULE}.frappe.db.sql", side_effect=wrapped_error),
            self.assertRaisesRegex(
                frappe.ValidationError, "reload and retry in a new request"
            ),
        ):
            IntercompanyMaterialBillingBatch._get_active_source_claim(doc, "SED-1")

    def test_unrelated_source_claim_database_error_propagates(self):
        database_error = RuntimeError(1213, "Deadlock found")
        wrapped_error = frappe.QueryDeadlockError(database_error)
        wrapped_error.__cause__ = database_error
        doc = batch()

        with patch(f"{MODULE}.frappe.db.sql", side_effect=wrapped_error):
            with self.assertRaises(frappe.QueryDeadlockError) as raised:
                IntercompanyMaterialBillingBatch._get_active_source_claim(doc, "SED-1")

        self.assertIs(raised.exception, wrapped_error)

    def test_unsubmitted_stock_entry_is_rejected(self):
        doc = batch()
        source = source_context(stock_entry__docstatus=0)

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_source_context(
                doc,
                billing_row(),
                source,
            )

    def test_non_material_issue_is_rejected(self):
        doc = batch()
        source = source_context(stock_entry__purpose="Material Transfer")

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_source_context(
                doc,
                billing_row(),
                source,
            )

    def test_internal_issue_is_rejected(self):
        doc = batch()
        source = source_context(
            withdrawal_slip__requesting_company="Cagayan Corn Products Corporation"
        )

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_source_context(
                doc,
                billing_row(),
                source,
            )

    def test_issuing_company_mismatch_is_rejected(self):
        doc = batch(issuing_company="GSL Realty Corporation")

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_source_context(
                doc,
                billing_row(),
                source_context(),
            )

    def test_requesting_company_mismatch_is_rejected(self):
        doc = batch(requesting_company="Woodland Farms Corporation")

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_source_context(
                doc,
                billing_row(),
                source_context(),
            )

    def test_posting_date_outside_billing_month_is_rejected(self):
        doc = batch(
            billing_month=getdate("2026-09-01"),
            period_start=getdate("2026-09-01"),
            period_end=getdate("2026-09-30"),
        )

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_source_context(
                doc,
                billing_row(),
                source_context(),
            )

    def test_missing_withdrawal_slip_item_link_is_rejected(self):
        source = source_context(detail__withdrawal_slip_item=None)

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_source_context(
                batch(),
                billing_row(withdrawal_slip_item=None),
                source,
            )

    def test_snapshot_identity_mismatch_is_rejected(self):
        doc = batch()
        row = billing_row(item_code="WRONG-ITEM")

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_snapshot(
                doc,
                row,
                source_context(),
            )

    def test_snapshot_numeric_mismatch_is_rejected(self):
        doc = batch()
        row = billing_row(frozen_basic_rate=9999)

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_snapshot(
                doc,
                row,
                source_context(),
            )

    def test_amount_must_equal_quantity_times_rate(self):
        doc = batch()
        source = source_context(detail__basic_amount=9999)
        row = billing_row(frozen_basic_amount=9999)

        with self.assertRaisesRegex(
            frappe.ValidationError,
            "Quantity.*Frozen Basic Rate",
        ):
            IntercompanyMaterialBillingBatch._validate_snapshot(
                doc,
                row,
                source,
            )

    def test_valid_snapshot_is_accepted(self):
        IntercompanyMaterialBillingBatch._validate_snapshot(
            batch(),
            billing_row(),
            source_context(),
        )

    def test_totals_use_line_count_and_frozen_amount(self):
        doc = batch(
            items=[
                billing_row(idx=1, frozen_basic_amount=10000),
                billing_row(
                    idx=2,
                    stock_entry_detail="SED-2",
                    frozen_basic_amount=2500,
                ),
            ]
        )

        IntercompanyMaterialBillingBatch._set_totals(doc)

        self.assertEqual(doc.line_count, 2)
        self.assertEqual(doc.total_amount, 12500)

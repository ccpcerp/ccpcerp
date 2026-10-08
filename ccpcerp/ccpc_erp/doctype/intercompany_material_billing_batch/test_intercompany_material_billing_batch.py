# Copyright (c) 2026, CCPC and contributors
# See license.txt

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe
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
        "items": [billing_row()],
        "line_count": 0,
        "total_amount": 0,
    }
    defaults.update(values)

    doc = MagicMock(spec=IntercompanyMaterialBillingBatch)
    for fieldname, value in defaults.items():
        setattr(doc, fieldname, value)

    doc.get.side_effect = lambda fieldname: getattr(doc, fieldname)
    doc.meta.get_label.side_effect = lambda fieldname: fieldname
    return doc


class TestIntercompanyMaterialBillingBatch(TestCase):
    def setUp(self):
        translation = patch(f"{MODULE}._", side_effect=lambda value: value)
        translation.start()
        self.addCleanup(translation.stop)

        throw = patch(
            f"{MODULE}.frappe.throw",
            side_effect=raise_frappe_exception,
        )
        throw.start()
        self.addCleanup(throw.stop)

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

        with (
            patch(f"{MODULE}.frappe.get_all", return_value=matches),
            patch(
                f"{MODULE}.frappe.db.get_value",
                return_value="Under Review",
            ),
        ):
            claimed = IntercompanyMaterialBillingBatch._source_row_is_claimed_elsewhere(
                batch(),
                "SED-1",
            )

        self.assertTrue(claimed)

    def test_cancelled_batch_does_not_claim_source_detail(self):
        matches = [
            frappe._dict(parent="ICMB-2026-00002")
        ]

        with (
            patch(f"{MODULE}.frappe.get_all", return_value=matches),
            patch(
                f"{MODULE}.frappe.db.get_value",
                return_value="Cancelled",
            ),
        ):
            claimed = IntercompanyMaterialBillingBatch._source_row_is_claimed_elsewhere(
                batch(),
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

        doc.check_permission.assert_called_once_with("write")
        doc._require_draft_refresh.assert_called_once_with()
        doc._set_billing_period.assert_called_once_with()
        doc._rebuild_items_from_sources.assert_called_once_with()
        doc.save.assert_called_once_with()

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
        doc.save.assert_not_called()

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
        doc.save.assert_called_once_with()

        self.assertEqual(result["status"], "Under Review")
        self.assertEqual(result["line_count"], 1)
        self.assertEqual(result["total_amount"], 10000)
        self.assertEqual(result["finalized_by"], "finance@example.com")


    def test_finance_approver_roles_are_allowed(self):
        cases = [
            ("Administrator", []),
            ("finance@example.com", ["Finance Controller"]),
            ("manager@example.com", ["System Manager"]),
        ]

        for user, roles in cases:
            with self.subTest(user=user):
                doc = batch()

                with (
                    patch(
                        f"{MODULE}.frappe.session",
                        frappe._dict(user=user),
                    ),
                    patch(
                        f"{MODULE}.frappe.get_roles",
                        return_value=roles,
                    ),
                ):
                    IntercompanyMaterialBillingBatch._require_finance_approver(
                        doc
                    )

                doc.check_permission.assert_called_once_with("write")

    def test_non_finance_user_cannot_approve(self):
        doc = batch()

        with (
            patch(
                f"{MODULE}.frappe.session",
                frappe._dict(user="ordinary@example.com"),
            ),
            patch(
                f"{MODULE}.frappe.get_roles",
                return_value=["Accounts User"],
            ),
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

    def test_generated_invoice_guard_rejects_invalid_generated_document(self):
        doc = batch(total_amount=10000)

        invoice = frappe._dict(
            doctype="Sales Invoice",
            name="SINV-1",
            docstatus=0,
            update_stock=0,
            taxes=[],
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

        self.assertEqual(doc.status, "Approved")
        self.assertEqual(doc.approved_by, "finance@example.com")
        self.assertEqual(doc.approved_on, "2026-11-01 09:00:00")
        self.assertEqual(doc.sales_invoice, "SINV-2026-00001")
        self.assertEqual(doc.purchase_invoice, "PINV-2026-00001")
        doc.save.assert_called_once_with()

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

    @patch(f"{MODULE}.frappe.db.exists")
    def test_duplicate_company_pair_and_month_is_rejected(self, exists):
        exists.return_value = "ICMB-2026-00002"
        doc = batch()

        with self.assertRaises(frappe.ValidationError):
            IntercompanyMaterialBillingBatch._validate_batch_uniqueness(doc)

        exists.assert_called_once()

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

        with (
            patch(f"{MODULE}.frappe.get_all", return_value=matches),
            patch(
                f"{MODULE}.frappe.db.get_value",
                return_value="Under Review",
            ),
            self.assertRaises(frappe.ValidationError),
        ):
            IntercompanyMaterialBillingBatch._validate_not_billed_elsewhere(
                batch(),
                billing_row(),
            )

    def test_cancelled_batch_releases_detail_for_rebilling(self):
        matches = [
            frappe._dict(
                name="ROW-CANCELLED",
                parent="ICMB-2026-00002",
            )
        ]

        with (
            patch(f"{MODULE}.frappe.get_all", return_value=matches),
            patch(
                f"{MODULE}.frappe.db.get_value",
                return_value="Cancelled",
            ),
        ):
            IntercompanyMaterialBillingBatch._validate_not_billed_elsewhere(
                batch(),
                billing_row(),
            )

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

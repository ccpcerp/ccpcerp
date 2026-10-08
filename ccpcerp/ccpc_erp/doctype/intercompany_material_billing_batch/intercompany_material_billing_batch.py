# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_first_day, get_last_day, getdate, now_datetime, today


EPSILON = 0.000001


class IntercompanyMaterialBillingBatch(Document):
    def before_validate(self):
        self._set_billing_period()

    def validate(self):
        self._validate_companies()
        self._validate_batch_uniqueness()
        self._validate_month_end_approval()
        self._validate_finalization_state()
        self._validate_items()
        self._validate_nonempty_finalized_batch()
        self._set_totals()

    def _set_billing_period(self):
        if not self.billing_month:
            return

        billing_month = getdate(self.billing_month)
        self.billing_month = get_first_day(billing_month)
        self.period_start = self.billing_month
        self.period_end = get_last_day(self.billing_month)

    def _validate_companies(self):
        if not self.issuing_company or not self.requesting_company:
            return

        if self.issuing_company == self.requesting_company:
            frappe.throw(
                _("Issuing Company and Requesting Company must be different.")
            )

    def _validate_batch_uniqueness(self):
        if not (
            self.issuing_company
            and self.requesting_company
            and self.billing_month
        ):
            return

        existing = frappe.db.exists(
            "Intercompany Material Billing Batch",
            {
                "issuing_company": self.issuing_company,
                "requesting_company": self.requesting_company,
                "billing_month": self.billing_month,
                "status": ["!=", "Cancelled"],
                "name": ["!=", self.name or ""],
            },
        )

        if existing:
            frappe.throw(
                _(
                    "Intercompany Material Billing Batch {0} already exists "
                    "for {1} to {2} for billing month {3}."
                ).format(
                    frappe.bold(existing),
                    frappe.bold(self.issuing_company),
                    frappe.bold(self.requesting_company),
                    frappe.bold(self.billing_month),
                )
            )

    def _validate_items(self):
        seen = set()

        for row in self.get("items") or []:
            if not row.stock_entry_detail:
                frappe.throw(
                    _("Row {0}: Stock Entry Detail is required.").format(row.idx)
                )

            if row.stock_entry_detail in seen:
                frappe.throw(
                    _("Row {0}: Stock Entry Detail {1} is duplicated in this batch.").format(
                        row.idx,
                        frappe.bold(row.stock_entry_detail),
                    )
                )

            seen.add(row.stock_entry_detail)

            self._validate_not_billed_elsewhere(row)
            source = self._get_source_context(row)
            self._validate_source_context(row, source)
            self._validate_snapshot(row, source)

    def _validate_month_end_approval(self):
        if self.status not in {"Approved", "Invoiced"}:
            return

        if not self.period_end:
            frappe.throw(_("Billing period is required before approval."))

        if getdate(today()) <= getdate(self.period_end):
            frappe.throw(
                _(
                    "Billing for period ending {0} cannot be approved "
                    "until the billing month has ended."
                ).format(frappe.bold(self.period_end))
            )

    def _validate_finalization_state(self):
        if self.status not in {"Under Review", "Approved", "Invoiced"}:
            return

        if not self.period_end:
            frappe.throw(_("Billing period is required before finalization."))

        if not self.finalized_on:
            frappe.throw(
                _("A final month-end refresh is required before this batch can enter {0}.").format(
                    frappe.bold(self.status)
                )
            )

        if getdate(self.finalized_on) <= getdate(self.period_end):
            frappe.throw(
                _(
                    "Final refresh must be performed after billing period end {0}."
                ).format(frappe.bold(self.period_end))
            )

        if not self.finalized_by:
            frappe.throw(_("Finalized By is required after the final month-end refresh."))

    def _validate_not_billed_elsewhere(self, row):
        filters = {
            "stock_entry_detail": row.stock_entry_detail,
            "parenttype": "Intercompany Material Billing Batch",
        }

        if self.name:
            filters["parent"] = ["!=", self.name]

        matches = frappe.get_all(
            "Intercompany Material Billing Item",
            filters=filters,
            fields=["name", "parent"],
        )

        for match in matches:
            status = frappe.db.get_value(
                "Intercompany Material Billing Batch",
                match.parent,
                "status",
            )

            if status == "Cancelled":
                continue

            frappe.throw(
                _(
                    "Row {0}: Stock Entry Detail {1} is already assigned "
                    "to active billing batch {2}."
                ).format(
                    row.idx,
                    frappe.bold(row.stock_entry_detail),
                    frappe.bold(match.parent),
                )
            )

    def _get_source_context(self, row):
        detail = frappe.db.get_value(
            "Stock Entry Detail",
            row.stock_entry_detail,
            [
                "parent",
                "parenttype",
                "item_code",
                "item_name",
                "transfer_qty",
                "stock_uom",
                "basic_rate",
                "basic_amount",
                "valuation_rate",
                "amount",
                "withdrawal_slip_item",
            ],
            as_dict=True,
        )

        if not detail:
            frappe.throw(
                _("Row {0}: Stock Entry Detail {1} does not exist.").format(
                    row.idx,
                    frappe.bold(row.stock_entry_detail),
                )
            )

        if detail.parenttype != "Stock Entry":
            frappe.throw(
                _("Row {0}: Source detail must belong to a Stock Entry.").format(
                    row.idx
                )
            )

        stock_entry = frappe.db.get_value(
            "Stock Entry",
            detail.parent,
            [
                "name",
                "docstatus",
                "purpose",
                "company",
                "posting_date",
                "posting_time",
                "withdrawal_slip",
            ],
            as_dict=True,
        )

        if not stock_entry:
            frappe.throw(
                _("Row {0}: Source Stock Entry does not exist.").format(row.idx)
            )

        if not stock_entry.withdrawal_slip:
            frappe.throw(
                _(
                    "Row {0}: Stock Entry {1} is not linked to a Withdrawal Slip."
                ).format(row.idx, frappe.bold(stock_entry.name))
            )

        slip = frappe.db.get_value(
            "Withdrawal Slip",
            stock_entry.withdrawal_slip,
            [
                "name",
                "company",
                "requesting_company",
                "department",
                "section",
                "work_for_department",
                "work_for_section",
                "equipment",
            ],
            as_dict=True,
        )

        if not slip:
            frappe.throw(
                _("Row {0}: Withdrawal Slip {1} does not exist.").format(
                    row.idx,
                    frappe.bold(stock_entry.withdrawal_slip),
                )
            )

        return frappe._dict(
            detail=detail,
            stock_entry=stock_entry,
            withdrawal_slip=slip,
        )

    def _validate_source_context(self, row, source):
        detail = source.detail
        stock_entry = source.stock_entry
        slip = source.withdrawal_slip

        if stock_entry.docstatus != 1:
            frappe.throw(
                _("Row {0}: Stock Entry {1} must be submitted.").format(
                    row.idx,
                    frappe.bold(stock_entry.name),
                )
            )

        if stock_entry.purpose != "Material Issue":
            frappe.throw(
                _("Row {0}: Stock Entry {1} must be a Material Issue.").format(
                    row.idx,
                    frappe.bold(stock_entry.name),
                )
            )

        if not slip.requesting_company:
            frappe.throw(
                _("Row {0}: Withdrawal Slip {1} has no Requesting Company.").format(
                    row.idx,
                    frappe.bold(slip.name),
                )
            )

        if stock_entry.company == slip.requesting_company:
            frappe.throw(
                _(
                    "Row {0}: Stock Entry {1} is an internal material issue "
                    "and is not eligible for intercompany billing."
                ).format(row.idx, frappe.bold(stock_entry.name))
            )

        if stock_entry.company != self.issuing_company:
            frappe.throw(
                _(
                    "Row {0}: Stock Entry issuing company {1} does not match "
                    "batch Issuing Company {2}."
                ).format(
                    row.idx,
                    frappe.bold(stock_entry.company),
                    frappe.bold(self.issuing_company),
                )
            )

        if slip.company != stock_entry.company:
            frappe.throw(
                _(
                    "Row {0}: Withdrawal Slip issuing company does not match "
                    "its Stock Entry company."
                ).format(row.idx)
            )

        if slip.requesting_company != self.requesting_company:
            frappe.throw(
                _(
                    "Row {0}: Withdrawal Slip requesting company {1} does not "
                    "match batch Requesting Company {2}."
                ).format(
                    row.idx,
                    frappe.bold(slip.requesting_company),
                    frappe.bold(self.requesting_company),
                )
            )

        posting_date = getdate(stock_entry.posting_date)
        if not self.period_start or not self.period_end:
            frappe.throw(_("Billing period is required."))

        if not (
            getdate(self.period_start)
            <= posting_date
            <= getdate(self.period_end)
        ):
            frappe.throw(
                _(
                    "Row {0}: Stock Entry posting date {1} is outside billing "
                    "period {2} to {3}."
                ).format(
                    row.idx,
                    frappe.bold(posting_date),
                    frappe.bold(self.period_start),
                    frappe.bold(self.period_end),
                )
            )

        if not detail.withdrawal_slip_item:
            frappe.throw(
                _(
                    "Row {0}: Stock Entry Detail {1} is not linked to a "
                    "Withdrawal Slip Item."
                ).format(
                    row.idx,
                    frappe.bold(row.stock_entry_detail),
                )
            )

    def _validate_snapshot(self, row, source):
        detail = source.detail
        stock_entry = source.stock_entry
        slip = source.withdrawal_slip

        expected = {
            "stock_entry": stock_entry.name,
            "withdrawal_slip": slip.name,
            "withdrawal_slip_item": detail.withdrawal_slip_item,
            "posting_date": stock_entry.posting_date,
            "posting_time": stock_entry.posting_time,
            "item_code": detail.item_code,
            "item_name": detail.item_name,
            "stock_uom": detail.stock_uom,
            "requesting_department": slip.department,
            "requesting_section": slip.section,
            "work_for_department": slip.work_for_department,
            "work_for_section": slip.work_for_section,
            "equipment": slip.equipment,
        }

        for fieldname, expected_value in expected.items():
            actual_value = row.get(fieldname)
            if actual_value != expected_value:
                frappe.throw(
                    _(
                        "Row {0}: {1} does not match the submitted source record."
                    ).format(
                        row.idx,
                        frappe.bold(self.meta.get_label(fieldname) or fieldname),
                    )
                )

        numeric_expected = {
            "quantity": detail.transfer_qty,
            "frozen_basic_rate": detail.basic_rate,
            "frozen_basic_amount": detail.basic_amount,
            "valuation_rate_snapshot": detail.valuation_rate,
            "amount_snapshot": detail.amount,
        }

        for fieldname, expected_value in numeric_expected.items():
            actual_value = flt(row.get(fieldname))
            expected_value = flt(expected_value)

            if abs(actual_value - expected_value) > EPSILON:
                frappe.throw(
                    _(
                        "Row {0}: {1} does not match the submitted source record."
                    ).format(
                        row.idx,
                        frappe.bold(self.meta.get_label(fieldname) or fieldname),
                    )
                )

        if flt(row.quantity) <= 0:
            frappe.throw(
                _("Row {0}: Billing quantity must be greater than zero.").format(
                    row.idx
                )
            )

        if flt(row.frozen_basic_rate) < 0:
            frappe.throw(
                _("Row {0}: Frozen Basic Rate cannot be negative.").format(
                    row.idx
                )
            )

        if flt(row.frozen_basic_amount) < 0:
            frappe.throw(
                _("Row {0}: Frozen Basic Amount cannot be negative.").format(
                    row.idx
                )
            )

        calculated_amount = flt(row.quantity) * flt(row.frozen_basic_rate)
        if abs(calculated_amount - flt(row.frozen_basic_amount)) > EPSILON:
            frappe.throw(
                _(
                    "Row {0}: Frozen Basic Amount must equal Quantity × "
                    "Frozen Basic Rate."
                ).format(row.idx)
            )


    def _require_draft_refresh(self):
        if self.status != "Draft":
            frappe.throw(
                _("Only Draft billing batches can be refreshed.")
            )

    def _get_eligible_source_rows(self):
        if not (
            self.issuing_company
            and self.requesting_company
            and self.period_start
            and self.period_end
        ):
            frappe.throw(
                _("Issuing Company, Requesting Company, and Billing Month are required before refresh.")
            )

        rows = frappe.db.sql(
            """
            SELECT
                se.name AS stock_entry,
                se.posting_date,
                se.posting_time,
                sed.name AS stock_entry_detail,
                sed.withdrawal_slip_item,
                sed.item_code,
                sed.item_name,
                sed.transfer_qty,
                sed.stock_uom,
                sed.basic_rate,
                sed.basic_amount,
                sed.valuation_rate,
                sed.amount,
                ws.name AS withdrawal_slip,
                ws.department AS requesting_department,
                ws.section AS requesting_section,
                ws.work_for_department,
                ws.work_for_section,
                ws.equipment
            FROM `tabStock Entry` se
            INNER JOIN `tabStock Entry Detail` sed
                ON sed.parent = se.name
               AND sed.parenttype = 'Stock Entry'
            INNER JOIN `tabWithdrawal Slip` ws
                ON ws.name = se.withdrawal_slip
            WHERE se.docstatus = 1
              AND se.purpose = 'Material Issue'
              AND se.company = %(issuing_company)s
              AND ws.company = %(issuing_company)s
              AND ws.requesting_company = %(requesting_company)s
              AND ws.requesting_company != se.company
              AND se.posting_date BETWEEN %(period_start)s AND %(period_end)s
              AND sed.withdrawal_slip_item IS NOT NULL
              AND sed.withdrawal_slip_item != ''
            ORDER BY
                se.posting_date,
                se.posting_time,
                se.name,
                sed.idx
            """,
            {
                "issuing_company": self.issuing_company,
                "requesting_company": self.requesting_company,
                "period_start": self.period_start,
                "period_end": self.period_end,
            },
            as_dict=True,
        )

        return [
            row
            for row in rows
            if not self._source_row_is_claimed_elsewhere(row.stock_entry_detail)
        ]

    def _source_row_is_claimed_elsewhere(self, stock_entry_detail):
        filters = {
            "stock_entry_detail": stock_entry_detail,
            "parenttype": "Intercompany Material Billing Batch",
        }

        if self.name:
            filters["parent"] = ["!=", self.name]

        matches = frappe.get_all(
            "Intercompany Material Billing Item",
            filters=filters,
            fields=["parent"],
        )

        for match in matches:
            status = frappe.db.get_value(
                "Intercompany Material Billing Batch",
                match.parent,
                "status",
            )
            if status != "Cancelled":
                return True

        return False

    def _rebuild_items_from_sources(self):
        source_rows = self._get_eligible_source_rows()
        self.set("items", [])

        for source in source_rows:
            self.append(
                "items",
                {
                    "stock_entry": source.stock_entry,
                    "stock_entry_detail": source.stock_entry_detail,
                    "withdrawal_slip": source.withdrawal_slip,
                    "withdrawal_slip_item": source.withdrawal_slip_item,
                    "posting_date": source.posting_date,
                    "posting_time": source.posting_time,
                    "item_code": source.item_code,
                    "item_name": source.item_name,
                    "quantity": source.transfer_qty,
                    "stock_uom": source.stock_uom,
                    "frozen_basic_rate": source.basic_rate,
                    "frozen_basic_amount": source.basic_amount,
                    "valuation_rate_snapshot": source.valuation_rate,
                    "amount_snapshot": source.amount,
                    "requesting_department": source.requesting_department,
                    "requesting_section": source.requesting_section,
                    "work_for_department": source.work_for_department,
                    "work_for_section": source.work_for_section,
                    "equipment": source.equipment,
                },
            )

        self._set_totals()
        return len(source_rows)

    @frappe.whitelist()
    def refresh_preview(self):
        self.check_permission("write")
        self._require_draft_refresh()
        self._set_billing_period()

        count = self._rebuild_items_from_sources()

        self.last_refreshed_on = now_datetime()
        self.finalized_on = None
        self.finalized_by = None
        self.save()

        return {
            "name": self.name,
            "line_count": count,
            "total_amount": self.total_amount,
            "last_refreshed_on": self.last_refreshed_on,
        }

    @frappe.whitelist()
    def finalize_month(self):
        self.check_permission("write")
        self._require_draft_refresh()
        self._set_billing_period()

        if not self.period_end:
            frappe.throw(_("Billing period is required before final refresh."))

        if getdate(today()) <= getdate(self.period_end):
            frappe.throw(
                _(
                    "Final refresh cannot be performed until after billing period end {0}."
                ).format(frappe.bold(self.period_end))
            )

        count = self._rebuild_items_from_sources()

        now = now_datetime()
        self.last_refreshed_on = now
        self.finalized_on = now
        self.finalized_by = frappe.session.user
        self.status = "Under Review"
        self.save()

        return {
            "name": self.name,
            "status": self.status,
            "line_count": count,
            "total_amount": self.total_amount,
            "last_refreshed_on": self.last_refreshed_on,
            "finalized_on": self.finalized_on,
            "finalized_by": self.finalized_by,
        }


    def _require_finance_approver(self):
        self.check_permission("write")

        if frappe.session.user == "Administrator":
            return

        roles = set(frappe.get_roles(frappe.session.user))
        if not roles.intersection({"Finance Controller", "System Manager"}):
            frappe.throw(
                _("Only Finance Controller or System Manager can approve intercompany material billing."),
                exc=frappe.PermissionError,
            )

    def _require_under_review_for_approval(self):
        if self.status != "Under Review":
            frappe.throw(
                _("Only an Under Review billing batch can be approved.")
            )

        if self.sales_invoice or self.purchase_invoice:
            frappe.throw(
                _(
                    "This billing batch already has an invoice reference. "
                    "Review the existing Sales Invoice / Purchase Invoice before retrying approval."
                )
            )

    def _get_internal_party(self, doctype, represented_company):
        if doctype == "Customer":
            internal_field = "is_internal_customer"
        elif doctype == "Supplier":
            internal_field = "is_internal_supplier"
        else:
            frappe.throw(_("Unsupported internal party type {0}.").format(doctype))

        parties = frappe.get_all(
            doctype,
            filters={
                internal_field: 1,
                "represents_company": represented_company,
                "disabled": 0,
            },
            pluck="name",
        )

        if len(parties) != 1:
            frappe.throw(
                _(
                    "Exactly one enabled internal {0} must represent Company {1}; found {2}."
                ).format(
                    doctype,
                    frappe.bold(represented_company),
                    len(parties),
                )
            )

        return parties[0]

    def _validate_invoice_account(
        self,
        account,
        company,
        root_type,
        account_type=None,
    ):
        info = frappe.db.get_value(
            "Account",
            account,
            ["company", "root_type", "account_type", "is_group"],
            as_dict=True,
        )

        if not info:
            frappe.throw(
                _("Required billing Account {0} does not exist.").format(
                    frappe.bold(account)
                )
            )

        if info.company != company:
            frappe.throw(
                _("Account {0} does not belong to Company {1}.").format(
                    frappe.bold(account),
                    frappe.bold(company),
                )
            )

        if info.is_group:
            frappe.throw(
                _("Account {0} must be a ledger account.").format(
                    frappe.bold(account)
                )
            )

        if info.root_type != root_type:
            frappe.throw(
                _("Account {0} must have Root Type {1}.").format(
                    frappe.bold(account),
                    frappe.bold(root_type),
                )
            )

        if account_type and info.account_type != account_type:
            frappe.throw(
                _("Account {0} must have Account Type {1}.").format(
                    frappe.bold(account),
                    frappe.bold(account_type),
                )
            )

    def _get_company_invoice_defaults(self, company):
        company_info = frappe.db.get_value(
            "Company",
            company,
            ["abbr", "default_currency", "cost_center"],
            as_dict=True,
        )

        if not company_info:
            frappe.throw(
                _("Company {0} does not exist.").format(
                    frappe.bold(company)
                )
            )

        if not company_info.abbr:
            frappe.throw(
                _("Company {0} requires an abbreviation.").format(
                    frappe.bold(company)
                )
            )

        if not company_info.cost_center:
            frappe.throw(
                _("Company {0} requires a default Cost Center.").format(
                    frappe.bold(company)
                )
            )

        cost_center = frappe.db.get_value(
            "Cost Center",
            company_info.cost_center,
            ["company", "is_group", "disabled"],
            as_dict=True,
        )

        if (
            not cost_center
            or cost_center.company != company
            or cost_center.is_group
            or cost_center.disabled
        ):
            frappe.throw(
                _(
                    "Default Cost Center {0} is not a valid enabled ledger Cost Center for Company {1}."
                ).format(
                    frappe.bold(company_info.cost_center),
                    frappe.bold(company),
                )
            )

        abbr = company_info.abbr

        defaults = frappe._dict(
            currency=company_info.default_currency,
            cost_center=company_info.cost_center,
            receivable_account=f"1312 - Intercompany Receivable - {abbr}",
            payable_account=f"2121 - Intercompany Payable - {abbr}",
            expense_account=f"5150 - Intercompany Materials Expense - {abbr}",
            income_account=f"6050 - Intercompany Material Charges - {abbr}",
        )

        self._validate_invoice_account(
            defaults.receivable_account,
            company,
            "Asset",
            "Receivable",
        )
        self._validate_invoice_account(
            defaults.payable_account,
            company,
            "Liability",
            "Payable",
        )
        self._validate_invoice_account(
            defaults.expense_account,
            company,
            "Expense",
        )
        self._validate_invoice_account(
            defaults.income_account,
            company,
            "Income",
        )

        return defaults

    def _build_sales_invoice(self, customer, defaults):
        items = []

        for row in self.items:
            items.append(
                {
                    "item_code": row.item_code,
                    "qty": flt(row.quantity),
                    "uom": row.stock_uom,
                    "conversion_factor": 1,
                    "rate": flt(row.frozen_basic_rate),
                    "income_account": defaults.income_account,
                    "cost_center": defaults.cost_center,
                }
            )

        return frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": self.issuing_company,
                "customer": customer,
                "posting_date": self.period_end,
                "currency": defaults.currency,
                "debit_to": defaults.receivable_account,
                "update_stock": 0,
                "ignore_pricing_rule": 1,
                "taxes_and_charges": None,
                "taxes": [],
                "remarks": "Intercompany material billing batch {0}".format(
                    self.name
                ),
                "items": items,
            }
        )

    def _build_purchase_invoice(
        self,
        supplier,
        defaults,
        sales_invoice_name,
    ):
        items = []

        for row in self.items:
            items.append(
                {
                    "item_code": row.item_code,
                    "qty": flt(row.quantity),
                    "uom": row.stock_uom,
                    "conversion_factor": 1,
                    "rate": flt(row.frozen_basic_rate),
                    "expense_account": defaults.expense_account,
                    "cost_center": defaults.cost_center,
                    "department": row.requesting_department,
                    "equipment": row.equipment,
                }
            )

        return frappe.get_doc(
            {
                "doctype": "Purchase Invoice",
                "company": self.requesting_company,
                "supplier": supplier,
                "posting_date": self.period_end,
                "currency": defaults.currency,
                "credit_to": defaults.payable_account,
                "update_stock": 0,
                "ignore_pricing_rule": 1,
                "taxes_and_charges": None,
                "taxes": [],
                "inter_company_invoice_reference": sales_invoice_name,
                "remarks": "Intercompany material billing batch {0}".format(
                    self.name
                ),
                "items": items,
            }
        )

    def _validate_generated_invoice(self, invoice):
        if invoice.docstatus != 0:
            frappe.throw(
                _("{0} {1} must remain Draft after billing approval.").format(
                    invoice.doctype,
                    frappe.bold(invoice.name),
                )
            )

        if invoice.update_stock:
            frappe.throw(
                _("{0} {1} must not update stock.").format(
                    invoice.doctype,
                    frappe.bold(invoice.name),
                )
            )

        if invoice.get("taxes"):
            frappe.throw(
                _(
                    "{0} {1} generated unexpected tax rows. "
                    "Intercompany material billing approval does not apply taxes automatically."
                ).format(
                    invoice.doctype,
                    frappe.bold(invoice.name),
                )
            )

        if abs(flt(invoice.grand_total) - flt(self.total_amount)) > 0.01:
            frappe.throw(
                _(
                    "{0} {1} total {2} does not match billing batch total {3}."
                ).format(
                    invoice.doctype,
                    frappe.bold(invoice.name),
                    invoice.grand_total,
                    self.total_amount,
                )
            )

    @frappe.whitelist()
    def approve_and_create_invoices(self):
        self._require_finance_approver()
        self._require_under_review_for_approval()

        self._set_billing_period()
        self._validate_finalization_state()
        self._validate_items()
        self._validate_nonempty_finalized_batch()
        self._set_totals()

        issuing_defaults = self._get_company_invoice_defaults(
            self.issuing_company
        )
        requesting_defaults = self._get_company_invoice_defaults(
            self.requesting_company
        )

        customer = self._get_internal_party(
            "Customer",
            self.requesting_company,
        )
        supplier = self._get_internal_party(
            "Supplier",
            self.issuing_company,
        )

        sales_invoice = self._build_sales_invoice(
            customer,
            issuing_defaults,
        )
        sales_invoice.insert(ignore_permissions=True)
        self._validate_generated_invoice(sales_invoice)

        purchase_invoice = self._build_purchase_invoice(
            supplier,
            requesting_defaults,
            sales_invoice.name,
        )
        purchase_invoice.insert(ignore_permissions=True)
        self._validate_generated_invoice(purchase_invoice)

        sales_invoice.inter_company_invoice_reference = purchase_invoice.name
        sales_invoice.save(ignore_permissions=True)

        now = now_datetime()
        self.approved_by = frappe.session.user
        self.approved_on = now
        self.sales_invoice = sales_invoice.name
        self.purchase_invoice = purchase_invoice.name
        self.status = "Approved"
        self.save()

        return {
            "name": self.name,
            "status": self.status,
            "sales_invoice": self.sales_invoice,
            "purchase_invoice": self.purchase_invoice,
            "approved_by": self.approved_by,
            "approved_on": self.approved_on,
            "posting_date": self.period_end,
            "total_amount": self.total_amount,
        }

    def _validate_nonempty_finalized_batch(self):
        if self.status in ("Under Review", "Approved", "Invoiced") and not self.items:
            frappe.throw(
                "Intercompany Material Billing Batch must contain at least one billing line before it can be finalized."
            )

    def _set_totals(self):
        self.line_count = len(self.get("items") or [])
        self.total_amount = sum(
            flt(row.frozen_basic_amount)
            for row in self.get("items") or []
        )

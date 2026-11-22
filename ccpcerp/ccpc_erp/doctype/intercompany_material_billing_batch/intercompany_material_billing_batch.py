# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

from collections import Counter
from uuid import uuid4

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_first_day, get_last_day, getdate, now_datetime, today

from ccpcerp.ccpc_erp.phase1_release_scope import (
    Phase1ScopeError,
    require_billing_disabled,
)


EPSILON = 0.000001
ER_CHECKREAD = 1020
BILLING_SCOPE_FIELDS = (
    "issuing_company",
    "requesting_company",
    "billing_month",
)
APPROVAL_PROTECTED_FIELDS = (
    "sales_invoice",
    "purchase_invoice",
    "approved_by",
    "approved_on",
)
FINALIZATION_AUDIT_FIELDS = (
    "last_refreshed_on",
    "finalized_on",
    "finalized_by",
)
_AUTHORIZED_WORKFLOW_TRANSITION = object()
_WORKFLOW_TRANSITION_FLAG = "_intercompany_material_billing_workflow_transition"
_AUTHORIZED_ITEM_REBUILD = object()
_ITEM_REBUILD_FLAG = "_intercompany_material_billing_item_rebuild"


def get_wrapped_database_errno(error):
    """Return the errno from a database exception wrapped by Frappe."""
    original = error.__cause__
    if original is None and error.args and isinstance(error.args[0], BaseException):
        original = error.args[0]
    return original.args[0] if original and original.args else None


def _assert_billing_not_released():
    """Fail closed even on direct server/whitelisted method calls."""
    try:
        require_billing_disabled()
    except Phase1ScopeError as exc:
        frappe.throw(_(str(exc)), frappe.PermissionError)


class IntercompanyMaterialBillingBatch(Document):
    def on_trash(self):
        _assert_billing_not_released()
        persisted, has_items = self._get_locked_deletion_state()
        protected_values = (
            persisted.sales_invoice,
            persisted.purchase_invoice,
            persisted.approved_by,
            persisted.approved_on,
            persisted.last_refreshed_on,
            persisted.finalized_on,
            persisted.finalized_by,
        )

        if (
            persisted.status != "Draft"
            or persisted.docstatus != 0
            or has_items
            or any(value is not None and type(value) is not str for value in protected_values)
            or any(type(value) is str and value != "" for value in protected_values)
        ):
            frappe.throw(_("Only an empty, unprocessed Draft billing batch can be deleted."))

    def before_discard(self):
        frappe.throw(
            _(
                "Intercompany Material Billing Batches cannot use the standard "
                "Discard action. Use the controlled billing batch cancellation process."
            )
        )

    def _get_locked_deletion_state(self):
        persisted_rows = frappe.db.sql(
            """
            SELECT
                status,
                docstatus,
                sales_invoice,
                purchase_invoice,
                approved_by,
                approved_on,
                last_refreshed_on,
                finalized_on,
                finalized_by
            FROM `tabIntercompany Material Billing Batch`
            WHERE name = %s
            FOR UPDATE
            """,
            (self.name,),
            as_dict=True,
        )
        if not persisted_rows:
            frappe.throw(
                _(
                    "The persisted billing batch state could not be verified. "
                    "Deletion was not performed."
                )
            )

        item_rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabIntercompany Material Billing Item`
            WHERE parent = %(parent)s
              AND parenttype = %(parenttype)s
              AND parentfield = %(parentfield)s
            LIMIT 1
            FOR UPDATE
            """,
            {
                "parent": self.name,
                "parenttype": "Intercompany Material Billing Batch",
                "parentfield": "items",
            },
            as_dict=True,
        )
        return persisted_rows[0], bool(item_rows)

    def before_validate(self):
        _assert_billing_not_released()
        self._set_billing_period()

    def validate(self):
        _assert_billing_not_released()
        self._validate_companies()
        self._validate_billing_scope_immutability()
        self._validate_status_transition()
        self._validate_finalization_audit_immutability()
        self._validate_approval_fields_immutability()
        self._validate_source_membership_immutability()
        validate_source_claims = self._source_membership_may_change()
        if validate_source_claims:
            self._acquire_billing_scope_lock()
        self._validate_batch_uniqueness()
        self._validate_month_end_approval()
        self._validate_finalization_state()
        self._validate_items(validate_source_claims=validate_source_claims)
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

    def _validate_billing_scope_immutability(self):
        if self.is_new():
            return

        before = self.get_doc_before_save()
        if not before:
            self._throw_unverified_saved_state()

        for fieldname in BILLING_SCOPE_FIELDS:
            current_value = self.get(fieldname)
            previous_value = before.get(fieldname)

            if fieldname == "billing_month":
                current_value = getdate(current_value) if current_value else None
                previous_value = getdate(previous_value) if previous_value else None

            if current_value != previous_value:
                frappe.throw(
                    _("{0} cannot be changed after the billing batch is created.").format(
                        frappe.bold(self.meta.get_label(fieldname))
                    )
                )

    def _validate_status_transition(self):
        if self.is_new():
            if self.status != "Draft":
                frappe.throw(_("A new billing batch must start in Draft status."))
            return

        before = self.get_doc_before_save()
        if not before:
            self._throw_unverified_saved_state()

        previous_status = before.status
        current_status = self.status

        if previous_status == current_status:
            return

        transition = (previous_status, current_status)
        if transition in {
            ("Draft", "Under Review"),
            ("Under Review", "Approved"),
        } and self._workflow_transition_is_authorized(*transition):
            return

        if transition in {
            ("Draft", "Cancelled"),
            ("Under Review", "Cancelled"),
        }:
            populated_fields = [
                fieldname
                for fieldname in APPROVAL_PROTECTED_FIELDS
                if self.get(fieldname) or before.get(fieldname)
            ]
            if populated_fields:
                frappe.throw(
                    _(
                        "A billing batch with invoice or approval information "
                        "cannot be cancelled."
                    )
                )
            return

        if previous_status == "Cancelled":
            frappe.throw(_("A cancelled billing batch cannot be reactivated."))

        frappe.throw(
            _("Billing batch status cannot be changed from {0} to {1}.").format(
                frappe.bold(previous_status),
                frappe.bold(current_status),
            )
        )

    def _validate_approval_fields_immutability(self):
        if self.is_new():
            changed_fields = [
                fieldname
                for fieldname in APPROVAL_PROTECTED_FIELDS
                if self.get(fieldname)
            ]
            if changed_fields:
                self._throw_protected_approval_fields(changed_fields)
            return

        before = self.get_doc_before_save()
        if not before:
            self._throw_unverified_saved_state()

        changed_fields = [
            fieldname
            for fieldname in APPROVAL_PROTECTED_FIELDS
            if str(self.get(fieldname) or "") != str(before.get(fieldname) or "")
        ]
        if not changed_fields:
            return

        if self._workflow_transition_is_authorized("Under Review", "Approved"):
            return

        self._throw_protected_approval_fields(changed_fields)

    def _throw_protected_approval_fields(self, fieldnames):
        labels = ", ".join(
            frappe.bold(self.meta.get_label(fieldname) or fieldname)
            for fieldname in fieldnames
        )
        frappe.throw(
            _("Approval and invoice fields cannot be changed directly: {0}.").format(
                labels
            )
        )

    def _validate_finalization_audit_immutability(self):
        if self.is_new():
            changed_fields = [
                fieldname
                for fieldname in FINALIZATION_AUDIT_FIELDS
                if self.get(fieldname)
            ]
            if changed_fields:
                self._throw_protected_finalization_audit_fields(changed_fields)
            return

        before = self.get_doc_before_save()
        if not before:
            self._throw_unverified_saved_state()

        changed_fields = [
            fieldname
            for fieldname in FINALIZATION_AUDIT_FIELDS
            if str(self.get(fieldname) or "") != str(before.get(fieldname) or "")
        ]
        if not changed_fields:
            return

        if self._finalization_audit_update_is_authorized(before):
            if self.status == "Draft" and (
                self.finalized_on or self.finalized_by
            ):
                frappe.throw(
                    _(
                        "Refresh Preview must leave Finalized On and Finalized By empty."
                    )
                )
            return

        self._throw_protected_finalization_audit_fields(changed_fields)

    def _throw_protected_finalization_audit_fields(self, fieldnames):
        labels = ", ".join(
            frappe.bold(self.meta.get_label(fieldname) or fieldname)
            for fieldname in fieldnames
        )
        frappe.throw(
            _(
                "Refresh and finalization audit fields cannot be changed directly: {0}."
            ).format(labels)
        )

    def _finalization_audit_update_is_authorized(self, before):
        if self.flags.get(_ITEM_REBUILD_FLAG) is not _AUTHORIZED_ITEM_REBUILD:
            return False

        if before.status == "Draft" and self.status == "Draft":
            return self.flags.get(_WORKFLOW_TRANSITION_FLAG) is None

        return (
            before.status == "Draft"
            and self.status == "Under Review"
            and self._workflow_transition_is_authorized("Draft", "Under Review")
        )

    def _workflow_transition_is_authorized(self, previous_status, current_status):
        authorization = self.flags.get(_WORKFLOW_TRANSITION_FLAG)
        return (
            isinstance(authorization, tuple)
            and len(authorization) == 3
            and authorization[0] is _AUTHORIZED_WORKFLOW_TRANSITION
            and authorization[1:] == (previous_status, current_status)
        )

    def _validate_source_membership_immutability(self):
        if self.is_new():
            return

        if self.flags.get(_ITEM_REBUILD_FLAG) is _AUTHORIZED_ITEM_REBUILD:
            return

        before = self.get_doc_before_save()
        if not before:
            self._throw_unverified_saved_state()

        current_membership = Counter(
            row.stock_entry_detail for row in (self.get("items") or [])
        )
        previous_membership = Counter(
            row.stock_entry_detail for row in (before.get("items") or [])
        )

        if current_membership != previous_membership:
            frappe.throw(
                _(
                    "Source Stock Entry Detail membership cannot be changed after "
                    "the billing batch is created. Use Refresh Preview or Finalize "
                    "Month to rebuild billing items."
                )
            )

    def _throw_unverified_saved_state(self):
        frappe.throw(
            _(
                "The previous billing batch state could not be verified. "
                "Please reload and retry in a new request."
            )
        )

    def _source_membership_may_change(self):
        return self.is_new() or (
            self.flags.get(_ITEM_REBUILD_FLAG) is _AUTHORIZED_ITEM_REBUILD
        )

    def _acquire_billing_scope_lock(self, company=None):
        company = company or self.issuing_company
        if not company:
            return

        rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabCompany`
            WHERE name = %s
            FOR UPDATE
            """,
            (company,),
            as_dict=True,
        )

        if not rows:
            frappe.throw(
                _("Issuing Company {0} does not exist.").format(
                    frappe.bold(company)
                )
            )

        return rows[0].name

    def _lock_and_reload_for_mutation(self):
        if not self.name or self.is_new():
            frappe.throw(
                _("Billing batch must be saved before this action can be performed.")
            )

        # Use the issuer to acquire the shared Company lock FIRST. This
        # read can be snapshot-based, so recheck it against the locked row.
        issuing_company = frappe.db.get_value(
            "Intercompany Material Billing Batch",
            self.name,
            "issuing_company",
        )
        if not issuing_company:
            frappe.throw(
                _("Billing batch {0} no longer exists.").format(
                    frappe.bold(self.name)
                )
            )

        self._acquire_billing_scope_lock(issuing_company)
        locked_rows = frappe.db.sql(
            """
            SELECT issuing_company, modified, status, sales_invoice, purchase_invoice
            FROM `tabIntercompany Material Billing Batch`
            WHERE name = %s
            FOR UPDATE
            """,
            (self.name,),
            as_dict=True,
        )
        if not locked_rows:
            frappe.throw(
                _("Billing batch {0} no longer exists.").format(
                    frappe.bold(self.name)
                )
            )

        locked_state = locked_rows[0]
        if locked_state.issuing_company != issuing_company:
            frappe.throw(
                _(
                    "Issuing Company changed while this billing action was starting. "
                    "Please retry the action in a new request."
                )
            )

        previous_for_update = self.flags.for_update
        self.flags.for_update = True
        try:
            self.reload()
        finally:
            self.flags.for_update = previous_for_update

        # With MariaDB REPEATABLE-READ, reload() may return an OLD snapshot
        # even after a locking read. Never act on inconsistent loaded state.
        verified_fields = (
            "issuing_company",
            "modified",
            "status",
            "sales_invoice",
            "purchase_invoice",
        )
        if any(
            str(getattr(self, field, None)) != str(locked_state.get(field))
            for field in verified_fields
        ):
            frappe.throw(
                _(
                    "Billing batch changed in another transaction. "
                    "Please reload the page and retry the action in a new request."
                )
            )

        self.check_permission("write")

    def _validate_batch_uniqueness(self):
        if not self.is_new() or self.status == "Cancelled":
            return

        if not (
            self.issuing_company
            and self.requesting_company
            and self.billing_month
        ):
            return

        try:
            existing = frappe.db.get_value(
                "Intercompany Material Billing Batch",
                {
                    "issuing_company": self.issuing_company,
                    "requesting_company": self.requesting_company,
                    "billing_month": self.billing_month,
                    "status": ["!=", "Cancelled"],
                    "name": ["!=", self.name or ""],
                },
                "name",
                order_by=None,
                for_update=True,
            )
        except frappe.QueryDeadlockError as error:
            if get_wrapped_database_errno(error) == ER_CHECKREAD:
                frappe.throw(
                    _(
                        "Another billing batch changed concurrently. Nothing was saved. "
                        "Please reload and retry in a new request."
                    )
                )
            raise

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

    def _validate_items(self, validate_source_claims=True):
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

            if validate_source_claims:
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
        claimed_by = self._get_active_source_claim(row.stock_entry_detail)
        if claimed_by:
            frappe.throw(
                _(
                    "Row {0}: Stock Entry Detail {1} is already assigned "
                    "to active billing batch {2}."
                ).format(
                    row.idx,
                    frappe.bold(row.stock_entry_detail),
                    frappe.bold(claimed_by),
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
        return bool(self._get_active_source_claim(stock_entry_detail))

    def _get_active_source_claim(self, stock_entry_detail):
        try:
            matches = frappe.db.sql(
                """
                SELECT item.parent
                FROM `tabIntercompany Material Billing Item` item
                INNER JOIN `tabIntercompany Material Billing Batch` batch
                    ON batch.name = item.parent
                   AND item.parenttype = 'Intercompany Material Billing Batch'
                WHERE item.stock_entry_detail = %(stock_entry_detail)s
                  AND item.parent != %(current_batch)s
                  AND batch.status != 'Cancelled'
                LIMIT 1
                FOR UPDATE
                """,
                {
                    "stock_entry_detail": stock_entry_detail,
                    "current_batch": self.name or "",
                },
                as_dict=True,
            )
        except frappe.QueryDeadlockError as error:
            if get_wrapped_database_errno(error) == ER_CHECKREAD:
                frappe.throw(
                    _(
                        "Another billing batch changed concurrently. Nothing was saved. "
                        "Please reload and retry in a new request."
                    )
                )
            raise

        return matches[0].parent if matches else None

    def _save_authorized_item_rebuild(self):
        previous_authorization = self.flags.get(_ITEM_REBUILD_FLAG)
        self.flags[_ITEM_REBUILD_FLAG] = _AUTHORIZED_ITEM_REBUILD
        try:
            self.save()
        finally:
            if previous_authorization is None:
                self.flags.pop(_ITEM_REBUILD_FLAG, None)
            else:
                self.flags[_ITEM_REBUILD_FLAG] = previous_authorization

    def _save_authorized_workflow_transition(
        self,
        previous_status,
        current_status,
        save_method=None,
    ):
        previous_authorization = self.flags.get(_WORKFLOW_TRANSITION_FLAG)
        self.flags[_WORKFLOW_TRANSITION_FLAG] = (
            _AUTHORIZED_WORKFLOW_TRANSITION,
            previous_status,
            current_status,
        )
        try:
            if save_method is None:
                self.save()
            else:
                save_method()
        finally:
            if previous_authorization is None:
                self.flags.pop(_WORKFLOW_TRANSITION_FLAG, None)
            else:
                self.flags[_WORKFLOW_TRANSITION_FLAG] = previous_authorization

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
        _assert_billing_not_released()
        self.check_permission("write")
        self._lock_and_reload_for_mutation()
        self._require_draft_refresh()
        self._set_billing_period()

        count = self._rebuild_items_from_sources()

        self.last_refreshed_on = now_datetime()
        self.finalized_on = None
        self.finalized_by = None
        self._save_authorized_item_rebuild()

        return {
            "name": self.name,
            "line_count": count,
            "total_amount": self.total_amount,
            "last_refreshed_on": self.last_refreshed_on,
        }

    @frappe.whitelist()
    def finalize_month(self):
        _assert_billing_not_released()
        self.check_permission("write")
        self._lock_and_reload_for_mutation()
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
        self._save_authorized_workflow_transition(
            "Draft",
            "Under Review",
            save_method=self._save_authorized_item_rebuild,
        )

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
        _assert_billing_not_released()
        self._require_finance_approver()
        self._lock_and_reload_for_mutation()
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

        # Frozen source rates cannot safely be copied as numerically identical
        # amounts between unlike currencies. Do not silently invent an FX rate
        # or post a mismatched intercompany claim. A reviewed FX conversion
        # policy is a separate future Accounting decision.
        issuing_currency = issuing_defaults.get("currency")
        requesting_currency = requesting_defaults.get("currency")
        if (not issuing_currency or not requesting_currency
                or issuing_currency != requesting_currency):
            frappe.throw(
                _("Intercompany invoice currency mismatch: both Companies must "
                  "use the same currency until an approved FX conversion "
                  "and reconciliation policy is implemented.")
            )

        customer = self._get_internal_party(
            "Customer",
            self.requesting_company,
        )
        supplier = self._get_internal_party(
            "Supplier",
            self.issuing_company,
        )

        # Inserting two separate ERPNext invoices is one financial decision.
        # Frappe normally rolls back an uncaught failed HTTP request, but callers
        # may catch an exception and commit their wider transaction. A dedicated
        # savepoint prevents orphan draft invoices in that case as well.
        # Use a unique identifier to avoid colliding with a caller's savepoint.
        approval_savepoint = "ccpc_ic_pair_" + uuid4().hex
        frappe.db.savepoint(approval_savepoint)
        try:
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
            self._save_authorized_workflow_transition(
                "Under Review",
                "Approved",
            )
        except Exception:
            frappe.db.rollback(save_point=approval_savepoint)
            # A failed approval must not leave an in-memory 'Approved' document
            # containing references to invoices that were rolled back.
            self.status = "Under Review"
            self.sales_invoice = None
            self.purchase_invoice = None
            self.approved_by = None
            self.approved_on = None
            raise

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

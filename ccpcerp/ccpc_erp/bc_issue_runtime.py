# Copyright (c) 2026, CCPC and contributors
"""Server-side B/C evidence resolver for original Withdrawal Slip Material Issue.

Review-only code. It does NOT approve financial mappings or change permissions.
Current mapping revisions are intentionally Draft-only: real issues fail closed
until an independently reviewed approval workflow and signed map intake exist.

Requires an existing authenticated/permissioned issue transaction, with the
Withdrawal Slip header already locked. All queries operate in that transaction.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import get_datetime, get_system_timezone

from ccpcerp.ccpc_erp.bc_issue_allocation import (
    ExpenseEvidence, CenterEvidence, resolve_original_issue_allocation,
    prepare_original_issue_accounting_rows, assert_original_issue_rows_equal,
)
from ccpcerp.ccpc_erp.bc_issue_locked_source import read_locked_issue_item_groups
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
from ccpcerp.ccpc_erp.withdrawal_issue_beneficiary import resolve_beneficiary_department


def _require(condition, message):
    if not condition:
        raise MappingPolicyError(message)


def _canonical(value, label):
    _require(isinstance(value, str) and bool(value.strip()) and value == value.strip(),
             label + " must be a canonical ERPNext record ID.")
    return value


def _utc(value):
    """Frappe Datetime fields are site-local naive values; normalize for G2."""
    if value is None or value == "":
        return None
    parsed = get_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(get_system_timezone()))
    return parsed.astimezone(timezone.utc)


def _one(rows, label):
    _require(len(rows) == 1, "Expected exactly one locked " + label + ".")
    return rows[0]


def _required_fields():
    """Missing migrations cannot silently downgrade financial accounting."""
    for doctype, fields in {
        "Stock Entry Detail": (
            "ccpc_expense_mapping_revision", "ccpc_center_mapping_revision",
            "ccpc_item_group_at_issue",
        ),
        "Stock Entry": ("withdrawal_slip",),
    }.items():
        meta = frappe.get_meta(doctype)
        for name in fields:
            _require(meta.has_field(name), "Missing B/C provenance field: " + doctype + "." + name)


def _real_authority(user, role, company):
    """Require actual enabled User, Role membership, and explicit Company grant.

    This does NOT replace verifying the historical approval decision/fingerprint
    in the future mapping approval service. No System Manager or Administrator
    override; never trust submitted JSON role/Company claims.
    """
    _canonical(user, "Mapping actor")
    _require(user not in ("Administrator", "Guest"), "Named Finance mapping actors are required.")
    enabled = frappe.db.sql("SELECT enabled FROM \`tabUser\` WHERE name=%s FOR UPDATE",
                            (user,), as_dict=True)
    _require(len(enabled) == 1 and enabled[0].get("enabled") == 1,
             "Mapping actor is not an enabled ERPNext user.")
    assigned = frappe.db.sql(
        "SELECT name FROM \`tabHas Role\` WHERE parenttype='User' "
        "AND parent=%s AND role=%s ORDER BY name FOR UPDATE",
        (user, role), as_dict=True,
    )
    _require(bool(assigned), "Mapping actor lacks an explicit Finance mapping role.")
    granted = frappe.db.sql(
        "SELECT name FROM \`tabUser Permission\` WHERE user=%s AND allow='Company' "
        "AND for_value=%s ORDER BY name FOR UPDATE",
        (user, company), as_dict=True,
    )
    _require(bool(granted), "Mapping actor lacks explicit Company-scoped permission.")
    stockroom = frappe.db.sql(
        "SELECT name FROM \`tabHas Role\` WHERE parenttype='User' AND parent=%s "
        "AND role='Stockroom Personnel' ORDER BY name FOR UPDATE",
        (user,), as_dict=True,
    )
    _require(not stockroom, "Stockroom personnel cannot authorize financial mapping revisions.")


def _assert_approval_provenance(revision, company):
    """Defense-in-depth only; the approved-revision writer is not yet enabled."""
    maker = _canonical(revision.get("owner"), "Mapping preparer")
    checker = _canonical(revision.get("approved_by"), "Mapping approver")
    _require(maker.casefold() != checker.casefold(), "A mapping approver cannot approve their own draft.")
    _canonical(revision.get("approval_reference"), "Accounting approval reference")
    _require(revision.get("approval_state") == "Approved",
             "B/C revision is not approved.")
    _require(bool(revision.get("approved_at")), "Missing server-owned approval timestamp.")
    _real_authority(maker, "CCPC Mapping Preparer", company)
    _real_authority(checker, "CCPC Accounting Mapping Approver", company)


def _lock_scope(doctype, company, scope_field, scope_value, *, department_only=False):
    _canonical(company, "Company")
    _canonical(scope_value, "Mapping scope")
    suffix = " AND selector_kind='Department Only' AND selector_key='DEPARTMENT_ONLY'" if department_only else ""
    rows = frappe.db.sql(
        "SELECT name, company, " + scope_field +
        " FROM \`tab" + doctype + "\` WHERE company=%s AND " + scope_field +
        "=%s" + suffix + " ORDER BY name FOR UPDATE",
        (company, scope_value), as_dict=True,
    )
    scope = _one(rows, doctype)
    _require(scope.get("company") == company and scope.get(scope_field) == scope_value,
             "B/C mapping scope mismatch.")
    return scope.name


def _lock_revisions(doctype, scope):
    # The B and C DocTypes deliberately have DIFFERENT master-link columns.
    if doctype == "CCPC Expense Mapping Revision":
        value_field = "expense_account"
    elif doctype == "CCPC Center Mapping Revision":
        value_field = "cost_center"
    else:
        raise MappingPolicyError("Unexpected financial mapping revision DocType.")
    return frappe.db.sql(
        "SELECT name, mapping_scope, owner, " + value_field +
        ", approval_state, approval_reference, approved_by, approved_at, "
        "effective_from, effective_until FROM `tab" + doctype + "` "
        "WHERE mapping_scope=%s ORDER BY name FOR UPDATE",
        (scope,), as_dict=True,
    )

def _master(doctype, record):
    _canonical(record, doctype)
    rows = frappe.db.sql(
        "SELECT name, company, is_group, disabled" +
        (", root_type" if doctype == "Account" else "") +
        " FROM \`tab" + doctype + "\` WHERE name=%s FOR UPDATE",
        (record,), as_dict=True,
    )
    result = _one(rows, doctype)
    _require(result.name == record, "Locked financial master identity mismatch.")
    return result


def _locked_source_lines(slip, beneficiary):
    slip_items = list(slip.get("items") or [])
    _require(bool(slip_items), "Withdrawal Slip has no source items.")
    rows = frappe.db.sql(
        "SELECT name, item_code FROM \`tabWithdrawal Slip Item\` "
        "WHERE parent=%s AND parenttype='Withdrawal Slip' ORDER BY name FOR UPDATE",
        (slip.name,), as_dict=True,
    )
    expected = {r.name: r.item_code for r in slip_items}
    _require(len(expected) == len(slip_items) == len(rows),
             "Withdrawal Slip source rows changed during issuance.")
    seen = set()
    for row in rows:
        _require(row.name not in seen and expected.get(row.name) == row.item_code,
                 "Withdrawal Slip item membership or Item changed during issuance.")
        seen.add(row.name)
    return read_locked_issue_item_groups(
        frappe.db, company=slip.company, beneficiary_department=beneficiary,
        slip_rows=[{"source_row": row.name, "item_code": row.item_code} for row in rows],
    )


def resolve_locked_original_issue(slip):
    """Return provenanced B/C intents from real locked server masters, never HTTP.

    For now G3 deliberately supports exactly one Department-only Cost Center.
    Explicit-choice scopes require additional audited selector work and are
    denied rather than silently picking a default.
    """
    _required_fields()
    _canonical(slip.company, "Issue Company")
    beneficiary = resolve_beneficiary_department(slip)
    lines = _locked_source_lines(slip, beneficiary)
    posting = datetime.now(timezone.utc)
    expenses = []
    centers = []
    # Deterministic scope locks by Item Group, then beneficiary Center scope.
    for group in sorted({line.item_group for line in lines}):
        scope = _lock_scope("CCPC Expense Mapping Scope", slip.company, "item_group", group)
        records = _lock_revisions("CCPC Expense Mapping Revision", scope)
        for row in records:
            _require(row.mapping_scope == scope, "Expense revision scope mismatch.")
            if row.approval_state != "Approved":
                continue
            _assert_approval_provenance(row, slip.company)
            account = _master("Account", row.expense_account)
            expenses.append(ExpenseEvidence(
                revision=row.name, company=slip.company, item_group=group,
                expense_account=account.name, account_company=account.company,
                account_root_type=account.root_type, account_is_group=bool(account.is_group),
                account_disabled=bool(account.disabled), item_group_is_group=False,
                approval_state=row.approval_state, prepared_by=row.owner, approved_by=row.approved_by,
                approved_at=_utc(row.approved_at), effective_from=_utc(row.effective_from),
                effective_until=_utc(row.effective_until),
            ))
    # Reject unsupported multi-center scope candidates rather than an arbitrary first match.
    all_center_scopes = frappe.db.sql(
        "SELECT name, selector_kind, selector_key FROM \`tabCCPC Center Mapping Scope\` "
        "WHERE company=%s AND beneficiary_department=%s ORDER BY name FOR UPDATE",
        (slip.company, beneficiary), as_dict=True,
    )
    _require(len(all_center_scopes) == 1, "A unique approved Department Cost Center scope is required.")
    scope_row = all_center_scopes[0]
    _require(scope_row.selector_kind == "Department Only" and
             scope_row.selector_key == "DEPARTMENT_ONLY",
             "Explicit Cost Center selection is not activated.")
    records = _lock_revisions("CCPC Center Mapping Revision", scope_row.name)
    for row in records:
        _require(row.mapping_scope == scope_row.name, "Center revision scope mismatch.")
        if row.approval_state != "Approved":
            continue
        _assert_approval_provenance(row, slip.company)
        center = _master("Cost Center", row.cost_center)
        centers.append(CenterEvidence(
            revision=row.name, company=slip.company, beneficiary_department=beneficiary,
            cost_center=center.name, center_company=center.company,
            center_is_group=bool(center.is_group), center_disabled=bool(center.disabled),
            selector_mode="Department Only", approval_state=row.approval_state,
            prepared_by=row.owner, approved_by=row.approved_by, approved_at=_utc(row.approved_at),
            effective_from=_utc(row.effective_from), effective_until=_utc(row.effective_until),
        ))
    return resolve_original_issue_allocation(
        company=slip.company, beneficiary_department=beneficiary, posting_at=posting,
        lines=lines, expense_evidence=expenses, center_evidence=centers,
    )


def assign_original_issue_b_c(entry, slip):
    """Assign only to fresh, server-constructed original Material Issue details."""
    allocations = resolve_locked_original_issue(slip)
    data = [{
        "withdrawal_slip_item": row.get("withdrawal_slip_item"),
        "item_code": row.get("item_code"),
        "expense_account": row.get("expense_account"),
        "cost_center": row.get("cost_center"),
    } for row in entry.get("items") or []]
    prepared = prepare_original_issue_accounting_rows(allocations, data)
    by_source = {a.source_row: a for a in allocations}
    for row, values in zip(entry.items, prepared):
        a = by_source[values["withdrawal_slip_item"]]
        row.expense_account = values["expense_account"]
        row.cost_center = values["cost_center"]
        row.ccpc_expense_mapping_revision = a.expense_revision
        row.ccpc_center_mapping_revision = a.center_revision
        row.ccpc_item_group_at_issue = a.item_group


def validate_original_issue_b_c(entry, slip):
    """Re-resolve under locks at before_submit, guarding all accounting fields."""
    allocations = resolve_locked_original_issue(slip)
    by_source = {a.source_row: a for a in allocations}
    source = []
    for row in entry.get("items") or []:
        source.append({
            "withdrawal_slip_item": row.get("withdrawal_slip_item"),
            "item_code": row.get("item_code"),
            "expense_account": row.get("expense_account"),
            "cost_center": row.get("cost_center"),
        })
        expected = by_source.get(row.get("withdrawal_slip_item"))
        _require(expected is not None and
                 row.get("ccpc_expense_mapping_revision") == expected.expense_revision and
                 row.get("ccpc_center_mapping_revision") == expected.center_revision and
                 row.get("ccpc_item_group_at_issue") == expected.item_group,
                 "Original issued expense/category/Center revision provenance was changed.")
    assert_original_issue_rows_equal(allocations, source)

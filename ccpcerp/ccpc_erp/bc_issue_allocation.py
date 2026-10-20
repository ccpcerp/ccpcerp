# Copyright (c) 2026, CCPC and contributors
"""Inert B/C original-issue allocation validator for controlled server evidence.

IMPORTANT: This module does NOT fetch or approve mappings, authenticate users,
lock ERPNext rows, write a Stock Entry or authorize posting. Its callers MUST
supply authenticated, company-scoped, approved and database-locked evidence.
Existing CCPC mapping revision DocTypes are Draft-only and remain so.
This pure validator deliberately has no hook into Withdrawal Slip issuance.
"""

from dataclasses import dataclass
from datetime import datetime

from ccpcerp.ccpc_erp.bc_mapping_policy import (
    MappingPolicyError,
    assert_independent_review,
    is_effective_after_approval,
    select_center_revision,
)


@dataclass(frozen=True)
class IssueLine:
    source_row: str
    item_code: str
    item_group: str


@dataclass(frozen=True)
class ExpenseEvidence:
    revision: str
    company: str
    item_group: str
    expense_account: str
    account_company: str
    account_root_type: str
    account_is_group: bool
    account_disabled: bool
    item_group_is_group: bool
    approval_state: str
    prepared_by: str
    approved_by: str
    approved_at: datetime
    effective_from: datetime
    effective_until: datetime | None


@dataclass(frozen=True)
class CenterEvidence:
    revision: str
    company: str
    beneficiary_department: str
    cost_center: str
    center_company: str
    center_is_group: bool
    center_disabled: bool
    selector_mode: str
    approval_state: str
    prepared_by: str
    approved_by: str
    approved_at: datetime
    effective_from: datetime
    effective_until: datetime | None


@dataclass(frozen=True)
class IssueRowAllocation:
    source_row: str
    item_code: str
    item_group: str
    expense_account: str
    cost_center: str
    expense_revision: str
    center_revision: str
    company: str
    beneficiary_department: str


def _named(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MappingPolicyError(label + " must be a nonempty canonical identifier.")
    return value


def _is_effective_signed(evidence, posting_at):
    # The policy helper validates timezone-aware inputs and half-open intervals.
    # An earlier/later revision is not eligible merely because it exists.
    if evidence.approval_state != "Approved":
        return False
    _named(evidence.revision, "Mapping revision")
    assert_independent_review(evidence.prepared_by, evidence.approved_by)
    return is_effective_after_approval(
        evidence.effective_from, evidence.effective_until,
        evidence.approved_at, posting_at,
    )


def _validate_expense_master(evidence, company):
    _named(evidence.expense_account, "Expense Account")
    if (evidence.account_company != company
            or evidence.account_root_type != "Expense"
            or evidence.account_is_group is not False
            or evidence.account_disabled is not False
            or evidence.item_group_is_group is not False):
        raise MappingPolicyError("Expense mapping master must be an enabled Company-owned leaf Expense Account and leaf Item Group.")


def _validate_center_master(evidence, company):
    _named(evidence.cost_center, "Cost Center")
    if (evidence.center_company != company
            or evidence.center_is_group is not False
            or evidence.center_disabled is not False):
        raise MappingPolicyError("Cost Center must be an enabled Company-owned leaf.")


def resolve_original_issue_allocation(
    *, company, beneficiary_department, posting_at, lines,
    expense_evidence, center_evidence, selected_center_revision=None,
):
    """Validate synthetic **server-verified** B/C evidence, returning immutable row intents.

    The future live adapter must load *all* scoped approved candidates under
    reviewed locks, independently verify database masters, approval signatures,
    Company roles, real posting timestamp, and source-row provenance; it must
    never accept evidence or selector values directly from client requests.
    This function makes no claim of durable SLE/GL integration.
    """
    _named(company, "Company")
    _named(beneficiary_department, "Beneficiary Department")
    if not isinstance(lines, (tuple, list)) or not lines:
        raise MappingPolicyError("At least one source Withdrawal Slip row is required.")
    if not isinstance(expense_evidence, (tuple, list)) or not isinstance(center_evidence, (tuple, list)):
        raise MappingPolicyError("Mapping evidence must be server-verified lists.")
    if not isinstance(posting_at, datetime) or posting_at.tzinfo is None or posting_at.utcoffset() is None:
        raise MappingPolicyError("Posting timestamp must be timezone aware.")

    rows = []
    seen_source_rows = set()
    for line in lines:
        if not isinstance(line, IssueLine):
            raise MappingPolicyError("Invalid Withdrawal Slip source row.")
        _named(line.source_row, "Withdrawal Slip item")
        _named(line.item_code, "Item")
        _named(line.item_group, "Exact leaf Item Group")
        if line.source_row in seen_source_rows:
            raise MappingPolicyError("Duplicate Withdrawal Slip source-row reference.")
        seen_source_rows.add(line.source_row)
        rows.append(line)

    # Wrong-company/Department scopes may be present in a server-side candidate
    # collection but can NEVER become matching options for this transaction.
    matching_centers = [e for e in center_evidence if isinstance(e, CenterEvidence)
                        and e.company == company and e.beneficiary_department == beneficiary_department]
    selected_centers = [e for e in matching_centers if _is_effective_signed(e, posting_at)]
    for e in selected_centers:
        _validate_center_master(e, company)
    center_revision = select_center_revision(
        [(e.revision, e.selector_mode) for e in selected_centers], selected_center_revision,
    )
    chosen_center = next(e for e in selected_centers if e.revision == center_revision)

    allocations = []
    for line in rows:
        relevant = [e for e in expense_evidence if isinstance(e, ExpenseEvidence)
                    and e.company == company and e.item_group == line.item_group]
        eligible = [e for e in relevant if _is_effective_signed(e, posting_at)]
        if len(eligible) != 1:
            raise MappingPolicyError("An exact Item Group must have exactly one effective approved Expense revision.")
        expense = eligible[0]
        _validate_expense_master(expense, company)
        allocations.append(IssueRowAllocation(
            source_row=line.source_row, item_code=line.item_code, item_group=line.item_group,
            expense_account=expense.expense_account, cost_center=chosen_center.cost_center,
            expense_revision=expense.revision, center_revision=chosen_center.revision,
            company=company, beneficiary_department=beneficiary_department,
        ))
    return tuple(allocations)


def assert_original_issue_rows_equal(allocation, source_rows):
    """Compare current Stock Entry row values with a previously validated intent.

    This is a pure negative guard; a future before-submit hook must also
    re-resolve against the locked source and approved mapping revisions.
    """
    if not isinstance(allocation, (tuple, list)) or not allocation:
        raise MappingPolicyError("An approved allocation is required.")
    if not isinstance(source_rows, (tuple, list)) or len(source_rows) != len(allocation):
        raise MappingPolicyError("Linked Stock Entry row membership changed.")
    by_row = {r.source_row: r for r in allocation if isinstance(r, IssueRowAllocation)}
    if len(by_row) != len(allocation):
        raise MappingPolicyError("Allocation source rows are invalid or duplicated.")
    seen = set()
    for row in source_rows:
        if not isinstance(row, dict):
            raise MappingPolicyError("Invalid Stock Entry detail evidence.")
        source = row.get("withdrawal_slip_item")
        expected = by_row.get(source)
        if expected is None or source in seen:
            raise MappingPolicyError("Unrecognized or duplicated Stock Entry source row.")
        seen.add(source)
        if (row.get("item_code") != expected.item_code
                or row.get("expense_account") != expected.expense_account
                or row.get("cost_center") != expected.cost_center):
            raise MappingPolicyError("Stock Entry Item or B/C account allocation was changed.")
    if seen != set(by_row):
        raise MappingPolicyError("Stock Entry row membership does not match approved intent.")
    return True


def prepare_original_issue_accounting_rows(allocation, fresh_rows):
    """Build new voucher rows with B/C values only from validated allocations.

    INERT PURE BRIDGE: Never call this with user-provided allocation evidence. The
    future server-side caller must resolve signed, currently effective revisions
    from locked ERPNext rows, verify Item categories, and then use these copies
    ONLY while constructing a *new* Withdrawal-linked Material Issue. This does
    not authorize posting, alter existing saved drafts, or persist revisions.

    A pre-populated account/center on an input row is suspicious even if its
    value happens to equal the approved one: do not silently accept overrides
    or disguise application defaults as independently approved B/C choices.
    """
    if not isinstance(allocation, (tuple, list)) or not allocation:
        raise MappingPolicyError("An original-issue allocation is required.")
    if not isinstance(fresh_rows, (tuple, list)) or len(fresh_rows) != len(allocation):
        raise MappingPolicyError("Original issue row count differs from the approved allocation.")
    expected = {}
    for item in allocation:
        if not isinstance(item, IssueRowAllocation) or not isinstance(item.source_row, str):
            raise MappingPolicyError("Invalid approved allocation row structure.")
        if item.source_row in expected:
            raise MappingPolicyError("Duplicate approved source-row identity.")
        _named(item.expense_account, "Expense Account")
        _named(item.cost_center, "Cost Center")
        expected[item.source_row] = item

    prepared = []
    observed = set()
    for row in fresh_rows:
        if not isinstance(row, dict):
            raise MappingPolicyError("New Material Issue rows must be canonical server-built dictionaries.")
        source = row.get("withdrawal_slip_item")
        match = expected.get(source)
        if match is None or source in observed or row.get("item_code") != match.item_code:
            raise MappingPolicyError("Material Issue item/source provenance does not match the approved allocation.")
        if row.get("expense_account") not in (None, "") or row.get("cost_center") not in (None, ""):
            raise MappingPolicyError("Unapproved account or Cost Center pre-populated on new Material Issue.")
        observed.add(source)
        copy = dict(row)
        copy["expense_account"] = match.expense_account
        copy["cost_center"] = match.cost_center
        prepared.append(copy)
    if observed != set(expected):
        raise MappingPolicyError("Missing approved source rows in the original Material Issue.")
    assert_original_issue_rows_equal(allocation, prepared)
    return tuple(prepared)

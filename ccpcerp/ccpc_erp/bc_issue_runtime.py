# Copyright (c) 2026, CCPC and contributors
"""Server-side B/C evidence resolver for original Withdrawal Slip Material Issue.

Does not approve financial mappings or change permissions. Original issue
evidence is resolved from signed Item Group Expense mappings and either
signed Department Cost Center evidence or verified Company.cost_center policy.
No issuance is authorized by this module without upstream transaction guards.

Requires an existing authenticated/permissioned issue transaction, with the
Withdrawal Slip header already locked. All queries operate in that transaction.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import get_datetime, get_system_timezone, now_datetime, nowdate, nowtime

from ccpcerp.ccpc_erp.bc_issue_allocation import (
    ExpenseEvidence, CenterEvidence, CompanyCenterDefaultEvidence,
    COMPANY_DEFAULT_COST_CENTERS, resolve_original_issue_allocation,
    prepare_original_issue_accounting_rows, assert_original_issue_rows_equal,
)
from ccpcerp.ccpc_erp.bc_issue_locked_source import read_locked_issue_item_groups
from ccpcerp.ccpc_erp.bc_mapping_policy import (
    MappingPolicyError, is_effective_after_approval,
)
from ccpcerp.ccpc_erp.phase1_maintenance_use import (
    MaintenanceUseError, require_maintenance_pilot_use,
)
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
            "ccpc_item_group_at_issue", "ccpc_center_assignment_source",
        ),
        "Stock Entry": ("withdrawal_slip",),
    }.items():
        meta = frappe.get_meta(doctype)
        for name in fields:
            _require(meta.has_field(name), "Missing B/C provenance field: " + doctype + "." + name)


# Stage A: only original, CCPC Department-scoped Center proposals can be
# prepared by Stockroom. Approval, retirement, Expense and issuance checks
# continue to use the unchanged strict _real_authority policy.
STOCKROOM_CENTER_PROPOSAL_COMPANY = "Cagayan Corn Products Corporation"
STOCKROOM_CENTER_PROPOSAL_VALUE = "Main - CCPC"


def _finance_role_company_authority(user, role, company):
    """Shared locked prerequisites; grants neither approval nor posting."""
    _canonical(user, "Mapping actor")
    _require(user not in ("Administrator", "Guest"), "Named Finance mapping actors are required.")
    enabled = frappe.db.sql("SELECT enabled FROM `tabUser` WHERE name=%s FOR UPDATE",
                            (user,), as_dict=True)
    _require(len(enabled) == 1 and enabled[0].get("enabled") == 1,
             "Mapping actor is not an enabled ERPNext user.")
    assigned = frappe.db.sql(
        "SELECT name FROM `tabHas Role` WHERE parenttype='User' "
        "AND parent=%s AND role=%s ORDER BY name FOR UPDATE",
        (user, role), as_dict=True,
    )
    _require(bool(assigned), "Mapping actor lacks an explicit Finance mapping role.")
    granted = frappe.db.sql(
        "SELECT name FROM `tabUser Permission` WHERE user=%s AND allow='Company' "
        "AND for_value=%s AND apply_to_all_doctypes=1 "
        "AND IFNULL(applicable_for, '')='' ORDER BY name FOR UPDATE",
        (user, company), as_dict=True,
    )
    _require(bool(granted), "Mapping actor lacks explicit Company-scoped permission.")


def _has_locked_mapping_role(user, role):
    rows = frappe.db.sql(
        "SELECT name FROM `tabHas Role` WHERE parenttype='User' AND parent=%s "
        "AND role=%s ORDER BY name FOR UPDATE",
        (user, role), as_dict=True,
    )
    return bool(rows)


def _real_authority(user, role, company):
    """Strict Finance authority for every action except scoped Center proposals.

    No Administrator, Guest, System Manager or Stockroom override.
    """
    _finance_role_company_authority(user, role, company)
    _require(not _has_locked_mapping_role(user, "Stockroom Personnel"),
             "Stockroom personnel cannot authorize financial mapping revisions.")


def _center_proposal_maker_authority(user, kind, company, value=None):
    """Permit Stockroom only for CCPC/Main Center Draft proposals.

    This must be called both at Draft creation and when a separate Controller
    signs/approves that exact Draft. None means *context/search only*; callers
    creating or approving a Draft MUST pass the actual saved Cost Center value.
    """
    if not _has_locked_mapping_role(user, "Stockroom Personnel"):
        return _real_authority(user, "CCPC Mapping Preparer", company)
    _require(kind == "Center" and company == STOCKROOM_CENTER_PROPOSAL_COMPANY,
             "Stockroom may prepare only CCPC Cost Center proposals.")
    _require(value is None or value == STOCKROOM_CENTER_PROPOSAL_VALUE,
             "Stockroom proposals require the approved shared Cost Center candidate.")
    _finance_role_company_authority(user, "CCPC Mapping Preparer", company)
    _require(not _has_locked_mapping_role(user, "CCPC Accounting Mapping Approver"),
             "Stockroom cannot be a Finance mapping approver.")


def _assert_approval_provenance(revision, company):
    """Require event-time signed evidence; do not recheck former users' current roles."""
    maker = _canonical(revision.get("owner"), "Mapping preparer")
    checker = _canonical(revision.get("approved_by"), "Mapping approver")
    _require(maker.casefold() != checker.casefold(), "Maker and Checker must differ.")
    reference = _canonical(revision.get("approval_reference"), "Accounting decision")
    _require(revision.get("approval_state") == "Approved" and revision.get("approved_at"),
             "Mapping does not have a valid historical approval.")
    kind = "Expense" if "expense_account" in revision else "Center"
    # Explicitly scoped disposable CI fixture only; NEVER a real Finance signoff.
    if reference == "CIRCLECI-SYNTHETIC-APPROVAL-NOT-FINANCE-AUTHORIZATION":
        from ccpcerp.ccpc_erp.ci_disposable_site_guard import is_reviewed_circleci_disposable_site
        _require(is_reviewed_circleci_disposable_site(getattr(frappe.local, "site", None)),
                 "Synthetic test approvals are forbidden outside the disposable CI site.")
        return
    rows = frappe.db.sql(
        "SELECT d.name, d.kind, d.action, d.revision, d.company, d.mapping_scope, "
        "d.financial_value, d.maker, d.signed_by, d.signed_at, d.status, "
        "u.revision AS used_revision, u.action AS used_action "
        "FROM " + chr(96) + "tabCCPC Accounting Mapping Decision" + chr(96) + " d "
        "INNER JOIN " + chr(96) + "tabCCPC Mapping Decision Use" + chr(96) + " u "
        "ON d.name=u.decision WHERE d.name=%s FOR UPDATE",
        (reference,), as_dict=True,
    )
    _require(len(rows) == 1, "Missing or duplicate signed Accounting decision evidence.")
    d = rows[0]
    value_field = "expense_account" if kind == "Expense" else "cost_center"
    _require(d.status == "Signed" and d.kind == kind and d.action == "Approve"
             and d.revision == revision.name and d.used_revision == revision.name
             and d.used_action == "Approve"
             and d.company == company and d.mapping_scope == revision.mapping_scope
             and d.financial_value == revision[value_field]
             and d.maker == maker and d.signed_by == checker
             and d.signed_at and _utc(d.signed_at) <= _utc(revision.approved_at),
             "Approved mapping provenance does not match its signed decision.")


def _lock_scope(doctype, company, scope_field, scope_value, *, department_only=False):
    _canonical(company, "Company")
    _canonical(scope_value, "Mapping scope")
    suffix = " AND selector_kind='Department Only' AND selector_key='DEPARTMENT_ONLY'" if department_only else ""
    rows = frappe.db.sql(
        "SELECT name, company, " + scope_field +
        " FROM `tab" + doctype + "` WHERE company=%s AND " + scope_field +
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

def _effective_mapping_end(kind, company, scope_id, row):
    """Lock and apply append-only retirement cutoff without changing Approved history."""
    rows = frappe.db.sql(
        "SELECT name,mapping_scope,company,approved_at,cutoff_at,decision,"
        "retirement_request FROM " +
        chr(96) + "tabCCPC Mapping Retirement Event" + chr(96) +
        " WHERE kind=%s AND revision=%s ORDER BY name FOR UPDATE",
        (kind, row.name), as_dict=True,
    )
    _require(len(rows) <= 1, "Duplicate accepted mapping retirements.")
    original = _utc(row.effective_until)
    if not rows:
        return original
    e = rows[0]
    _require(e.company == company and e.mapping_scope == scope_id and
             e.approved_at and e.cutoff_at and e.decision and e.retirement_request,
             "Retirement event does not match its Company and Scope.")
    refs = frappe.db.sql(
        "SELECT name FROM " + chr(96) + "tabCCPC Mapping Decision Use" + chr(96) +
        " WHERE decision=%s AND revision=%s AND action='Retire' "
        "AND event_ref=%s FOR UPDATE",
        (e.decision, row.name, e.name), as_dict=True,
    )
    _require(len(refs) == 1, "Retirement event lacks signed one-use evidence.")
    cutoff = _utc(e.cutoff_at)
    _require(cutoff >= _utc(e.approved_at) and cutoff > _utc(row.effective_from),
             "Invalid retrospective retirement cutoff.")
    return min(original, cutoff) if original else cutoff


def _master(doctype, record):
    _canonical(record, doctype)
    rows = frappe.db.sql(
        "SELECT name, company, is_group, disabled" +
        (", root_type" if doctype == "Account" else "") +
        " FROM `tab" + doctype + "` WHERE name=%s FOR UPDATE",
        (record,), as_dict=True,
    )
    result = _one(rows, doctype)
    _require(result.name == record, "Locked financial master identity mismatch.")
    return result


def _locked_source_lines(slip, beneficiary):
    slip_items = list(slip.get("items") or [])
    _require(bool(slip_items), "Withdrawal Slip has no source items.")
    rows = frappe.db.sql(
        "SELECT name, item_code FROM `tabWithdrawal Slip Item` "
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


def _validated_voucher_posting_utc(entry):
    """Use the posted voucher instant; prevent unapproved backdating/cutoff bypass.

    Initial review release path accepts server-stamped same-day issues only.
    Formal future G2 exception/backposting policy requires Accounting signoff.
    """
    _require(entry.get("set_posting_time") in (1, True),
             "Original Withdrawal issues require a server-stamped posting instant.")
    local_now = now_datetime()
    date = entry.get("posting_date")
    clock = entry.get("posting_time")
    _require(str(date) == nowdate() and bool(clock),
             "Original issue posting must use current site-local date and time.")
    chosen = get_datetime(str(date) + " " + str(clock))
    if chosen.tzinfo is None:
        chosen = chosen.replace(tzinfo=ZoneInfo(get_system_timezone()))
    posted = chosen.astimezone(timezone.utc)
    age = (_utc(local_now) - posted).total_seconds()
    _require(-30 <= age <= 180,
             "Issue posting timestamp is stale, future, or backdated.")
    return posted


def stamp_original_issue_posting(entry):
    """Server-only newly constructed issue timestamp, never caller-supplied."""
    entry.set_posting_time = 1
    entry.posting_date = nowdate()
    entry.posting_time = nowtime()
    _validated_voucher_posting_utc(entry)


def resolve_locked_original_issue(slip, entry):
    """Return provenanced B/C intents from real locked server masters, never HTTP.

    Prefer existing independently signed Department-scoped Center evidence.
    When the beneficiary has no Center mapping scope, use only the exact
    Company.cost_center master approved in the CCPC/RMC policy allowlist.
    Never bypass a conflicting or inactive Department-specific scope.
    """
    _required_fields()
    _canonical(slip.company, "Issue Company")
    beneficiary = resolve_beneficiary_department(slip)
    lines = _locked_source_lines(slip, beneficiary)
    # Locked source Item Groups, not HTTP inputs or editable Stock Entry rows.
    # Shared resolver runs for both issue creation and before_submit recheck.
    try:
        require_maintenance_pilot_use(
            slip.company, beneficiary,
            tuple(line.item_group for line in lines),
            slip.get("ccpc_material_use"),
        )
    except MaintenanceUseError as exc:
        raise MappingPolicyError(str(exc)) from exc
    posting = _validated_voucher_posting_utc(entry)
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
            # Historical Approved rows remain signed for audit, but must not
            # require still-active roles or enabled masters after their cutoff.
            computed_end = _effective_mapping_end("Expense", slip.company, scope, row)
            if not is_effective_after_approval(
                _utc(row.effective_from), computed_end,
                _utc(row.approved_at), posting,
            ):
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
                effective_until=computed_end,
            ))
    # Reject unsupported multi-center scope candidates rather than an arbitrary first match.
    all_center_scopes = frappe.db.sql(
        "SELECT name, selector_kind, selector_key FROM `tabCCPC Center Mapping Scope` "
        "WHERE company=%s AND beneficiary_department=%s ORDER BY name FOR UPDATE",
        (slip.company, beneficiary), as_dict=True,
    )
    _require(len(all_center_scopes) <= 1, "Multiple Department Cost Center scopes require Finance review.")
    company_center_default = None
    if not all_center_scopes:
        # Lock Company settings and the leaf master for both creation and
        # before_submit. Neither HTTP caller nor an Item default can choose it.
        expected_center = COMPANY_DEFAULT_COST_CENTERS.get(slip.company)
        _require(bool(expected_center), "This Company has no authorized default Cost Center policy.")
        companies = frappe.db.sql(
            "SELECT name, cost_center FROM `tabCompany` WHERE name=%s FOR UPDATE",
            (slip.company,), as_dict=True,
        )
        _require(len(companies) == 1 and companies[0].name == slip.company
                 and companies[0].cost_center == expected_center,
                 "Company default Cost Center is not the authorized CCPC/RMC main Center.")
        center = _master("Cost Center", expected_center)
        _require(center.company == slip.company and not center.is_group and not center.disabled,
                 "Company default Cost Center must be an enabled Company-owned leaf.")
        company_center_default = CompanyCenterDefaultEvidence(
            company=slip.company, cost_center=expected_center,
            center_company=center.company, center_is_group=bool(center.is_group),
            center_disabled=bool(center.disabled),
        )
    else:
        scope_row = all_center_scopes[0]
        _require(scope_row.selector_kind == "Department Only" and
                 scope_row.selector_key == "DEPARTMENT_ONLY",
                 "Explicit Cost Center selection is not activated.")
        records = _lock_revisions("CCPC Center Mapping Revision", scope_row.name)
        for row in records:
            _require(row.mapping_scope == scope_row.name, "Center revision scope mismatch.")
            if row.approval_state != "Approved":
                continue
            computed_end = _effective_mapping_end("Center", slip.company, scope_row.name, row)
            if not is_effective_after_approval(
                _utc(row.effective_from), computed_end,
                _utc(row.approved_at), posting,
            ):
                continue
            _assert_approval_provenance(row, slip.company)
            center = _master("Cost Center", row.cost_center)
            centers.append(CenterEvidence(
                revision=row.name, company=slip.company, beneficiary_department=beneficiary,
                cost_center=center.name, center_company=center.company,
                center_is_group=bool(center.is_group), center_disabled=bool(center.disabled),
                selector_mode="Department Only", approval_state=row.approval_state,
                prepared_by=row.owner, approved_by=row.approved_by, approved_at=_utc(row.approved_at),
                effective_from=_utc(row.effective_from), effective_until=computed_end,
            ))
    allocations = resolve_original_issue_allocation(
        company=slip.company, beneficiary_department=beneficiary, posting_at=posting,
        lines=lines, expense_evidence=expenses, center_evidence=centers,
        company_center_default=company_center_default,
    )
    # A server-stamped voucher can wait on Finance locks while a future
    # replacement or retirement crosses its cutoff. Do not post an old
    # mapping after its closure just because the voucher timestamp predates
    # the cutoff. Reject the crossing and require a fresh server-stamped issue.
    check_now = _utc(now_datetime())
    selected = {e.revision: e for e in expenses + centers}
    for allocation in allocations:
        for name in (allocation.expense_revision, allocation.center_revision):
            if name is None:
                _require(allocation.center_source == "Company Default",
                         "Unsigned Center is not a verified Company default.")
                continue
            evidence = selected[name]
            _require(is_effective_after_approval(
                evidence.effective_from, evidence.effective_until,
                evidence.approved_at, check_now,
            ), "Financial mapping cutoff crossed during issue; retry a fresh voucher.")
    return allocations


def assign_original_issue_b_c(entry, slip):
    """Assign only to fresh, server-constructed original Material Issue details."""
    allocations = resolve_locked_original_issue(slip, entry)
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
        row.ccpc_center_assignment_source = a.center_source
        row.ccpc_item_group_at_issue = a.item_group


def validate_original_issue_b_c(entry, slip):
    """Re-resolve under locks at before_submit, guarding all accounting fields."""
    allocations = resolve_locked_original_issue(slip, entry)
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
                 (row.get("ccpc_center_mapping_revision") or None) == expected.center_revision and
                 row.get("ccpc_center_assignment_source") == expected.center_source and
                 row.get("ccpc_item_group_at_issue") == expected.item_group,
                 "Original issued expense/category/Center revision provenance was changed.")
    assert_original_issue_rows_equal(allocations, source)

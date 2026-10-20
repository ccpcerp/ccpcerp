# Copyright (c) 2026, CCPC and contributors
"""Read-only (FOR UPDATE) source master snapshot for future B/C integration.

INERT: Not wired into Withdrawal Slip or Stock Entry hooks, and cannot approve
financial mappings or post stock. Caller must already own the transaction and
have locked/validated the Withdrawal Slip with a permissioned issuer. No commit,
rollback, role elevation or user-supplied SQL identifiers are performed here.
"""

from ccpcerp.ccpc_erp.bc_issue_allocation import IssueLine
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError


def _name(value, description):
    if not isinstance(value, str) or not value or value != value.strip():
        raise MappingPolicyError(description + " must be a canonical nonempty name.")
    return value


def _non_group_enabled(row, flags, label):
    if row is None:
        raise MappingPolicyError(label + " was not found.")
    if any(row.get(field) not in (0, False) for field in flags):
        raise MappingPolicyError(label + " must be active and not a group.")


def read_locked_issue_item_groups(db, *, company, beneficiary_department, slip_rows):
    """Lock source Item/Department/Item Group masters and return Item Group facts.

    The source Slip and its exact child rows must be independently locked and
    verified by the issuer; this helper does NOT prove Slip provenance or
    replace company-specific approval, mapping, or permission checks.
    
    db must be the authenticated server's frappe.db, never an HTTP argument.
    The SQL rows are canonical ERPNext sources, not client-declared categories.
    """
    _name(company, "Company")
    _name(beneficiary_department, "Beneficiary Department")
    if not isinstance(slip_rows, (tuple, list)) or not (0 < len(slip_rows) <= 1000):
        raise MappingPolicyError("Expected 1–1000 locked Withdrawal Slip source rows.")
    seen_source = set()
    names = set()
    for row in slip_rows:
        if not isinstance(row, dict):
            raise MappingPolicyError("Locked Withdrawal source rows must be canonical server records.")
        source = _name(row.get("source_row"), "Slip item")
        item = _name(row.get("item_code"), "Stock Item")
        if source in seen_source:
            raise MappingPolicyError("Duplicated source row in Withdrawal Slip.")
        seen_source.add(source)
        names.add(item)

    # Stable lock order; never use Frappe get_all() for original issue master facts.
    dept = db.sql(
        "SELECT name, company, is_group, disabled FROM `tabDepartment` "
        "WHERE name=%s FOR UPDATE", (beneficiary_department,), as_dict=True,
    )
    if len(dept) != 1 or dept[0].get("name") != beneficiary_department or dept[0].get("company") != company:
        raise MappingPolicyError("Beneficiary Department must exist in the issue Company.")
    _non_group_enabled(dept[0], ("is_group", "disabled"), "Beneficiary Department")

    items_sorted = sorted(names)
    markers = ", ".join(["%s"] * len(items_sorted))
    items = db.sql(
        "SELECT name, item_group, disabled, is_stock_item FROM `tabItem` "
        "WHERE name IN (" + markers + ") ORDER BY name FOR UPDATE",
        tuple(items_sorted), as_dict=True,
    )
    if len(items) != len(items_sorted):
        raise MappingPolicyError("One or more source Items no longer exist.")
    by_item = {}
    for record in items:
        item_id = record.get("name")
        if item_id not in names or item_id in by_item or record.get("disabled") not in (0, False) or record.get("is_stock_item") not in (1, True):
            raise MappingPolicyError("Source Item must be an enabled stock Item.")
        by_item[item_id] = _name(record.get("item_group"), "Item Group")
    groups = sorted(set(by_item.values()))
    group_marks = ", ".join(["%s"] * len(groups))
    master_groups = db.sql(
        "SELECT name, is_group FROM `tabItem Group` WHERE name IN (" + group_marks + ") "
        "ORDER BY name FOR UPDATE",
        tuple(groups), as_dict=True,
    )
    if len(master_groups) != len(groups):
        raise MappingPolicyError("One or more Item Groups no longer exist.")
    seen_groups = set()
    for group in master_groups:
        group_name = group.get("name")
        if group_name not in groups or group_name in seen_groups or group.get("is_group") not in (0, False):
            raise MappingPolicyError("Material Issue Item Groups must be exact active leaves.")
        seen_groups.add(group_name)

    return tuple(
        IssueLine(row["source_row"], row["item_code"], by_item[row["item_code"]])
        for row in slip_rows
    )

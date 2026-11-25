# Copyright (c) 2026, CCPC and contributors
"""Fail-closed maintenance-only policy for the initial CCPC Bearings withdrawal.

Pure policy logic; no database, network, or ERPNext side effects. Source Item
Groups and beneficiary Department MUST be resolved from server-validated
Company-scoped records, not from a client request or editable Stock Entry.
"""

PILOT_COMPANY = "Cagayan Corn Products Corporation"
PILOT_ITEM_GROUP = "Bearings"
# Legacy fixture/reference label; not a posting restriction after same-Company
# beneficiary validation and approved Department-scoped Finance mapping checks.
PILOT_BENEFICIARY = "Maintenance Department - CCPC"
ROUTINE_MAINTENANCE = "Routine Maintenance"


class MaintenanceUseError(ValueError):
    """An issue is outside the signed routine-maintenance expense pilot."""


def require_maintenance_pilot_use(
    company, beneficiary_department, item_groups, material_use
):
    """Reject capital, unclassified, malformed-beneficiary or mixed Bearings uses.

    The upstream issue resolver establishes a real, enabled, same-Company
    Department from locked server masters. This pure policy checks that
    validated beneficiary's canonical name, not whether it is Maintenance.
    The resolver separately requires an approved, effective Finance Center
    mapping for the actual beneficiary. Other groups/Companies are unchanged.
    """
    if not isinstance(item_groups, (list, tuple, set, frozenset)) or not item_groups:
        raise MaintenanceUseError("Issue requires server-validated Item Groups.")
    if any(not isinstance(group, str) or not group for group in item_groups):
        raise MaintenanceUseError("Issue contains an invalid Item Group.")

    groups = set(item_groups)
    if company != PILOT_COMPANY or PILOT_ITEM_GROUP not in groups:
        return False

    if groups != {PILOT_ITEM_GROUP}:
        raise MaintenanceUseError(
            "CCPC Bearings pilot cannot mix other Item Groups in one Withdrawal Slip."
        )
    if (
        not isinstance(beneficiary_department, str)
        or not beneficiary_department
        or beneficiary_department != beneficiary_department.strip()
    ):
        raise MaintenanceUseError(
            "CCPC Bearings routine maintenance requires a valid canonical "
            "same-Company beneficiary Department."
        )
    if material_use != ROUTINE_MAINTENANCE:
        raise MaintenanceUseError(
            "CCPC Bearings expense mapping is restricted to explicitly "
            "classified Routine Maintenance. Capital projects, other use "
            "and unclassified requests require a separate Accounting route."
        )
    return True

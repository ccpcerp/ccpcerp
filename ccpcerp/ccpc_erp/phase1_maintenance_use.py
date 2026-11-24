# Copyright (c) 2026, CCPC and contributors
"""Fail-closed maintenance-only policy for the initial CCPC Bearings withdrawal.

Pure policy logic; no database, network, or ERPNext side effects. Source Item
Groups and beneficiary Department MUST be resolved from server-validated
Company-scoped records, not from a client request or editable Stock Entry.
"""

PILOT_COMPANY = "Cagayan Corn Products Corporation"
PILOT_ITEM_GROUP = "Bearings"
PILOT_BENEFICIARY = "Maintenance Department - CCPC"
ROUTINE_MAINTENANCE = "Routine Maintenance"


class MaintenanceUseError(ValueError):
    """An issue is outside the signed routine-maintenance expense pilot."""


def require_maintenance_pilot_use(
    company, beneficiary_department, item_groups, material_use
):
    """Reject capital, unclassified, foreign-beneficiary, or mixed Bearings issues.

    Only the exact CCPC Bearings pilot is gated. Other Item Groups and
    Companies preserve their existing mapping policy; extending the policy
    requires a separately approved business and code change.
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
    if beneficiary_department != PILOT_BENEFICIARY:
        raise MaintenanceUseError(
            "CCPC Bearings routine maintenance issues require the approved "
            "same-Company Maintenance beneficiary Department."
        )
    if material_use != ROUTINE_MAINTENANCE:
        raise MaintenanceUseError(
            "CCPC Bearings expense mapping is restricted to explicitly "
            "classified Routine Maintenance. Capital projects, other use "
            "and unclassified requests require a separate Accounting route."
        )
    return True

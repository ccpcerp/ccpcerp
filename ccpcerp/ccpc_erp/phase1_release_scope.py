# Copyright (c) 2026, CCPC and contributors
"""Phase 1 release scope: same-Company stock withdrawal only.

This module deliberately has no Frappe/SQL side effects.  Call from both the
normal Stockroom API and the linked Stock Entry validation path.  It guards
posting; it does not rewrite, delete or relabel older cross-Company records.
"""
class Phase1ScopeError(ValueError):
    """A requested Material Issue falls outside the approved Phase 1 scope."""


def require_same_company_issue(issuing_company, requesting_company):
    """Reject absent/ambiguous names and every cross-Company Withdrawal issue.

    The issuer owns the source warehouse and posted Stock Entry/GL.  A request
    charged to another legal Company is reserved for the later, independently
    approved intercompany financial flow.  Never treat issuer SLE/expense as
    evidence of another Company's booked Department cost.
    """
    for label, value in (
        ("Issuing Company", issuing_company),
        ("Requesting Company", requesting_company),
    ):
        if not isinstance(value, str) or not value or value != value.strip():
            raise Phase1ScopeError(label + " must be an exact nonempty Company record ID.")
    if issuing_company != requesting_company:
        raise Phase1ScopeError(
            "Phase 1 allows Material Issue only when the Withdrawal Slip's "
            "issuing and requesting Companies match. Intercompany "
            "withdrawal/billing is not released."
        )
    return issuing_company


def require_billing_disabled():
    """Fail-closed server-side gate for the unreleased custom billing service."""
    raise Phase1ScopeError(
        "CCPC intercompany material billing is disabled in the Phase 1 "
        "Withdrawal/Department-cost release."
    )

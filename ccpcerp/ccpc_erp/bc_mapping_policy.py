# Copyright (c) 2026, CCPC and contributors
"""Pure, inert G1-G3 policy checks for *future* controlled B/C mappings.

NO mapping approval, ERPNext role lookup, database lookup, Stock Entry hook, or
posting operation is implemented or authorized by this module. All inputs
must originate from authenticated/locked server-side data in later code.
"""

from datetime import datetime, timezone


class MappingPolicyError(ValueError):
    """A proposed operation cannot be treated as an authorized mapping."""


def assert_independent_review(preparer_user, approver_user):
    """Check G1 identity separation, NOT Accounting role or Company permission.

    Later server action MUST independently verify the approver's logged-in
    identity, role, Company scope and approved Accounting decision.
    """
    if not isinstance(preparer_user, str) or not isinstance(approver_user, str):
        raise MappingPolicyError("Authenticated preparer and approver identities are required.")
    maker, checker = preparer_user.strip(), approver_user.strip()
    if (not maker or not checker
            or maker.casefold() in {"guest", "administrator"}
            or checker.casefold() in {"guest", "administrator"}):
        raise MappingPolicyError("An independent named Accounting reviewer is required.")
    if maker.casefold() == checker.casefold():
        raise MappingPolicyError("A mapping preparer cannot approve their own revision.")
    return maker, checker


def _as_utc(value, fieldname):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MappingPolicyError(fieldname + " must be a timezone-aware datetime.")
    return value.astimezone(timezone.utc)


def _interval(start, until):
    begin = _as_utc(start, "effective_from")
    end = None if until is None else _as_utc(until, "effective_until")
    if end is not None and end <= begin:
        raise MappingPolicyError("A mapping validity interval must end after it starts.")
    return begin, end


def intervals_overlap(start_a, until_a, start_b, until_b):
    """Half-open [from, until) intervals; adjacency is not an overlap.

    This is only the interval arithmetic for G2, NOT a DB race guard.
    Actual revision approval must acquire stable scope locks, test current
    approved records and validate the Accounting-authorized cutoff policy.
    """
    a, a_end = _interval(start_a, until_a)
    b, b_end = _interval(start_b, until_b)
    return (a_end is None or b < a_end) and (b_end is None or a < b_end)


def is_effective_after_approval(effective_from, effective_until, approved_at, posting_at):
    """A hypothetical signed revision cannot retrospectively authorize issue.

    Requires external proof of a genuine Accounting approval. Backposting,
    operational cutoff and exact timestamps remain to be approved.
    """
    start, end = _interval(effective_from, effective_until)
    approval = _as_utc(approved_at, "approved_at")
    posting = _as_utc(posting_at, "posting_at")
    return approval <= posting and start <= posting and (end is None or posting < end)


def select_center_revision(eligible_signed_choices, selected_revision=None):
    """G3: choose a unique server-verified revision; never use a first match.

    eligible_signed_choices: iterable of (revision_name, mode) tuples derived
    ONLY from already independently approved, effective, company-matching,
    beneficiary-matching, locked mapping rows in a future server resolver.
    This pure function CANNOT establish eligibility or approval itself.

    The only allowed modes are 'Department Only' and 'Explicit Selection'.
    One eligible revision may resolve uniquely. Multiple authorized Centers
    demand *all* explicit-choice scopes and a positive specific selection;
    mixing a Department Only fallback with explicit alternatives fails closed.
    """
    if not isinstance(eligible_signed_choices, (tuple, list)):
        raise MappingPolicyError("Eligible Center choices must be server-validated.")
    values = []
    seen = set()
    for item in eligible_signed_choices:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise MappingPolicyError("Invalid eligible Center revision structure.")
        revision, mode = item
        if (not isinstance(revision, str) or not revision.strip()
                or mode not in ("Department Only", "Explicit Selection")):
            raise MappingPolicyError("Invalid signed Center revision or scope mode.")
        revision = revision.strip()
        if revision in seen:
            raise MappingPolicyError("Duplicate eligible Center revision detected.")
        seen.add(revision)
        values.append((revision, mode))
    if not values:
        raise MappingPolicyError("No approved eligible Center revision was provided.")
    if len(values) > 1 and any(mode != "Explicit Selection" for _, mode in values):
        raise MappingPolicyError("Department-only Center and explicit options cannot compete.")
    if selected_revision is None or selected_revision == "":
        if len(values) != 1:
            raise MappingPolicyError("Multiple eligible Centers require positive revision selection.")
        return values[0][0]
    if not isinstance(selected_revision, str) or selected_revision not in seen:
        raise MappingPolicyError("Selected Center revision is not among the approved eligible choices.")
    return selected_revision

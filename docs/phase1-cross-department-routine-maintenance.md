# Phase 1 — cross-Department routine Bearings maintenance (review candidate)

**Owner-approved policy correction, 2026-11-25.** This file documents a source change on a dedicated GitHub **review branch**. It is **not** a live DEVELOPMENT migration, financial sign-off, PR merge or PRODUCTION release.

## Business reality and attribution

Maintenance personnel may request or perform repairs on equipment that benefits **Wet Mill Plant** or another eligible Department within the **same issuing Company**. Requesting Department and **Work For Department** are not interchangeable. The Withdrawal Slip must preserve the actual beneficiary; changing the beneficiary to Maintenance merely to pass an old safeguard would misstate material consumption.

The operational request that started this project is to report **the quantity and stock valuation cost of materials consumed by each Department through Withdrawal Slips**, independently of whether a Department has an exclusive GL Cost Center. The existing `CCPC Department Material Consumption` report uses the frozen original Stock Entry header/detail Department and original outbound Stock Ledger valuation; it is a **gross original issue report**, not net consumption, returns, billing, or independently certified departmental GL expense. Source quality flags must remain visible.

## Approved, narrowly scoped change

For exact issuing Company `Cagayan Corn Products Corporation` and server-confirmed exact Item Group `Bearings`, a genuine `Routine Maintenance` Withdrawal Slip may name **any valid, enabled, non-group Department of the same Company** as `Work For Department`, including `Wet Mill Plant - CCPC`. The pure maintenance policy no longer hardcodes `Maintenance Department - CCPC` as the only acceptable beneficiary. A helper's canonical nonempty Department check is **not** standalone Company authorization: runtime must continue to resolve the actual beneficiary and Equipment Registry consistency via `withdrawal_issue_beneficiary.resolve_beneficiary_department` and verify locked Company/Department/Item masters via `bc_issue_locked_source.read_locked_issue_item_groups`.

**Do not loosen other safeguards:** capital/improvement, other/unclassified/blank classification, mixed Bearings/non-Bearings, direct unlinked Bearings Material Issue, cross-Company requests, inactive/foreign/master-mismatched beneficiaries, wrong Equipment affiliation, missing authorized named Stockroom issuer, double posting, failed transaction rollback, signed Finance mapping requirements and real SLE/GL accounting remain blocked.

## Department is not the same as Cost Center

The original-issue Finance resolver still requires:
- exactly one **Department Only** Cost Center Mapping Scope for the **actual beneficiary** within the issuing Company;
- one effective and independently approved/signed Center Revision for that scope, with a valid same-Company Cost Center;
- an effective independently approved/signed Expense Mapping Revision for the **Item Group**, plus normal accounts and inventory dimensions.

This design **does not require a different Cost Center master for every Department**. Finance may approve the same shared Cost Center (e.g., a company-level Main center) in multiple beneficiary-specific Department scopes, *if correct for the Company's actual accounting policy*. Those explicit approvals preserve auditability; do **not** auto-create scopes or silently reuse Maintenance's signed mapping for Wet Mill. Department material consumption reporting groups by the actual Department on original posted issue rows, irrespective of whether Cost Center masters are shared.

Whether CCPC should later simplify the separate per-beneficiary Finance Center mapping approvals, or certify Department-level net/GL charge totals, is a **distinct accounting and report design decision**; this review branch does not assume that authority.

## Tests and scope

- Pure policy test: ordinary Maintenance and cross-Department Wet Mill/Cogen canonical beneficiaries pass; malformed/missing beneficiary, nonroutine/capital/other/blank material use, mixed groups and wrong Company scope stay blocked or out of pilot.
- Native disposable Frappe/ERPNext/MariaDB test: an alternate same-Company beneficiary is denied before Finance signs its **own** Center scope (no new SLE/GL); after separate maker/checker approval, posts once with one inventory decrement, GL balance, **actual beneficiary** on Stock Entry and expense GL, shared approved Cost Center, and one verified row in Department Material Consumption using original SLE value.
- Existing 10-case native suite, cancellation/reissue, authenticated HTTP/CSRF/rollback/retry/replay, and static source gates must be rerun on the updated combined candidate before any MAIN DEVELOPMENT rollout decision.

**Not yet verified at authoring time:** native MariaDB execution of these modified tests, exact live Item master group for the operator's chosen item, Equipment Registry beneficiary affiliation, current Wet Mill Center mapping and provenance, named-user privileges, DEV site state, posting/reports. Do not press Issue Materials on the current draft until those independent checks pass and a separately reviewed DEV rollout occurs.

**Operational hold:** Preserve the current Draft Withdrawal Slip and its truthful beneficiary. GitHub is source of reviewed code; DEVELOPMENT and PRODUCTION are not to be changed by mere GitHub publication.

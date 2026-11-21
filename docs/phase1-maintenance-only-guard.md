# Phase 1 CCPC Bearings maintenance-use safeguard — review only

**Branch:** `feature/phase1-maintenance-only-2026-11-21`, based on the reviewed Phase 1 release branch, not the default branch.

**Status:** GitHub implementation for review. **Not merged, not pulled to DEVELOPMENT, not migrated or tested against a live MariaDB site.** No PRODUCTION operation is permitted without separate release authorization.

## Why this is needed

Expense Account Mapping selects the expense nature for a same-Company, original Material Issue based on an approved Item Group→Expense Account revision. It is **not** evidence that any given use of a bearing should be expensed rather than capitalized. Department represents the actual same-Company beneficiary; Cost Center is a distinct signed financial dimension. The pilot Item Group has 19 bearing items. A group-wide expense mapping cannot automatically discern a routine repair from a capital improvement.

## Selected interim scope

- Company: Cagayan Corn Products Corporation **only**.
- Item Group: exact leaf `Bearings`.
- Beneficiary: `Maintenance Department - CCPC` **only**.
- New Withdrawal Slip structured, blank-by-default field: `ccpc_material_use`.
- Choice `Routine Maintenance`: acceptable only for a genuine maintenance request, using the signed mapping once effective.
- Choices `Capital Project / Improvement`, `Other / Unclassified`, or no classification: **cannot use the Bearings maintenance issue route**.
- Mixed-item-group slips containing Bearings: **rejected** until separately designed and approved.
- Other Companies and Item Groups: existing Phase 1 posting policy unchanged. The guard is **not** a generalized capitalization engine.

## Server enforcement and auditability

1. New structured classification is a part of Withdrawal Slip **request intent** and cannot normally be changed after approval. No automatic default to Routine Maintenance.
2. During authorized Stockroom issue creation and linked Stock Entry validation on submission, the issue resolver locks/verifies original Slip rows and Item/Item Group masters, resolves actual same-Company beneficiary, then checks the structured classification **before** looking up/allocating any Expense Account or Cost Center. No trust in client-entered Item Groups.
3. The locked parent snapshot includes `ccpc_material_use`, and optimistic stale-Slip comparisons include it, preventing a changed approved Slip from being processed with a stale permissioned Stock Entry.
4. The Stock Entry `validate` and `before_submit` hooks also block **unlinked CCPC Bearings Material Issues**, including direct stock posting. Other direct non-Bearings operations and material receipts retain their existing treatment.
5. The existing signed Finance revision check remains mandatory; this safeguard does **not** activate the Draft Expense mapping or bypass Accounting approval.
6. This guard cannot establish the **truth** of a user-selected Routine Maintenance classification. Responsible requestor/supervisor review, supporting work order/evidence, and Accounting exception procedures remain required. Deliberate false classification, database writes by privileged administrators, and broader ERPNext voucher routes require separate governance/testing.

## Regression and acceptance plan

- Pure unit tests cover Routine Maintenance acceptance; missing/capital/other/bad classification rejection; wrong beneficiary and mixed group rejection; non-pilot scopes unaffected.
- Static wiring tests cover new metadata, request-intent immutability, locked parent snapshot, shared creation/submit policy resolver, unlinked direct Stock Entry gate, and `validate`/`before_submit` hook presence.
- A small same-repository GitHub PR CI workflow compiles application Python and runs both suites plus existing release policy/wiring smoke tests. **No DB or deployment, no production credentials.**
- On a **separately authorized disposable site**, test (a) valid routine CCPC Bearings request with signed Center + Expense revisions posts once; header/item Department, SLE, GL and original-issue report reconcile, (b) missing/capital/other classification cannot create/submit linked Stock Entry, (c) mixed groups/wrong beneficiary fail, (d) unlinked direct CCPC Bearings Material Issue fails, (e) existing non-Bearings stock operations still work, (f) an approved classification cannot be changed after stockroom handoff, (g) double issue/concurrency/failed transaction rollback.
- Before **any** DEVELOPMENT pull: verify Git status/branch/commit, uncommitted work and backup/rollback, because DEVELOPMENT's shared bench hosts multiple sites. New DocType field requires a schema migration, **not authorized by code approval alone**.
- Finance Checker must independently sign the actual Expense revision **only after** the reviewed guard is deployed/tested and exception policy accepted. There is no automatic approval or historical backfill.
- **Risks requiring owner review:** the guard temporarily blocks legitimate native, unlinked CCPC Bearings Material Issues, including potential capital project workflow. Such work requires an independently designed and approved alternate route. This is intentional fail-closed behavior, not an implicit capitalization posting.

## Release status

- GitHub source: implemented for review.
- Python compilation and offline tests: pending PR CI verification.
- Actual ERPNext / MariaDB and DEVELOPMENT UAT: **not performed**.
- Expense Mapping status: still Draft, not active.
- PRODUCTION: **untouched**.

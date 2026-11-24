# Phase 1 maintenance-only integration candidate — 2026-11-24

**GitHub-only review artifact.** Base: `release/withdrawal-department-phase1-2026-11-17` at exact `b0cbe5c13cb68499a7920de2879f65e72cff000a`. Source: [draft PR #42](https://github.com/ccpcerp/ccpcerp/pull/42), `feature/phase1-maintenance-only-2026-11-21` at exact `3fa7c6c81b148dab870949b970e7a19f82ca6b2e`. All nine copied paths retain exact source Git blob SHAs; this integration candidate is not a merge of PR #42's entire branch history.

## Why a separate integration branch

The two branches **diverged** from `034afb116cf3fe0e3f9315b932eceba6dc961ce6`: current Phase 1 has seven additional commits (merged PR #41's read-only Frappe/MariaDB HTTP rollback and cancellation/reissue regression tests) missing from PR #42, while PR #42 adds its independent maintenance policy. A direct checkout of PR #42 would lose those newer release tests. GitHub compare confirmed the release-only changes touched its test and disposable guard paths, **not** any of the five existing production-code files copied here. This branch preserves the current Phase 1 release lineage and selectively applies the reviewed PR #42 runtime/metadata policy, its two pure/static tests, and original review document.

## Exact change surface

- **Fail-closed Bearings maintenance policy:** new `phase1_maintenance_use.py`; exact Company `Cagayan Corn Products Corporation`, exact official leaf Item Group `Bearings`, exact `Maintenance Department - CCPC` beneficiary, and explicit `Routine Maintenance` use only. Missing/capital/other/mixed Item Groups/wrong beneficiary deny. Other groups and Companies retain the existing Phase 1 policy.
- **Original-source enforcement:** `bc_issue_runtime.py` uses server-resolved locked original source Item Groups and beneficiary; `withdrawal_slip_stock.py` protects direct unlinked CCPC Bearings Material Issues, adds classification to parent row lock/stale snapshot comparisons.
- **Request intent and schema:** `withdrawal_slip.{py,js,json}` adds blank-by-default `ccpc_material_use`, requires server-side request-intent immutability and client read-only treatment after approval. **The extra DocType field needs a reviewed, separately approved site migration** on the shared live DEVELOPMENT bench; no migration occurred here.
- **Tests and documentation:** pure policy test `test_phase1_maintenance_use.py`, AST/static wiring test `test_phase1_maintenance_wiring_static.py`, and original `docs/phase1-maintenance-only-guard.md`.

## Explicit exclusions and tests

**Do not copy the PR #42-specific GitHub Actions YAML or special CI worker/real-DB test harness to this review candidate:** those scripts are explicitly pinned to **PR #42 identity/head branch, one-shot manual label, and disposable site**. Unreviewed reuse on a new PR could give misleading results or trigger unexpected hosted CI spend. Original source PR #42 remains unmodified with all of its original test harness and historical evidence. [GitHub Actions #44884746256](https://github.com/ccpcerp/ccpcerp/actions/runs/44884746256) passed **29 offline/static and 10 actual disposable MariaDB tests** at exact PR42 source code `33e2729d6ed010d25f0e38857e541e48c1c72faa`; PR42 tip is documentation-only after that test. These are *source-branch* results. **This newly assembled integration tree has not yet passed its own full native ERPNext/MariaDB or live MAIN DEVELOPMENT regression suite.** No new CI/provider paid job or cron has been triggered by constructing this branch.

## Release blockers / next gates

1. Review source/current-base diff, especially row locks in direct Stock Entry validation, replay/idempotency and cancellation; confirm no regression to PR #41 test cases or Phase 1 intercompany-billing-disabled behavior.
2. Verify exact official Company/Item Group/Department names **exist in the actual MAIN DEVELOPMENT masters**, not just approved taxonomy; confirm operational decision on blocking direct/unlinked Bearings Material Issues and an approved route for legitimate capital/other uses.
3. Have Accounting determine/sign the exact Company + Item Group→Expense Account and beneficiary Department→Cost Center mapping with independent named preparer/checker. The new field alone does **not** decide capitalization or authorize Draft mappings.
4. Before any persistent DEVELOPMENT pull or schema migration: verify live checkout SHA/status/uncommitted work, fresh two-site backups and independent restore (scoped offline rehearsal documented separately), audit the broader `after_migrate` side effects and define rollback; obtain scope-specific DEV authorization. No site schema, roles or financial records were changed.
5. Run an independently approved disposable **integrated-tip** Frappe/ERPNext/MariaDB regression (including PR #41 rollback and PR #42 10-case SQL suite), then carefully controlled MAIN DEVELOPMENT named-user UAT: original Withdrawal→submitted Stock Entry→SLE/GL→Department report, positive/denial/replay/cancellation.
6. Keep PR #33 intercompany billing **disabled/out of scope**. No protected branch merge or PRODUCTION deployment without separate review/approval and production access/security/backup/rollback readiness.

**Status:** SOURCE INTEGRATION CANDIDATE / REVIEW ONLY; runtime and schema **NOT DEPLOYED OR VERIFIED ON MAIN DEVELOPMENT**.

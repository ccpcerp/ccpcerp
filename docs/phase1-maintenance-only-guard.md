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

1. New structured classification is a part of Withdrawal Slip **request intent** and cannot normally be changed after approval. No automatic default to Routine Maintenance. The Withdrawal Slip browser form's read-only field list also includes the new selector, and the server remains authoritative against modified HTTP input.
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

## Verification checkpoint (2026-11-21)

- **Executed isolated pure policy unit tests:** Six tests passed locally against byte-for-byte copies of `phase1_maintenance_use.py` and `test_phase1_maintenance_use.py` whose Git blob SHA values were compared to the latest private GitHub branch; covers positive routine, missing/capital/other, beneficiary mismatch, mixed group, non-pilot unaffected and malformed group source. This proves only **pure policy logic**, not database/ERPNext integration.
- **Read-only source review:** 10 code/metadata/hook presence assertions passed using exact GitHub branch content. These are **textual inspection checks**, not executing the Python AST unittest module or testing true Stock Entry transactions.
- **GitHub Actions:** Latest PR CI run still `failure` before steps were assigned/executed; no GitHub-hosted tests passed. Runner provisioning root cause unverified. PR remains draft.
- **Required next:** Offline execution of full static wiring suite (using a complete reviewed checkout) and disposable Frappe/MariaDB integration under the safe site gate; then manual review and separately authorized DEVELOPMENT rollout/migration. Do not deploy this branch directly while Finance draft remains unsigned.

## Offline PR42 checkout verification (2026-11-21)

**Operator-reported terminal evidence (reviewed in chat, not independently observed on server):** A clean detached Git worktree at `~/ccpc-codex-work/ccpcerp-pr42-offline` was created from exact PR42 commit `43609f1e185d328cc4626d5acb6f413176a0ae4a`. The owner ran `python3 -m compileall -q ccpcerp` successfully and `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v` on the four modules `test_phase1_maintenance_use`, `test_phase1_maintenance_wiring_static`, `test_phase1_release_scope`, and `test_phase1_release_wiring_static`. Output displayed **Ran 24 tests in 0.079s — OK**, with every listed test passing. The shell returned through `git status --short` with no reported changes. This supersedes the earlier 'offline suite pending' checkpoint **for that tested commit**.

**Scope of evidence:** Python source syntax and offline pure/static regression tests only. No Frappe/MariaDB database tests, stock or GL postings, race tests, DEV application pull/migration, or Finance Expense mapping activation. GitHub Actions still has no verified successful hosted run. PR remains draft; obtain a reviewed disposable-site acceptance result before any DEV deployment decision. If PR code changes, rerun tests on the new head.

## 2026-11-21 — Prepared manual GitHub Actions disposable MariaDB acceptance

**Owner decision:** Prefer GitHub Actions over CircleCI for this PR42 acceptance, but no costly run, development pull/migration or production work has been authorized by implementation alone. The existing CircleCI job remains unchanged. The workflow is `.github/workflows/phase1-maintenance-mariadb-manual.yml`, and the real SQL suite is `ccpcerp/ccpc_erp/test_phase1_maintenance_real_integration.py`. A separate site-free safety test `test_phase1_maintenance_actions_static.py` inspects the opt-in workflow and guard.

**Manual activation, not automatic:** GitHub Actions `workflow_dispatch` requires a workflow in the default branch for the standard UI, so this unmerged draft uses `pull_request` `types: [labeled]` and **only** the exact `ccpc-run-pr42-db` label on app PR42. Job condition also checks PR number **42**, private same-repo head `feature/phase1-maintenance-only-2026-11-21` and base `release/withdrawal-department-phase1-2026-11-17`. No `push`, `synchronize`, `schedule`, repo secret, self-hosted runner or deployment event. Branch commit SHA is taken from PR head, checked out explicitly and verified before any bench install. Applying this label is the opt-in to consume GitHub-hosted minutes; **do not apply it until reviewing the branch, budget and runner availability**. Unlabeled ordinary PR updates never start this DB job.

**Disposable integration job:** GitHub-hosted `ubuntu-24.04` with bounded 65-minute maximum, MariaDB 11.8 and Redis 7 sidecars; new `ci-pr42-maintenance.localhost` site, Frappe/ERPNext v16 installed from upstream, CCPC application from exact same-repo PR head. The test is fail-closed outside exact GitHub PR42 identity and local disposable site. It exercises real MariaDB row locks, signed synthetic two-person Finance mapping revisions, a routine Withdrawal Slip → posted Material Issue → actual SLE/GL with balanced inventory outflow and expense attribution, duplicate prevention, negative capital/unclassified/blank, foreign beneficiary, mixed group, native direct-issue bypass, and immutability of an approved Slip classification. It **does not access persistent DEV or LIVE PROD**.

**Test fixture boundary:** The ERPNext base fixture uses synthetic `_Test Company`, `_Test Item Group` and synthetic cost/expense masters. The real-DB suite patches the three pilot policy constants and native direct-issue gate constants *in memory for test process only* to route those synthetic records through the identical live issue code. The separate six pure policy tests prove that the **unmodified production constants** are exactly the real CCPC/Bearings/Maintenance labels. No real CCPC accounting decisions or company data are populated in CI. These new DB tests and the new Actions workflow have **not yet executed**; they may need correction after the first authorized run. The 24 prior offline tests passing at commit `43609f1` remain genuine but do not substitute for database integration.

**Execution risk:** Earlier GitHub-hosted review attempts failed with no assigned runner and zero steps. The cause is still unverified; workflow execution may be unavailable because of private-repository Actions settings or hosted-runner availability. The owner should check repository Actions policy/billing/minutes before applying the label. If runner provisioning fails again, treat it as no test execution and use another owner-approved disposable runner; never silently fall back to persistent DEV or PROD. CI passing is not authorization for app merge, DEV migration or Expense mapping sign-off.


## 2026-11-21 — GitHub Actions PR42 disposable MariaDB acceptance passed

**Verified CI evidence:** GitHub-hosted run [44882706418](https://github.com/ccpcerp/ccpcerp/actions/runs/44882706418), at exact app commit `ff7129f3271b73a2922a6d214f698a77eb9d439a`, completed **successfully**, including test job `isolated-pr42-ledger`. Fetched runner logs show **28 offline Python policy/static/workflow-safety tests: OK**, and **6 real ERPNext v16/MariaDB 11.8 integration tests: OK** on a new synthetic `ci-pr42-maintenance.localhost` site. No skipped tests reported in the six-method integration suite. The suite independently exercised:
- signed synthetic Finance Expense and Center mapping followed by routine-maintenance original Material Issue, real Stock Ledger quantity/value, balanced General Ledger, Department and Cost Center, and duplicate rejection;
- capital, other/unclassified and blank classification denials without stock or GL change;
- wrong beneficiary, mixed Item Group and unlinked/native issue denials without financial postings;
- post-approval material-use immutability.

**What this does not prove:** Actual CCPC master-data Company/Department/item/category mapping, live Finance sign-off, full concurrent transaction/rollback/cancellation coverage, the behavior of all historical Withdrawals, UI-level acceptance, DEVELOPMENT application and database migration, or any PRODUCTION behavior. The test only temporarily translates exact pilot constants to synthetic fixtures in test memory. The original 24 policy checks and 4 new CI safety checks were executed. PR remains **Draft** pending code review, broader integration/UAT and separate Finance signature.

**Source repair identified by running CI:** Retry of preexisting cheap run [44881701981](https://github.com/ccpcerp/ccpcerp/actions/runs/44881701981) reached a real runner and found Python syntax error in a new test-only SQL field assertion. Fixed in app commit `ff7129f`. Automatically triggered cheap [run 44882657682](https://github.com/ccpcerp/ccpcerp/actions/runs/44882657682) passed, followed by the label-triggered real database green run. Existing CircleCI job unchanged.

**Repository visibility changed:** Repository `ccpcerp/ccpcerp` was verified **public** following the owner's decision. Treat source, all public branches/history, Actions logs and any existing forks as potentially permanently exposed; simply switching the repository to private later cannot revoke prior copies. No full secret/history scan was performed by this acceptance run. Audit sensitive material immediately, rotate any exposed credentials, and assess restoring private access as appropriate. Public visibility is not a release authorization.


## 2026-11-21 — Expanded PR42 real MariaDB concurrency, rollback, cancellation and roles PASSED

**Authoritative actual CI evidence:** [GitHub Actions run 44884746256](https://github.com/ccpcerp/ccpcerp/actions/runs/44884746256), exactly checking out app commit `33e2729d6ed010d25f0e38857e541e48c1c72faa`, completed **SUCCESS**. Read from GitHub runner logs: **29 offline Python/static/CI-safety tests, all OK**, and **10 real Frappe v16 + ERPNext v16 + MariaDB 11.8 integration test methods, all OK**. All ten actual methods were individually marked as passed in the console log, including a genuine two-independent-process MariaDB `FOR UPDATE` row-lock contention test. Concurrency test verifies exactly one successful submitted Stock Entry and one rejected second issuer, with exactly one stock-ledger issue and balanced GL, rather than simply calling twice serially. Test process-local mocks translate policy labels to synthetic ERPNext `_Test Company`, Item Group and Department but do **not** bypass the original DB locks, stock posting or GL APIs.

Additional newly verified behavior: (g) injected post-submit fault then SQL savepoint rollback removes all draft/ledger changes; (h) real ERPNext Stock Entry cancellation restores Bin quantity/value, creates reversing SLE/GL and restores Withdrawal Slip state; (i) creator, Guest, Administrator denied without stock or GL changes; (j) real concurrent issuer contention single-issue outcome. Original six maintenance-only positive/negative cases also passed.

**Failed prior attempt understood:** [Expanded run 44884053661](https://github.com/ccpcerp/ccpcerp/actions/runs/44884053661) showed the first nine real tests passing, but the final concurrency worker imported full ERPNext test bootstrap inside `multiprocessing.spawn` before site initialization (`AttributeError: new_doc_templates`). This was **test harness only**, not confirmed application/financial logic defect. CI worker moved into new lightweight and independently site/PR-guarded module `ccpcerp/ccpc_erp/ci_pr42_concurrent_issue_worker.py`. Full post-fix [run 44884746256](https://github.com/ccpcerp/ccpcerp/actions/runs/44884746256) proves all 10 tests pass. The opt-in run label `ccpc-run-pr42-db` has been removed following completion. No unattended recurring expensive run enabled.

**Approval boundaries unchanged:** Passing synthetic tests does not approve real CCPC Expense mapping, master-data configuration, DEVELOPMENT migration, or PRODUCTION deployment. PR42 stays Draft. Real company UAT and migration/rollback preflight remain separate. Public repository visibility remains the owner's explicit temporary decision; no passwords, real business records, or production credentials were committed in this test update. Full source/history security review remains deferred at owner direction.


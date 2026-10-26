# CircleCI Phase 2 — review checkpoint (2026-10-26)

## Source and scope

- Source: `ccpcerp/ccpcerp`, review branch `review/circleci-isolated-erpnext-phase2-2026-10-26`.
- Derived from `circleci-project-setup`, which was created from the reviewed intercompany invoice code at `16e6542016121aa5446516725186a45e8832b39c`.
- Configuration: `.circleci/config.yml`. Existing Python syntax job is retained.
- Second job provisions **disposable** Frappe v16, ERPNext v16, MariaDB 11.8 and two Redis endpoints within CircleCI. It runs four Withdrawal Slip modules and four intercompany-billing modules.
- No live CCPC database connections, credentials, deployment, Docker host, or production operations. All passwords in the file are fixed **test-only** values on throwaway containers; do not reuse them outside CI.

## Activation gate and billing protection

- `run_integration` is a pipeline boolean parameter with default `false`.
- Normal `run-ci` PR-label events run the lightweight `starter-validation` workflow only, subject to CircleCI's event/branch configuration.
- Full `opt-in-isolated-erpnext` is selected only when the operator explicitly runs a manual pipeline with `run_integration=true`; obtain separate approval before first such run.
- `resource_class: large` and bench setup may consume substantial free credits. CircleCI's credit rates, limits and paid-usage controls must be checked before approval. No CI test has been run on this branch.
- Do not add automatic push/schedule triggers or change the default parameter without owner approval.

## Verification and known risks

- GitHub commits and file content have been verified via the GitHub connector, but **CircleCI CLI schema validation and execution have NOT been performed**.
- Frappe v16/ERPNext v16 bench init, image compatibility, MariaDB startup and real test fixture compatibility may require revisions after a controlled isolated run.
- The job uses `sudo apt-get` for MariaDB C client headers and `npm install -g yarn`, unlike the cheap initial syntax-only job. This may increase runtime.
- Because the branch has changed relative to test-review branches, confirm the included tests and their isolation guards before deliberately enabling the full suite.
- A passing disposable CI run will **not** substitute for persistent DEVELOPMENT acceptance and never authorizes PRODUCTION deployment.

## Next step

1. Review GitHub changes and CircleCI free-credit budget / spending guard.
2. Validate `.circleci/config.yml` with the official CircleCI CLI or a safe CircleCI config validation facility.
3. If acceptable, request explicit approval for **one** manual pipeline using matching config/checkout source branch and parameter `run_integration=true`.
4. Inspect actual logs, document false failures or environment prerequisites, correct on review branch, and rerun only with approval.

## First CircleCI full run — 2026-10-26

- Operator confirmed starter-validation passed (7 seconds); opt-in-isolated-erpnext failed after around 1 minute 20 seconds.
- Uploaded job log showed Frappe source clone and dependency installation completed, but `bench init` failed in `bench.setup.backups()` with `FileNotFoundError: /usr/bin/crontab`.
- Cause: disposable CircleCI Python image lacked the `crontab` executable. This occurred **before** ERPNext and app installation and before any application test modules ran. It is not evidence of a failing business test.
- Fixed in review config by adding Ubuntu `cron` package to the existing ephemeral job prerequisites. This supplies `/usr/bin/crontab`; it does not modify any shared CCPC server.
- Fix **not yet run or validated in CircleCI**. Do not automatically rerun; request operator approval for another credit-consuming run after official CLI config validation.

## CircleCI build #7 engineering continuation (2026-10-26)

Reported results: 72/72 mocked Withdrawal Slip tests passed; real-ledger module had one success and 12 errors. Eleven errors were due to the original GitHub Actions-only isolation guard. The remaining error was missing synthetic approved B/C accounting mapping in the disposable test site. No intercompany modules ran in that build.

On this review branch, the CircleCI runner must now satisfy exact repository, review branch, job, commit, site and probe authorization checks before the costly Frappe installation. The existing GitHub Actions isolation restrictions are preserved. The spawned concurrency worker, WSGI probe, and intercompany SQL tests use the same site identity rules. Negative guard tests run without MariaDB in the inexpensive syntax job.

The new synthetic Finance fixture is **CircleCI disposable-site only**: two different test users, explicit Company scopes, valid leaf Account and Cost Center, and test-only approval evidence. Important: **the real Finance mapping approval controllers remain disabled.** The fixture uses a controlled test database update to simulate already approved revision rows for downstream ledger validation. It must not be treated as proof that genuine Accounting approval has been implemented or passed, and must never be used on DEVELOPMENT or PRODUCTION.

All changes remain unexecuted after build #7. Revalidate the latest config with CircleCI CLI. A new full pipeline requires explicit manual authorization and free-credit review; no automatic rerun or deployment.

## CircleCI build #9: silent preflight exit 1; GitHub App organization mismatch corrected (2026-10-26)

The uploaded `build_9_step_102_container_0.txt` contains only `Exited with code exit status 1` (no failed command or full job summary). Investigation identified a **probable** deterministic mismatch in the new fast-fail preflight: the GitHub App-integrated CircleCI organization is `CCPCerp_2026`, whereas the configuration and Python test guard erroneously compared `CIRCLE_PROJECT_USERNAME` to GitHub owner `ccpcerp`. CircleCI explains that on GitHub App projects this variable is the **CircleCI organization name**, not the GitHub account name; `CIRCLE_PROJECT_REPONAME` is the CircleCI project name. No app or Finance code error is evidenced by this particular 34-byte step log.

Corrected the **CircleCI organization name check only**, while preserving exact project, branch, job, pipeline/workflow IDs, checkout SHA, disposable site and opt-in probe restrictions. Updated the positive and negative no-database guard tests (including deliberate rejection of the old GitHub-owner value). Added a specific non-sensitive failure label to each CLI preflight check so the next log identifies which condition failed without printing credentials.

**Verification boundary:** committed to the same GitHub review branch; CircleCI's actual runtime environment variables were **not recorded** in the uploaded log, so the diagnosis is highly plausible but **not empirically confirmed**. The fix has not been revalidated via CircleCI CLI or executed in a new pipeline. No paid/automatic testing, app release, DEVELOPMENT or PRODUCTION actions.

Next: operator should re-download the latest review-branch config and run `circleci config validate` (no build credits). Check remaining Free credits and authorize at most one new manually triggered pipeline; if its early guard still fails, the new labelled error gives the precise failing variable to investigate without relaxing any guard.

## CircleCI build #11: 72 mocked passes; 12/13 real-ledger passes; one assertion aligned (2026-10-26)

Owner-uploaded `build_11_step_106_container_0.txt` documents:
- `test_withdrawal_slip`: 37/37 passed.
- `test_withdrawal_issue_safety`: 17/17 passed.
- `test_withdrawal_peer_issuance`: 18/18 passed.
- `test_withdrawal_real_ledger_integration`: 12/13 passed; one assertion failure, not an unexpected successful posting. The suite exercised real stock+GL, Department dimension, equipment beneficiary/conflict, prevention of duplicate issues, cross-session rollback/commit, actual two-issuer concurrency, WSGI HTTP/session/CSRF, and stock/GL cancellation.
- The failed check `test_x_forged_direct_voucher_department_rejected_before_ledger` tampered with a saved Stock Entry detail Department, and actual `doc.submit()` correctly threw `ValidationError: Row 1: original issue-time Equipment or financial mapping provenance cannot be modified.` Existing test had expected a string matching `[Dd]epartment`. Source code confirms `_validate_provenance_immutable` precedes `validate_issue_beneficiary` in the submit hook.
- Latest review-only test fix now checks the beneficiary helper separately raises a Department-specific validation error against the same forged detail, then checks the real `doc.submit()` raises the earlier immutable-provenance error, with no SLE or GL creation. **No runtime guard weakened or bypassed.** Fix commit `63fa1e52e74ed86c1144718dfd40060df25aed24`.
- The integration job exited with status 1 before its intercompany billing modules, which remain **unexecuted in build #11**. This does not prove full ERPNext integration or genuine Finance mapping approval workflow (synthetic CircleCI approval fixture remains test-only).

**Next:** After reviewing remaining Free-plan credits, owner can authorize one *new* pipeline using latest app review branch for config/checkout and `run_integration=true`. Prefer analyzing any fresh test errors, especially real intercompany draft invoice behavior. No automatic rerun, DEV or PROD changes.

## CircleCI build #13: 129 intercompany mocks passed; real-SQL fixture missing currencies (2026-10-26)

The owner's uploaded `build_13_step_107_container_0.txt` confirms `test_intercompany_material_billing_batch` **124/124 passed**, followed by `test_intercompany_billing_approval_failures` **5/5 passed**. The next real-MariaDB `test_intercompany_atomic_pair_database` attempted four tests but each errored *before inserting any SQL marker*: `Intercompany invoice currency mismatch: both Companies must use the same currency until an approved FX conversion and reconciliation policy is implemented.` The fixture's mocked `_get_company_invoice_defaults` returned `[{}, {}]`, yielding `None` for both required currencies. The runtime fail-closed FX safeguard operated correctly.

**GitHub review-only correction:** `test_intercompany_atomic_pair_database.py` now supplies `{"currency": "INR"}` for each mocked Company's invoice defaults (matching synthetic-currency test intent). No runtime currency matching restriction, rollback logic, or invoice approval logic was modified. Commit `9233f0a3d44042b5b9a0820eb89f6a1ceef7f4d7`. The separate `test_cross_currency_draft_pair_denied_before_any_invoice_creation` already passed in this same build. This fixture tests real SQL savepoints with synthetic ToDo rows; it does **not** test actual invoice inserts. The subsequent `test_intercompany_real_draft_invoice_database` was **not reached** in build #13.

**Verification:** source patch committed in GitHub but not yet executed. No CircleCI pipeline triggered by ChatGPT, no merge and no persistent DEVELOPMENT/PRODUCTION changes. Next: with owner's manual CircleCI run, verify four savepoint cases then genuine Sales/Purchase Invoice draft pair tests; inspect first failure only.

## CircleCI build #15: all four real SQL savepoint cases passed; genuine invoice tests blocked by USD/INR party account fixture (2026-10-26)

The uploaded `build_15_step_107_container_0.txt` is the intercompany test step on disposable site `ci-circleci.localhost`:
- `test_intercompany_material_billing_batch`: **124/124 passed**.
- `test_intercompany_billing_approval_failures`: **5/5 passed**, including cross-currency rejection.
- `test_intercompany_atomic_pair_database`: **4/4 passed** using actual MariaDB ToDo savepoint markers (invoice documents remain mocked in this suite). This verifies case-specific rollback/retention of those SQL markers, not genuine invoice insert validity.
- `test_intercompany_real_draft_invoice_database`: **0/2 passed**. Both errors occurred during real `SalesInvoice.insert`, before the intended paired-draft/rollback assertions. ERPNext `validate_party_account_currency` correctly rejected a USD receivable account (`_Test Receivable USD - _TC`) against an INR Sales Invoice. `_account` selected the first active Receivable by type/root/company, rather than verifying account currency. A passing genuine Sales/Purchase Invoice pair has NOT yet been demonstrated.

**Review-branch test-fixture fix:** `test_intercompany_real_draft_invoice_database.py` now filters synthetic Receivable and Payable accounts by the actual Company invoice currency (`Account.account_currency`) and positively checks the selected account currency. If a matching party account is unavailable the test fails clearly, rather than bypassing ERPNext native currency controls or assuming arbitrary currency conversion. **No runtime billing code, party-account validation, currency/FX policy or transaction rollback logic changed.** Fix commit `c2ff12d530a644a5afb54c10cacaec3fed5db7e6`.

**Not yet validated:** the changed fixture has not been executed on CircleCI. Future issues could arise from another ERPNext draft validation requirement; analyze actual first error and only use properly configured synthetic data. The fixture binds real invoice builders/validators to a mocked finalized batch; even if green, it will not prove real source membership, Finance workflow approval, invoice submission, returns, or deployment readiness.

**Next:** with owner approval, trigger a *new* manual CircleCI integration pipeline using the latest review branch and `run_integration=true`; do not rerun an old SHA. Capture full intercompany real-draft log or passing summary and update this handoff. No persistent DEVELOPMENT/PRODUCTION operations.

## Phase 2 isolated integration pipeline: first reported all-green result (2026-10-26)

**Owner-provided CircleCI screenshot** shows pipeline **#9** in project `CCPCerp_2026 / ccpcerp`, job/pipeline source `ccpcerp/ccpcerp` review branch `review/circleci-isolated-erpnext-phase2-2026-10-26`, abbreviated commit `d90773e` (independently confirmed review Git SHA `d90773e271e837500a7e105c6c2c883cd44a4b9a`). The UI shows green status for both workflows: `opt-in-isolated-erpnext` (**7m 56s**) and `starter-validation` (**7s**); pipeline green, around 2026-10-26 04:46–04:54 UTC. This is the first owner-reported **full successful Phase 2 isolated ERPNext pipeline** after the build #7–#15 fixture and guard fixes.

**Coverage implied by the verified config at exact tested commit:** the integration job uses `set -euo pipefail` and `bench --site ci-circleci.localhost run-tests --module ...` in a shell loop for:
- Withdrawal Slip modules `test_withdrawal_slip`, `test_withdrawal_issue_safety`, `test_withdrawal_peer_issuance` and `test_withdrawal_real_ledger_integration`.
- Intercompany modules `test_intercompany_material_billing_batch`, `test_intercompany_billing_approval_failures`, `test_intercompany_atomic_pair_database` and `test_intercompany_real_draft_invoice_database`.
- Separate lightweight Python compilation and disposable-site-identity tests in the starter job.

A green job implies those configured module commands completed successfully in this pipeline. **Evidence limitation:** only the CircleCI UI screenshot, not the full successful job step logs or each unittest output, was supplied for pipeline #9; do not invent precise test counts or detailed invoice record assertions from its UI alone. Previous individual build logs corroborate the historical test areas but not the final per-test results of this pipeline. The test site is disposable CircleCI MariaDB/Frappe/ERPNext, not CCPC DEVELOPMENT or PRODUCTION.

**Security and functional limits remain:** The controlled B/C Finance mapping approval user workflow is deliberately not activated: the CI fixture inserts synthetic approved revision state for ledger tests. The intercompany real-invoice suite creates actual **draft** ERPNext Sales/Purchase Invoices but uses a simulated finalized batch; it is **not** a real authenticated Finance approval, source-membership E2E, posted invoice/return cycle, or persistent DEVELOPMENT validation. Do not promote code to PRODUCTION based on CircleCI green alone.

**Recommended handoff:** preserve this passing `d90773e` code checkpoint; further changes require scoped review and retesting. Separately review permission controls, finance approval workflow, month-end intercompany process, migration/schema plans, and a safe DEVELOPMENT pull/test plan (check exact branch, SHA, git status and existing uncommitted changes first). PRODUCTION remains untouched pending explicit separate approval. No paid or scheduled CI automation.

## 2026-10-26 — Next milestone authorized: real Finance approval coverage added to CircleCI, DEVELOPMENT preflight still read-only

The owner approved continuation after the first screenshot-confirmed all-green Phase 2 CircleCI run of **application source** `d90773e271e837500a7e105c6c2c883cd44a4b9a` (pipeline #9, 7m 56s). Existing real Finance services `bc_mapping_workflow.py` and `bc_mapping_lifecycle.py` were **already on that same tested review branch**, inherited unchanged from draft PR #19. The controllers remain Draft-only against ordinary user saves by design; authenticated, authorized service actions perform the signed Finance approval transition. Past GitHub Actions Finance suite on PR #19 SHA `53ab180d...` passed **6/6** genuine disposable ERPNext/MariaDB tests, but the previously green **CircleCI configuration did not include that Finance-specific six-case suite**. Therefore the green CircleCI result was not independently a genuine maker/checker approval execution.

**New test-harness-only commits (NOT YET EXECUTED):**
- `test_withdrawal_real_ledger_integration.py`: its CircleCI synthetic `Approved` B/C fixture now skips the Finance-specific test class marked `_verify_genuine_finance_workflow=True`, preserving a missing-mapping negative case and authentic preparer/checker state.
- `test_bc_original_issue_real_integration.py`: strict opt-in `CCPC_ALLOW_BC_POSTING_PROBE=isolated-only`, unchanged originally authorized GitHub Actions repo/ref/site requirement **OR** the existing exact CircleCI organization/repo/review branch/job/run IDs/disposable-site verifier; no arbitrary CI flag or DEVELOPMENT/PRODUCTION site access.
- `.circleci/config.yml`: explicit Finance probe opt-in only inside disposable integration job; executes `test_bc_original_issue_real_integration` **after three lightweight Withdrawal modules but before the separate synthetic-approved real-ledger module**. The real six cases exercise missing mapping denial, genuine proposal/checker approval and posting, permission/overlap/sealed revision denials, cross-Company/backdate constraints, effective-dated replacement and retirement on SQL/GL.

**Scope boundaries:** No runtime Finance service, accounting permissions, DocType schema, SLE/GL logic, deployment or container changed; this is an incremental isolated CI test-coverage enhancement. Existing CircleCI green checkpoint remains valid for tested `d90773e`, but new review branch head is NOT YET VERIFIED green. Do not interpret test fixtures as signed CCPC business crosswalk G4, authentic live Finance identities or approval to activate real financial data. Additional gates: policy for exceptional posting dates / multi-center selector, true Company+leaf Item Group and beneficiary Department signed account/cost-center mapping, full end-to-end real sources and invoice returns, persistent DEVELOPMENT acceptance, backup/restore, separate PRODUCTION authorization.

**Next controlled checks:** operator validates newly fetched `.circleci/config.yml` via CircleCI CLI before a new **manually approved** run with `run_integration=true`; obtain step-level logs for the six genuine Finance tests (not screenshot alone). Before any persistent DEVELOPMENT action, owner sends **read-only** git branch/HEAD/status and named Docker site/containers; then review branch origin, existing uncommitted changes, database backup/rollback, and target site isolation. Do not pull, reset, stash, checkout, migrate, install roles, change Company permissions, restart or post Stock Entries until a separately reviewed safe plan is authorized.

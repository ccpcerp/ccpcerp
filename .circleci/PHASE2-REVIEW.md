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

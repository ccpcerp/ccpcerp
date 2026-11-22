# Phase 1 Finance Desk UI regression tests

These test the **actual** `ccpc_phase1_finance.js` on the checked-out Phase 1 branch. No ERPNext application code, accounting policy, roles, mappings or financial data are changed by this suite.

## Offline automated regression (run this first)

Run in an **isolated credential-free workspace**, with Node.js 20+, npm and Chromium available:

```sh
cd tests/phase1-finance-ui
npm ci
npx playwright install chromium
npm run check
npm test
```

The four offline browser tests run the actual Finance Page JavaScript against a simulated Frappe context and jQuery, with **no Frappe server, DB or credentials**. They check both CCPC and Ricor, Expense Account and Cost Center, the exact Item Group/Beneficiary Department labels, mapping-value labels, preparer/controller button separation, and clearing stale inputs on Company/Mapping type changes. The fixture rejects Finance API calls other than `get_pilot_context`; no proposals, approvals or coverage queries occur.

**Limits:** these tests check browser rendering only, *not* server-side authorization, actual user grants, master selection, coverage, mapping signatures, Stock Ledger or General Ledger. The exact CircleCI-generated `package-lock.json` has been checksum-verified and committed to this directory; use `npm ci` for a reproducible fresh installation.

## Opt-in disposable CircleCI runner (official Playwright Test)

The existing GitHub Actions one-shot trial [#44643024277](https://github.com/ccpcerp/ccpcerp/actions/runs/44643024277) was marked failed **before runner allocation**, with zero executed job steps, `runner_id=0` and 0 billable runner milliseconds. Do not claim the Node suite failed; provider failure cause was not exposed in available logs. Do **not** rerun that workflow automatically.

The [CircleCI](https://app.circleci.com/) runner added to `.circleci/config.yml` is **disabled by default**. It uses Microsoft's disposable Playwright v1.56.0 Noble image, `resource_class: small`, with a 420-second command limit, **no Frappe database**, no MAIN DEVELOPMENT credentials, no production access, and branch + source hash validation. There is a separate manual approval hold. Running a CI service may consume hosted CI credits; verify the organization's remaining free allowance and budget before approving the hold.

For any later, separately authorized one-time regression run using the existing CircleCI project connection (the original official 4/4 PASS does not need repeating):

1. In the `ccpcerp` CircleCI project, choose **Run Pipeline**, branch `release/withdrawal-department-phase1-2026-11-17` and latest reviewed commit; set only pipeline parameter `run_finance_ui = true` (leave `run_integration`, `run_finance_lifecycle`, `run_finance_bridge`, `run_four_reports_integrated` false).
2. Open the `opt-in-finance-ui` workflow and review the pending `finance-ui-manual-approval` hold; ensure branch and expected job `finance-ui-playwright-isolated` before clicking **Approve**. Do not rerun automatically on a failure.
3. Review the `npm run check` and `npm test` output for **4/4 passing tests**, source pin, exact installed package versions, and the stored `ccpc-finance-ui-package-lock.json` artifact. For future runs, compare the output to the reviewed lockfile and report any discrepancies; do not regenerate or replace the lockfile without review.

**2026-11-20 CircleCI results (user-provided job build 149 step logs): OFFICIAL 4/4 PASS.** The CircleCI identity/checked-out Git SHA guard passed at `324e7ae717b238392ee8acfc20e61db3a1862f07`. Node `v22.20.0`, npm `10.9.3`, `@playwright/test@1.56.0`, jQuery `3.7.1`, `npm run check` and the actual `npm test` runner all passed (4 tests, 1 worker, 3.0 seconds). The generated `package-lock.json` SHA-256 was `cbe5dc6e71b790d073ba096f18e54ca4fd80dcc56ae16ab6625bc6c352212696`; the job uploaded it to the `ccpc-finance-ui-package-lock.json` artifact (2.6 kB). **The owner subsequently supplied the exact artifact content**, and its reconstructed bytes matched the SHA-256 above. It was committed as `tests/phase1-finance-ui/package-lock.json` at **`ee8482b97444b7d626fc6eb2aeaf75fcefc97c44`** (Git blob `3308340b0791cfeabdf810c1690f9bae6fe330f7`), which GitHub refetch verified. The job log does not establish live named-user/Finance backend or ledger acceptance. At the time of the original CircleCI test, PR40 was still draft/unmerged. No repeat CircleCI run is needed for these four offline specs.

## Optional authenticated MAIN DEVELOPMENT browser check

`npm test` excludes the separate `@live` tests. Live tests require **explicit opt-in** and accept only `http://development.localhost:8000`, not any production host. They verify the same Company × Mapping type UI matrix with existing named Finance Preparer/Controller sessions.

Use a **trusted workstation** with the intended DEVELOPMENT site accessible. Prepare two independent storage-state JSON files locally, signing in interactively as each existing authorized user. For example:

```sh
mkdir -p .auth
npx playwright codegen --save-storage=.auth/preparer.json http://development.localhost:8000/login
# Sign in as the existing authorized preparer, then close codegen.
npx playwright codegen --save-storage=.auth/checker.json http://development.localhost:8000/login
# Sign in as the independent authorized controller, then close codegen.
```

Do not commit passwords, cookies, tokens, storage states, screenshots, traces or any business-data artifacts. `.auth/` and `*.storage-state.json` are ignored. Verify the named users' approved effective roles/Company permissions; account creation or grants are outside this task.

```sh
CCPC_FINANCE_UI_LIVE=1 \
CCPC_FINANCE_UI_DEV_URL=http://development.localhost:8000 \
CCPC_FINANCE_UI_PREPARER_STATE=.auth/preparer.json \
CCPC_FINANCE_UI_CHECKER_STATE=.auth/checker.json \
npm run test:live
```

The live browser performs navigation and selection changes only, disallows cross-origin requests, and blocks Finance onboarding API endpoints other than `get_pilot_context`. It never clicks `Check coverage`, `Propose mapping`, `Review draft` or `Sign and approve`, and never types a financial master. Session/bootstrap activity can still generate normal login/audit traffic; it is **not** a database read-only transaction. Check current MAIN DEVELOPMENT readiness and protected backups before a live run. Do **not** run against PRODUCTION or launch this from the historical SSH-mounted shared DEV container merely to bypass access restrictions.

## Status and release safety

- Historical named-user browser checks of the four Company × Mapping type combinations were manually recorded PASS on November 19; **they were not automated**.
- **Official CircleCI Playwright Test: 4/4 PASS** on exact reviewed commit `324e7ae`, with pinned `@playwright/test@1.56.0` and jQuery `3.7.1`; Node syntax checks passed. The exact output lockfile is now checked into GitHub. The new README/lockfile-only commits do not alter the previously tested Finance Page JS or browser spec.
- Live authenticated tests: not performed; require careful separately scoped DEV execution.
- A one-time GitHub Actions attempt failed before any runner started (0 billable milliseconds), and the spent workflow file was removed from this PR before merge. The CircleCI manual-only, approval-held job **subsequently passed its original four browser tests**. No recurring CI schedule or unattended rerun was configured. These are synthetic offline UI checks only; no role grant, database write, MAIN DEVELOPMENT checkout/migration/restart or PRODUCTION operation was part of the test job.
- Playwright disables screenshots, traces and video to avoid preserving authenticated content.

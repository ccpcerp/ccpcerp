# Phase 1 Finance Desk UI regression tests

These test the **actual** `ccpc_phase1_finance.js` on the checked-out Phase 1 branch. No ERPNext application code, accounting policy, roles, mappings or financial data are changed by this suite.

## Offline automated regression (run this first)

Run in an **isolated credential-free workspace**, with Node.js 20+, npm and Chromium available:

```sh
cd tests/phase1-finance-ui
npm install
npx playwright install chromium
npm run check
npm test
```

The four offline browser tests run the actual Finance Page JavaScript against a simulated Frappe context and jQuery, with **no Frappe server, DB or credentials**. They check both CCPC and Ricor, Expense Account and Cost Center, the exact Item Group/Beneficiary Department labels, mapping-value labels, preparer/controller button separation, and clearing stale inputs on Company/Mapping type changes. The fixture rejects Finance API calls other than `get_pilot_context`; no proposals, approvals or coverage queries occur.

**Limits:** these tests check browser rendering only, *not* server-side authorization, actual user grants, master selection, coverage, mapping signatures, Stock Ledger or General Ledger. There is not yet a committed npm lockfile; after a successful isolated install, review and commit its lockfile to make dependency resolution reproducible.

## Opt-in disposable CircleCI runner (official Playwright Test)

The existing GitHub Actions one-shot trial [#44643024277](https://github.com/ccpcerp/ccpcerp/actions/runs/44643024277) was marked failed **before runner allocation**, with zero executed job steps, `runner_id=0` and 0 billable runner milliseconds. Do not claim the Node suite failed; provider failure cause was not exposed in available logs. Do **not** rerun that workflow automatically.

The [CircleCI](https://app.circleci.com/) fallback added to `.circleci/config.yml` is **disabled by default**. It uses Microsoft's disposable Playwright v1.56.0 Noble image, `resource_class: small`, with a 420-second command limit, **no Frappe database**, no MAIN DEVELOPMENT credentials, no production access, and branch + source hash validation. There is a separate manual approval hold. Running a CI service may consume hosted CI credits; verify the organization's remaining free allowance and budget before approving the hold.

To run once using the existing CircleCI project connection:

1. In the `ccpcerp` CircleCI project, choose **Run Pipeline**, branch `test/phase1-finance-playwright-ui-2026-11-20` and latest reviewed commit; set only pipeline parameter `run_finance_ui = true` (leave `run_integration`, `run_finance_lifecycle`, `run_finance_bridge`, `run_four_reports_integrated` false).
2. Open the `opt-in-finance-ui` workflow and review the pending `finance-ui-manual-approval` hold; ensure branch and expected job `finance-ui-playwright-isolated` before clicking **Approve**. Do not rerun automatically on a failure.
3. Review the `npm run check` and `npm test` output for **4/4 passing tests**, source pin, exact installed package versions, and the stored `ccpc-finance-ui-package-lock.json` artifact. The operator can share the CircleCI link/output for verification and to commit the reviewed npm lockfile to PR40.

**At the current checkpoint this CircleCI job is configured but has NOT been executed.** PR40 is still draft and unmerged; the earlier local isolated Chromium check executed the four original test functions through a temporary Node adapter and returned **4/4 PASS**, which does not substitute for the official Playwright Test runner or named-user acceptance.

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
- Original four JavaScript test functions: **4/4 PASS** in isolated Chromium using a temporary Node adapter with local Playwright Core and jQuery; the exact pinned `@playwright/test` `npm test` run is still pending a hosted CI run with npm access.
- Live authenticated tests: not performed; require careful separately scoped DEV execution.
- A one-time, path-filtered GitHub Actions push test attempted but failed before any runner started (0 billable milliseconds). The CircleCI workflow is manual-only, parameter default false, approval-held and branch-restricted; no recurring CI scheduling or unattended rerun is authorized. No role grant, database write, MAIN DEVELOPMENT checkout/migration/restart or PRODUCTION operation occurred.
- Playwright disables screenshots, traces and video to avoid preserving authenticated content.

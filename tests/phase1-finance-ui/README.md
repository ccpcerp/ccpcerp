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
- Offline suite: add and syntax-check in GitHub; browser run remains pending until Playwright/Chromium dependencies are installed in a safe disposable workspace.
- Live authenticated tests: not performed; require careful separately scoped DEV execution.
- No new auto-triggered CI, paid runner, role grant, database write, MAIN DEVELOPMENT checkout/migration/restart or PRODUCTION operation is authorized by this test-only change.
- Playwright disables screenshots, traces and video to avoid preserving authenticated content.

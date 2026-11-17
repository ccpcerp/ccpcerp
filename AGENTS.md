# CCPC ERPNext — Codex development instructions (Phase 1 release candidate)

This file applies to work in this repository. **Read GitHub before editing.** The current first-release target is **tested CCPC Withdrawal + real stock/Expense GL + Department material consumption**, same issuing/requesting legal Company; native ERPNext handles unrelated ordinary workflows. Production deployment is a separate owner decision.

## Working source and objective
- First-release GitHub branch: `release/withdrawal-department-phase1-2026-11-17`, draft PR #35: https://github.com/ccpcerp/ccpcerp/pull/35.
- Finance feature baseline is commit `a2f0e89f12afbc28ac10dab71b6af47a455fdc95`. Do not assume default `version-16` contains current functionality. Verify current branch HEAD and PR diff.
- Project workflow and current handoff live in the **separate private** `ccpcerp/ccpc-frappe-deployment` repository, under `docs/ccpc-project/`. If inaccessible, continue from this file and explicitly report documentation access as unverified rather than inferring outdated milestones.
- Immediate executable task: `CODEX-PHASE1-DEVELOPMENT-TASK.md`. Finish functional code and realistic tests; do not spend a session producing only another broad source audit.

## Allowed autonomy in an isolated, disposable environment
- Inspect, edit, refactor, run code and tests, build a **synthetic** Frappe/ERPNext v16/MariaDB test site and repair test failures. Use a sandbox you control that contains **no CCPC business data or shared credential/backup mounts**.
- Use synthetic Company, Stockroom, Item, GL, Department and User fixtures. You may create/recreate and delete **only test resources created in that dedicated disposable environment**. Do not assume `development.localhost` or `ccpc-integration2.localhost` is disposable.
- Validate the actual positive same-Company Withdrawal -> approved issue -> submitted Stock Entry -> SLE / Expense GL / original Department report and rejection/rollback paths; the earlier CircleCI acceptance is prior evidence, not this candidate's proof.
- Run cheap preflight first: `python -m compileall -q ccpcerp` and `python -m unittest -v ccpcerp.ccpc_erp.test_phase1_release_scope ccpcerp.ccpc_erp.test_phase1_release_wiring_static`. These are offline, not ledger acceptance.
- Commit narrow verified code changes to an **unprotected task/review branch**, and propose a PR against the Phase 1 release branch. Record command, passed/failed/untested count, source SHA and any genuine ledger voucher test evidence. The owner wants a reviewable finished improvement, not endless preflight reports.

## Explicit host/credential boundaries — not merely instructions in place of isolation
- Never use unrestricted Codex CLI in the **existing** DEVELOPMENT container `frappe_docker_devcontainer-frappe-1` until its credential and host mounts have been independently removed or an isolated replacement is provided. A November 16 checkpoint found the container had `/home/frappe/.ssh` mounted and source code under a broad `/workspace` bind. No Docker socket/privileged mode was reported; that alone does **not** make it safe.
- The legacy PRODUCTION instance was historically on `ccpcerpserver` alongside DEV. A new physical `ccpcerp-prod` server is separately documented, but its status as the *actual live* production system was not independently verified when this policy was written. Do not assume production is physically isolated until the current site has been verified.
- Do not open/read/copy SSH keys, tokens, `.env`, Frappe `site_config.json`, production databases, protected private backups, NAS mounts or unrelated personal files. Never print credentials to a transcript, tool output, CI logs or GitHub. Do not work around failures by adding these accesses.
- No Docker host socket or privileged host operations, no network route to production/backup systems, no shared MAIN DEVELOPMENT site/config/DB/Redis state in a Codex writable test sandbox. Actual enforcement requires environment setup; this document alone cannot create a security boundary.
- Do not edit, migrate, post, delete or restart existing MAIN DEVELOPMENT, legacy PROD or new PROD, or alter protected GitHub branches, financial approval records or actual user permissions. Even owner-approved Codex coding autonomy does **not** authorize changing business data or PROD. No scheduled agent reporting, paid API arrangements, auto-merge or auto-deploy.

## Engineering quality and handoff
- Keep Company scoping, permission checks, transaction locks, duplicate issue prevention, Expense/Cost Center mapping provenance, cancellations and reporting separation intact.
- This release deliberately **blocks** custom intercompany billing actions; do not reactivate billing PR #33/#34, modify historical invoice records, or change the agreed same-Company boundary unless the owner explicitly changes scope.
- If the sandbox lacks Docker/bench/DB, do useful implementation, source tests and a **precise runnable disposable integration test** instead of claiming real SQL PASS. Do not try to access MAIN DEV to compensate.
- At end: state what code changed, branch/commit/PR, which genuine tests ran, what remains, and the one next action. Keep credentials, customer/business data and sensitive logs out of reports.

Owner-approved high-level delegation: **Codex can perform broad code/debug/test work inside a properly isolated disposable DEVELOPMENT workspace**. Creating or granting that isolation, and any actual persistent DEVELOPMENT/PRODUCTION operation, require their own verified operating boundary.

# Codex task — finish Phase 1 Withdrawal / Department cost release candidate

**Owner authorization, 2026-11-17:** perform substantial autonomous **coding and testing** in Codex's isolated disposable workspace, not in shared DEV or any production environment. No extra paid OpenAI API, scheduled automation or public repo. Use the connected **private** `ccpcerp/ccpcerp` GitHub source, starting from release branch `release/withdrawal-department-phase1-2026-11-17`, [draft PR #35](https://github.com/ccpcerp/ccpcerp/pull/35). Read `AGENTS.md`.

## Deliverable to complete in one task, not another generic readiness audit

Bring the existing tested CCPC feature to a **reviewable, exact-source Phase 1 release candidate**. Make focused code and tests where gaps appear. Retain the already implemented custom Withdrawal Slip and its existing Finance mapping/Stockroom issue engine; routine purchase/stock/accounting not on this path stays native ERPNext. Only same-Company issues in R0, no new custom CCPC↔Ricor invoices.

1. Verify release branch and identify the code actually used by stock issue entry points:
   - `ccpcerp/ccpc_erp/doctype/withdrawal_slip/withdrawal_slip.py`
   - `ccpcerp/ccpc_erp/withdrawal_slip_stock.py`
   - `ccpcerp/ccpc_erp/phase1_release_scope.py`
   - `ccpcerp/ccpc_erp/withdrawal_issue_beneficiary.py`
   - `ccpcerp/ccpc_erp/bc_issue_runtime.py`
   - `ccpcerp/ccpc_erp/report/ccpc_department_material_consumption/ccpc_department_material_consumption.py`
   - installed hooks, setup and relevant native Stock Entry permissions.
2. First run offline `python -m compileall -q ccpcerp` and `python -m unittest -v ccpcerp.ccpc_erp.test_phase1_release_scope ccpcerp.ccpc_erp.test_phase1_release_wiring_static`. Report their actual results, not historical pass counts. Current GitHub Actions run [#44086363890](https://github.com/ccpcerp/ccpcerp/actions/runs/44086363890) was **0-step infrastructure failure**, not test evidence; do not wait for that runner to resume coding.
3. Execute the existing three Frappe route tests `ccpcerp.ccpc_erp.test_phase1_release_routes` where a working installed Frappe environment exists. If not available, make the tests import/install-compatible, then record precisely **NOT RUN**.
4. Implement/repair a **disposable real Frappe/ERPNext v16 + MariaDB** acceptance for:
   - One synthetic same-Company Withdrawal initiated/approved by distinct requestor/approver; independent authenticated Stockroom issuer; exactly one native submitted Material Issue; real matching Stock Ledger Entry, Expense GL, Company and Cost Center; original Department cost report amount and stock UOM tie-out at the documented report grain.
   - Deny another legal Company at both Stockroom and direct linked Stock Entry paths without new SLE/GL; deny unapproved mapping/unauthorized role; duplicate/replay and error rollback must leave no extra issue, stock or GL. Confirm locked original `requesting_company` check.
   - No custom Intercompany Material Billing Batch create/update/delete/preview/finalize/approve/invoice action. Do not touch existing historical CCPC/Ricor draft invoices or change native SI/PI policies; report other endpoints that evade this controller.
   - Report distinct source/posted accounting totals without misrepresenting a gross SLE consumption report as net Department P&L or counting Expense twice.
5. Review only integration-critical `hooks.py`, `setup.py`, patches/schema/roles and syntax/compatibility. If necessary, write minimal hardening changes and targeted tests in an **unprotected task branch** cut from the current release HEAD. Avoid redesign, new broad accounting modules, or mixing unmerged PR33/34 intercompany code.
6. Present a concise outcome: branch + commit SHA + PR against `release/withdrawal-department-phase1-2026-11-17`, tests executed with PASS/FAIL/NOT RUN and actual runner/site origin, observed ledger/report correctness, risks, and one next manual MAIN DEV acceptance check. Do not merge or deploy.

## Isolation and completion rules

- Work on an isolated *synthetic* site only; app code and disposable fixtures may be rewritten freely there. Never read or mutate `development.localhost`, `ccpc-integration2.localhost`, or historical broken `ccpc-integration.localhost` on the owner server. Never access legacy/new PRODUCTION, actual business data, NAS, credentials, host SSH or Docker socket. Those boundaries are not negotiable merely because the user wants fast progress.
- Prefer running genuine existing disposable tests over inventing repetitive static-only reports. If provisioning full MariaDB/Frappe is unavailable in Codex Cloud, carry through substantial code/test fixes, record that genuine DB remains pending and make the disposable test runnable through an existing independently approved runner. Do not claim actual installation or tests you did not perform.
- Private repository stays private. Codex cloud work may run while the owner's phone/computer sleeps; **this GitHub task file itself does not launch any agent or grant shell access**.
- No third-party paid OpenAI API key, CI budget increase, scheduled reporter, production migration or user-role grant.

**Task kickoff message to paste in Codex:**
> Use the current release branch of `ccpcerp/ccpcerp`; follow `AGENTS.md` and `CODEX-PHASE1-DEVELOPMENT-TASK.md`. Build and fix the Phase 1 same-Company Withdrawal→native Material Issue→real SLE/Expense GL→Department cost acceptance. Work autonomously in your isolated workspace, run available tests, commit narrow corrections on a separate task branch, and open a review PR. Do not connect to persistent DEVELOPMENT or PRODUCTION. Report exact tests and blockers rather than stopping after a source-only audit.

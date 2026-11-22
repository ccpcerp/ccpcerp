# CCPC Phase 1 / PR #33 — safe source reconciliation for MAIN DEVELOPMENT

**Prepared:** 2026-11-18 (Asia/Manila). **Scope:** GitHub-only review branch from the owner-approved, merged Phase 1 release. No live DEVELOPMENT or PRODUCTION operations.

## Exact code provenance

| Line | Ref | GitHub commit |
| --- | --- | --- |
| Phase 1 source of truth (including merged Finance onboarding PR #38) | `release/withdrawal-department-phase1-2026-11-17` | `2b6e4e73cfbbd316caaa06fa9987255d05a631de` |
| Earlier PR #33 billing/correction work to preserve **unchanged** | `review/billing-authorization-correction-2026-11-03` | `25a7b6be1801a54f7ed39fef54f4ea4e3a1cea87` |
| This reconciliation proposal | `review/phase1-pr33-safe-reconciliation-2026-11-18` | Branch starts exactly at the Phase 1 SHA above; subsequent isolated review/test commits only |

The branches have diverged. The GitHub commit comparison on 2026-11-18 found **15 release-only** commits and **44 PR33-only** commits. This is **not** a fast-forward. Preserve PR33's branch and all historical billing/correction records for later independently authorized work. Do **not** merge or cherry-pick the whole PR33 branch into Phase 1.

## Material code conflicts and decisions

1. **Billing must stay disabled in Phase 1.** PR33's `ccpcerp/ccpc_erp/doctype/intercompany_material_billing_batch/intercompany_material_billing_batch.py` contains active `finalize_month` and `approve_and_create_invoices` paths. The approved Phase 1 source instead calls `_assert_billing_not_released()` before mutation, backed by `phase1_release_scope.require_billing_disabled()`. An indiscriminate merge could restore invoice creation and invalidate the limited same-Company release.
2. **Same-Company stock issuance must stay enforced.** Phase 1 adds `phase1_release_scope.require_same_company_issue` in both `withdrawal_slip_stock._get_issuable_slip` (Stockroom path) and `validate_withdrawal_slip_stock_entry` (direct linked Stock Entry path). The PR33 snapshot predates this gate. Historical cross-Company records must not be rewritten or deleted to enforce the new posting rule.
3. **Finance role bootstrapping differs.** Phase 1 `hooks.py` runs `ensure_ccpc_group_executive_reporting_role` and `ensure_bc_finance_mapping_roles` before install/migrate. PR33 uses `ensure_ccpc_bootstrap_roles` and has a separate Billing Preparer role. Avoid silently changing live access, seeding new named User or Company grants, or activating PR33 billing permissions in Phase 1.
4. **The live checkout has a protected untracked `AGENTS.md`.** The release branch tracks its own `AGENTS.md`. Direct checkout/pull can fail or risk overwriting the protected local file; do not `git clean`, force reset, stash without inventory, delete, rename, or overwrite it as a shortcut. Treat it as owner-controlled and review its disposition only under a separate DEV source-integration authorization.
5. **Migration is not Page-only.** A Phase 1 migration will sync a new Desk Page and its role definitions, and existing `ccpcerp.setup.after_migrate` performs broader backfills/permissions/workflow updates. It also runs on a shared bench with a broken `ccpc-integration.localhost` site. Do not run `bench migrate --site all`, `bench schedule`, or broad bench restart merely to make the new Page appear.

## What this review PR changes

The only application-file change is to the **offline, AST-based static wiring tests** `ccpcerp/ccpc_erp/test_phase1_release_wiring_static.py`. They now fail when:

- A newly introduced `@frappe.whitelist()` method on the legacy billing controller does **not** begin with the server-side billing-disabled assertion. Existing tests already cover six known mutation hooks; this adds an enumeration guard against future PR33 endpoint imports.
- A PR33 merge replaces the Phase 1 `before_install`/`before_migrate` Finance mapping and executive role setup with the old billing bootstrap, or removes its existing `after_migrate` connection.

These are **source integrity checks**, not actual permission proofs, database recovery tests, or sufficient grounds to merge/deploy. No PR33 implementation code is imported; Phase 1 business behavior remains unchanged. The reconciliation is a staged *boundary-protection proposal*, not a completed deployment or a full merging of 44+15 commits.

## Known MAIN DEVELOPMENT evidence (operator-verified)

On `ccpcerpserver`, container `frappe_docker_devcontainer-frappe-1` is running, Compose project `frappe_docker_devcontainer`. Existing shared app path is `/workspace/development/frappe-bench/apps/ccpcerp`, **detached** at exactly PR33 SHA `25a7b6be1801a54f7ed39fef54f4ea4e3a1cea87`, `git status --porcelain=v1 --untracked-files=all` only `?? AGENTS.md`. Main site `development.localhost`; synthetic `ccpc-integration2.localhost`; known broken `ccpc-integration.localhost` is not to be connected or migrated.

The existing **2026-11-17** MAIN DEV four-component snapshot `development.localhost-20261117T043152Z-1582f342f870` was independently re-hashed by the operator in both locations with **4/4 SHA256 PASS local**, **4/4 PASS Synology NAS**, **local/NAS MATCH**, exit 0. Off-host copy: `/mnt/erpnext_backups/ccpc-development-pr33-main-precommit-20261117T044035Z-591a9f7e2b035cae/` on verified CIFS mount `//192.168.50.132/erpnext_backups`. These checks establish backup file integrity/copy agreement **at that Nov 17 recovery point**, not a current post-Nov 17 backup, full database consistency or an actual restore.

## Controlled future acceptance sequence — NOT authorized by this PR

1. **Review boundary tests and exact diff in GitHub.** Run offline release tests on this PR's exact commit in a trusted isolated source-only checkout; then run a real disposable Frappe v16/MariaDB test for Finance onboarding and same-Company Withdrawal/SLE/Expense GL/report, including cross-Company/duplicate/replay and billing-disabled controls. Keep test results linked to the exact commit, and do not infer them from earlier PR #38 test logs.
2. **Preserve PR33 separately.** Plan DEV source integration using a distinct, owner-approved staging checkout/worktree and exact commit pins; keep the existing detached checkout and owner-controlled untracked file intact. Review the branch divergence (including `hooks.py`, billing controller and `setup.py`) before selecting the release code, without importing PR33's active intercompany billing.
3. **Backup/recovery gate immediately before actual DEV write:** independently authorize a *new* four-component `development.localhost` backup, integrity-verified off-host copy, and an isolated production-disconnected restore or an explicitly reviewed recovery/rollback test. The Nov 17 set is retained; it does not certify present-day DEV state. Do not reuse old one-shot scripts without reviewing their pinned checks or one-time markers.
4. **Separate owner approval for MAIN DEV writes**: confirm current Git branch/HEAD/status, hash and protect `AGENTS.md` without publishing it, verify actual site/active connections, review *every* `after_migrate` side effect, and explicitly target only `development.localhost`. Do not pull/migrate/restart automatically.
5. **Finance-owned real pilot and UAT:** obtain actual CCPC leaf Item Group, beneficiary Department, eligible Expense Account and Cost Center, plus independent enabled named preparer/checker with explicit Company User Permissions. Confirm Page access/denial and test real site workflow only with owner-authorized safe fixtures, verify native Stock Ledger/Expense GL and Department original report; preserve PR33 historical billing records and cancel/rollback safeguards.

**HOLD:** Any PR merge, persistent DEV checkout/pull, migration, site write, Docker operation, actual production host/data access or local Codex agent startup remains outside this GitHub-only proposal. The new physical PRODUCTION server `ccpcerp-prod` and retained legacy PRODUCTION data on `ccpcerpserver` must remain untouched.

# Phase 1 integrated maintenance MariaDB acceptance review

**Status:** GitHub-only review prep. Not executed against MAIN DEVELOPMENT, PRODUCTION, or a hosted runner.

Base app integration candidate: `e51c57efd8ea17b7193dfb37caab38f315f22e83` (review/phase1-maintenance-release-integration-2026-11-24). This branch adds only a reviewed, renamed and re-scoped copy of the 10-case synthetic PR42 MariaDB test suite and its spawned row-lock worker, plus a manual-label GitHub-hosted workflow and static guard. The PR42 source suite was previously exercised on a separate branch; **these adapted files have not run on an actual MariaDB/Frappe site**.

## Manual spend gate

Workflow `.github/workflows/phase1-maintenance-integrated-manual.yml` subscribes ONLY to `pull_request: [labeled]` where base is `review/phase1-maintenance-release-integration-2026-11-24`; the expensive job rejects any event other than a same-repo PR from `review/phase1-maintenance-integrated-tests-2026-11-24` with label `ccpc-run-phase1-integrated-db`, a first attempt, exact reviewed checkout, and disposable site identity. **Do not add that label** until the owner approves a single bounded hosted job after verifying free minutes/credits, expected cost, cancellation plan, secrets isolation and exact PR head SHA. No push, schedule, workflow_dispatch or self-hosted runner; no CCPC finance or infrastructure credentials.

Site: `ci-phase1-integrated.localhost`, MariaDB 11.8, Redis 7, Frappe/ERPNext v16, synthetic ERPNext _Test Company. Checkout keeps credentials out of Git. The job uses an immutable PR head SHA, not the current release branch alone; generates only runner-local disposable secrets. 65-minute maximum job, **not a guaranteed monetary cap**. Manual single-run approval required.

The native test suite covers signed synthetic Finance mapping, Routine Maintenance debit/credit GL + SLE, capital and Other/Unclassified denial, wrong department, mixed group, native direct Material Issue bypass, immutable use, transaction rollback, cancellation/reversal, unauthorized issuer and concurrent independent connections. Pure/static suite tests fail closed. The current PR41 rollback and reissue test blobs are checked by exact Git hashes in CI but **not rerun by this workflow**; they require separate integrated-tip execution under their own accepted disposable guard. Do not claim full PR41 regressions based only on hash preservation.

Finance business approval is still missing for MAIN DEVELOPMENT (Bearings Expense Draft=1, Approved=0). This review PR does not create/approve accounts, alter mapping policy, migrate the pilot field, issue DEV stock, or authorize PRODUCTION.

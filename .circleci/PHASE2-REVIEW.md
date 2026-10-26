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

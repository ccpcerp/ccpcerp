#!/usr/bin/env bash
# No-secret, CI-only smoke check for pinned Frappe / ERPNext install compatibility.
# Never source or execute on DEVELOPMENT, PRODUCTION, or a database host.
set -euo pipefail
umask 077

fail() {
  printf 'PINNED BENCH VERSION SMOKE: %s\n' "$1" >&2
  exit 1
}

# Fail closed before any package download or bench creation.
[[ "${CIRCLECI:-}" == "true" ]] || fail "CI_IDENTITY_FAIL"
[[ "${CIRCLE_PROJECT_USERNAME:-}" == "CCPCerp_2026" ]] || fail "CI_IDENTITY_FAIL"
[[ "${CIRCLE_PROJECT_REPONAME:-}" == "ccpcerp" ]] || fail "CI_IDENTITY_FAIL"
[[ "${CIRCLE_BRANCH:-}" == "review/pr32-integration2-isolated-restore-2026-10-30" ]] || fail "CI_IDENTITY_FAIL"
[[ "${CIRCLE_JOB:-}" == "pr32-bench-version-smoke" ]] || fail "CI_IDENTITY_FAIL"
[[ -n "${CIRCLE_PIPELINE_ID:-}" && -n "${CIRCLE_WORKFLOW_ID:-}" && -n "${CIRCLE_BUILD_NUM:-}" ]] || fail "CI_IDENTITY_FAIL"
[[ "${HOME:-}" == "/home/circleci" ]] || fail "CI_HOME_FAIL"
[[ "$(git rev-parse HEAD 2>/dev/null)" == "${CIRCLE_SHA1:-}" ]] || fail "CI_CHECKOUT_FAIL"
[[ "$(git rev-parse --show-toplevel 2>/dev/null)" == "$PWD" ]] || fail "CI_CHECKOUT_FAIL"

# Never accept confidential Context or project-scope backup credentials here.
for name in CCPC_RESTORE_AGE_IDENTITY CCPC_RESTORE_B2_KEY_ID CCPC_RESTORE_B2_APP_KEY; do
  [[ -z "${!name:-}" ]] || fail "CONFIDENTIAL_ENVIRONMENT_FORBIDDEN"
done
[[ -z "${CCPC_ALLOW_INTEGRATION2_RESTORE:-}" ]] || fail "RESTORE_POLICY_FORBIDDEN"
[[ -z "${TEST_SITE:-}" && -z "${CI_DB_ROOT_PASSWORD:-}" ]] || fail "DATABASE_ENVIRONMENT_FORBIDDEN"

FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76
ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd
BENCH_DIR="$HOME/ccpc-pr32-version-smoke-bench"
[[ ! -e "$BENCH_DIR" && ! -L "$BENCH_DIR" ]] || fail "NONEMPTY_DESTINATION"

# Keep third-party install stdout/stderr out of CI logs and artifacts.
LOG_DIR="$(mktemp -d "$HOME/.ccpc-bench-version-smoke.XXXXXXXX")"
trap 'rm -f -- "$LOG_DIR"/*.log; rmdir -- "$LOG_DIR"' EXIT
checked() {
  local phase="$1"
  shift
  if ! "$@" >"$LOG_DIR/$phase.log" 2>&1; then
    fail "${phase}_FAIL"
  fi
}

export PATH="$HOME/.local/bin:$PATH"
export GIT_TERMINAL_PROMPT=0
checked apt-update sudo apt-get update -qq
checked apt-deps sudo apt-get install -y --no-install-recommends cron libmariadb-dev libmariadb-dev-compat pkg-config
checked yarn-cli sudo npm install -g yarn
checked bench-cli python -m pip install --user frappe-bench

# Exercise the actual pinned Bench commands; no site or service is created.
checked frappe-init bench init --frappe-branch v16.33.1 --skip-assets --skip-redis-config-generation --python "$(command -v python)" "$BENCH_DIR"
[[ "$(git -C "$BENCH_DIR/apps/frappe" rev-parse HEAD 2>/dev/null)" == "$FRAPPE_SHA" ]] || fail "FRAPPE_SHA_MISMATCH"

cd "$BENCH_DIR"
checked erpnext-install bench get-app --branch v16.34.2 --skip-assets erpnext https://github.com/frappe/erpnext
[[ "$(git -C "$BENCH_DIR/apps/erpnext" rev-parse HEAD 2>/dev/null)" == "$ERPNEXT_SHA" ]] || fail "ERPNEXT_SHA_MISMATCH"
checked dependency-validation bench validate-dependencies

# Specifically reject the transient Bench dependency warning from Build #108.
for phase in frappe-init erpnext-install dependency-validation; do
  if grep -Fq "does not satisfy required version" "$LOG_DIR/$phase.log"; then
    fail "VERSION_REQUIREMENT_WARNING"
  fi
done

printf '%s\n' "PINNED BENCH VERSION SMOKE: EXACT SOURCE SHA PASS"
printf '%s\n' "PINNED BENCH VERSION SMOKE: DEPENDENCY VALIDATION PASS"
printf '%s\n' "PINNED BENCH VERSION SMOKE: NO TRANSIENT VERSION WARNING PASS"
printf '%s\n' "PINNED BENCH VERSION SMOKE: CI-ONLY NO SITE NO BACKUP NO PROD/DEV"

#!/usr/bin/env bash
# Copyright (c) 2026, CCPC and contributors
# Explicit, default-OFF CircleCI PR29 report workflow. Disposable site only.
set -euo pipefail

BRANCH='review/department-original-material-consumption-2026-10-29'
JOB='department-report29-isolated-integration'
SITE='ci-department-report29.localhost'
APPROVED="${CCPC_DEPT29_APPROVED_SHA:-}"
[[ "${CIRCLECI:-}" == true ]]
[[ "${CIRCLE_PROJECT_USERNAME:-}" == CCPCerp_2026 ]]
[[ "${CIRCLE_PROJECT_REPONAME:-}" == ccpcerp ]]
[[ "${CIRCLE_BRANCH:-}" == "$BRANCH" ]]
[[ "${CIRCLE_JOB:-}" == "$JOB" ]]
[[ "${TEST_SITE:-}" == "$SITE" ]]
[[ "${CCPC_ALLOW_DEPT29_TESTS:-}" == isolated-only ]]
[[ -n "${CIRCLE_PIPELINE_ID:-}" && -n "${CIRCLE_WORKFLOW_ID:-}" && -n "${CIRCLE_BUILD_NUM:-}" ]]
[[ "$APPROVED" =~ ^[0-9a-f]{40}$ ]]
[[ "${CIRCLE_SHA1:-}" == "$APPROVED" ]]
[[ "$(git rev-parse HEAD)" == "$APPROVED" ]]
# This validation executes before any package, database or bench operation.
python - <<'PY'
from ccpcerp.ccpc_erp.ci_department29_site_guard import assert_department29_disposable_site
assert_department29_disposable_site("ci-department-report29.localhost")
print("PR29 exact-SHA isolated CircleCI identity verified")
PY

case "${1:-}" in
  identity)
    # Cumulative subprocess budget is a safety ceiling, not a credit cap.
    python .circleci/finance_baseline_deadline.py start
    python .circleci/finance_baseline_deadline.py run python -m unittest discover \
      -s .circleci -p 'test_department29_ci_config_static.py' -v
    python .circleci/finance_baseline_deadline.py run python -m compileall -q ccpcerp
    ;;
  services)
    for port in 3306 13000 11000; do
      ready=0
      for _ in $(seq 1 60); do
        if (echo >/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
          ready=1
          break
        fi
        sleep 2
      done
      [[ "$ready" == 1 ]] || { echo "Disposable CI sidecar unavailable" >&2; exit 1; }
    done
    ;;
  install)
    export PATH="$HOME/.local/bin:$PATH"
    APP_CHECKOUT="$PWD"
    BENCH_DIR="$HOME/ccpc-department29-bench"
    FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76
    ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd
    sudo apt-get update -qq
    sudo apt-get install -y --no-install-recommends cron mariadb-client libmariadb-dev libmariadb-dev-compat pkg-config
    sudo npm install -g yarn
    python -m pip install --user frappe-bench
    MYSQL_PWD="$CI_DB_ROOT_PASSWORD" mariadb --host=127.0.0.1 --user=root \
      -e "SET GLOBAL character_set_server = 'utf8mb4'; SET GLOBAL collation_server = 'utf8mb4_unicode_ci';"
    bench init --frappe-branch version-16 --skip-assets --skip-redis-config-generation \
      --python "$(command -v python)" "$BENCH_DIR"
    cd "$BENCH_DIR"
    git -C apps/frappe fetch --depth 1 upstream "$FRAPPE_SHA"
    git -C apps/frappe checkout --detach "$FRAPPE_SHA"
    bench set-config -g redis_cache redis://127.0.0.1:13000
    bench set-config -g redis_queue redis://127.0.0.1:11000
    bench set-config -g redis_socketio redis://127.0.0.1:11000
    bench get-app --branch version-16 erpnext https://github.com/frappe/erpnext
    git -C apps/erpnext fetch --depth 1 upstream "$ERPNEXT_SHA"
    git -C apps/erpnext checkout --detach "$ERPNEXT_SHA"
    bench get-app ccpcerp "$APP_CHECKOUT"
    [[ "$(git -C apps/frappe rev-parse HEAD)" == "$FRAPPE_SHA" ]]
    [[ "$(git -C apps/erpnext rev-parse HEAD)" == "$ERPNEXT_SHA" ]]
    [[ "$(git -C apps/ccpcerp rev-parse HEAD)" == "$APPROVED" ]]
    bench setup requirements --dev
    bench new-site "$TEST_SITE" --db-host 127.0.0.1 --db-port 3306 \
      --db-root-username root --db-root-password "$CI_DB_ROOT_PASSWORD" \
      --admin-password "$CI_ADMIN_PASSWORD"
    bench --site "$TEST_SITE" install-app erpnext
    bench --site "$TEST_SITE" install-app ccpcerp
    bench --site "$TEST_SITE" migrate
    bench --site "$TEST_SITE" set-config allow_tests true
    echo "PR29 disposable Frappe site installed; no persistent CCPC site used"
    ;;
  tests)
    export PATH="$HOME/.local/bin:$PATH"
    cd "$HOME/ccpc-department29-bench"
    [[ "$(git -C apps/ccpcerp rev-parse HEAD)" == "$APPROVED" ]]
    # The 14 authored Department source policy tests run inside installed Frappe,
    # followed by six genuine MariaDB signed-source/GL/Department tests.
    bench --site "$TEST_SITE" run-tests --module \
      ccpcerp.ccpc_erp.report.ccpc_department_material_consumption.test_ccpc_department_material_consumption
    bench --site "$TEST_SITE" run-tests --module \
      ccpcerp.ccpc_erp.test_department_report29_disposable_integration
    echo "PR29 real Department acceptance finished; review each test and SQL log"
    ;;
  *)
    echo "Expected identity|services|install|tests" >&2
    exit 2
    ;;
esac

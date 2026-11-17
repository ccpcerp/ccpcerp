#!/usr/bin/env bash
# Copyright (c) 2026, CCPC and contributors
# Manual-hold, disposable SQL fixture acceptance. No DEV/PROD site use.
set -euo pipefail

# Exact branch, site and approved immutable SHA on EVERY stage.
test "${CIRCLECI:-}" = "true"
test "${CIRCLE_PROJECT_USERNAME:-}" = "CCPCerp_2026"
test "${CIRCLE_PROJECT_REPONAME:-}" = "ccpcerp"
test "${CIRCLE_BRANCH:-}" = "review/ricor-direct-consumption-billing-item-2026-11-17"
test "${CIRCLE_JOB:-}" = "ricor-direct-consumption-integration"
test "${TEST_SITE:-}" = "ci-ricor-direct.localhost"
test -n "${CIRCLE_PIPELINE_ID:-}"
test -n "${CIRCLE_WORKFLOW_ID:-}"
test -n "${CIRCLE_BUILD_NUM:-}"
test -n "${CCPC_RICOR_APPROVED_SHA:-}"
test "${CIRCLE_SHA1:-}" = "${CCPC_RICOR_APPROVED_SHA}"
test "$(git rev-parse HEAD)" = "${CCPC_RICOR_APPROVED_SHA}"
test "${CCPC_ALLOW_RICOR_DIRECT_FIXTURES:-}" = "isolated-only"

case "${1:-}" in
  services)
    for port in 3306 13000 11000; do
      ready=0
      for i in $(seq 1 60); do
        if (echo >/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
          ready=1
          break
        fi
        sleep 2
      done
      test "$ready" = 1 || { echo "Required isolated sidecar missing"; exit 1; }
    done
    ;;
  install)
    export PATH="$HOME/.local/bin:$PATH"
    APP_CHECKOUT="$PWD"
    BENCH_DIR="$HOME/ccpc-ricor-direct-bench"
    # Reuse previously reviewed known pinned v16 sources (Billing Controls).
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
    test "$(git -C apps/frappe rev-parse HEAD)" = "$FRAPPE_SHA"
    test "$(git -C apps/erpnext rev-parse HEAD)" = "$ERPNEXT_SHA"
    test "$(git -C apps/ccpcerp rev-parse HEAD)" = "$CCPC_RICOR_APPROVED_SHA"
    bench setup requirements --dev
    bench new-site "$TEST_SITE" --db-host 127.0.0.1 --db-port 3306 \
      --db-root-username root --db-root-password "$CI_DB_ROOT_PASSWORD" \
      --admin-password "$CI_ADMIN_PASSWORD"
    bench --site "$TEST_SITE" install-app erpnext
    bench --site "$TEST_SITE" install-app ccpcerp
    bench --site "$TEST_SITE" migrate
    bench --site "$TEST_SITE" set-config allow_tests true
    echo "Disposable Ricor direct-consumption site installed; no real Company data read"
    ;;
  tests)
    export PATH="$HOME/.local/bin:$PATH"
    cd "$HOME/ccpc-ricor-direct-bench"
    test "$(git -C apps/ccpcerp rev-parse HEAD)" = "$CCPC_RICOR_APPROVED_SHA"
    # This suite creates only uniquely named synthetic Item/User/PI fixtures.
    bench --site "$TEST_SITE" run-tests \
      --module ccpcerp.ccpc_erp.test_ricor_direct_consumption_real_fixtures
    echo "Ricor synthetic native PI and mapping actor acceptance completed"
    ;;
  *)
    echo "Unknown Ricor direct-consumption CI stage" >&2
    exit 2
    ;;
esac

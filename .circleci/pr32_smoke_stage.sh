#!/usr/bin/env bash
# Synthetic-only held CircleCI site-isolation smoke. NO real backup/restore.
set -euo pipefail
umask 077
export PATH="$HOME/.local/bin:$PATH"

python .circleci/ci_sidecar_smoke.py identity
case "${1:-}" in
  prerequisites)
    for port in 3306 13000 11000; do
      ready=0
      for _ in $(seq 1 60); do
        if (echo >/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
          ready=1
          break
        fi
        sleep 2
      done
      [[ "$ready" == 1 ]] || { echo "Disposable sidecar not reachable"; exit 1; }
    done
    sudo apt-get update -qq
    sudo apt-get install -y --no-install-recommends cron mariadb-client libmariadb-dev libmariadb-dev-compat pkg-config
    sudo npm install -g yarn
    python -m pip install --user frappe-bench
    echo "DISPOSABLE SIDECAR PREREQUISITES: PASS"
    ;;
  bench)
    APP_CHECKOUT="$PWD"
    BENCH_DIR="$HOME/ccpc-pr32-restore-bench"
    [[ ! -e "$BENCH_DIR" && ! -L "$BENCH_DIR" ]]
    FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76
    ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd
    ORIGINAL_APP_SHA=0c223d7cbe8b27c2392d8b2303c45a99dd4d8b41
    # This is a new disposable bench; unlike a restore, no site/DB exists yet.
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
    # CircleCI's local checkout may be a partial Git object store even when
    # checkout of an ancestor exits zero. Fetch the pinned original SHA from
    # the private authoritative GitHub repo using CircleCI's existing GitHub
    # checkout deploy key (no new CI/DEV/PROD credentials).
    # Fail closed if that key cannot read the pinned commit.
    git -C apps/ccpcerp -c fetch.fsckObjects=true fetch --no-tags --depth 1 \
      git@github.com:ccpcerp/ccpcerp.git "$ORIGINAL_APP_SHA"
    git -C apps/ccpcerp checkout --detach "$ORIGINAL_APP_SHA"
    # Exact HEAD alone was insufficient in Build #54: eight tracked blobs
    # were absent despite a zero-exit checkout and a green follow-on test.
    if ! git -C apps/ccpcerp fsck --full --strict --no-reflogs >/dev/null 2>&1; then
      echo "Original app Git object integrity cannot be proven" >&2
      exit 1
    fi
    if [[ -n "$(git -C apps/ccpcerp status --porcelain=v1 --untracked-files=no)" ]]; then
      echo "Original app pinned worktree is incomplete or changed" >&2
      exit 1
    fi
    [[ "$(git -C apps/frappe rev-parse HEAD)" == "$FRAPPE_SHA" ]]
    [[ "$(git -C apps/erpnext rev-parse HEAD)" == "$ERPNEXT_SHA" ]]
    [[ "$(git -C apps/ccpcerp rev-parse HEAD)" == "$ORIGINAL_APP_SHA" ]]
    bench setup requirements --dev
    echo "PINNED DISPOSABLE BENCH SOURCES: PASS"
    ;;
  *)
    echo "Expected prerequisites or bench phase" >&2
    exit 2
    ;;
esac

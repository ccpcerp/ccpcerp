#!/usr/bin/env bash
# Held Integration2 restore: PINNED disposable bench only. Never run on DEV/PROD.
set -euo pipefail
umask 077
export PATH="$HOME/.local/bin:$PATH"

python .circleci/restore_guard.py
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
    sudo apt-get install -y --no-install-recommends age
    echo "ISOLATED RESTORE PREREQUISITES: PASS"
    ;;
  bench)
    APP_CHECKOUT="$PWD"
    BENCH_DIR="$HOME/ccpc-pr32-restore-bench"
    [[ ! -e "$BENCH_DIR" && ! -L "$BENCH_DIR" ]]
    FRAPPE_SHA=988e54f3c4c291e2077a83809663f123731abe76
    ERPNEXT_SHA=4048fb70e14d1843956fcdabb7c3cca75a1cbcdd
    ORIGINAL_APP_SHA=0c223d7cbe8b27c2392d8b2303c45a99dd4d8b41
    # This is a new disposable bench; unlike a restore, no site/DB exists yet.
    # Clone the exact Frappe release at initialization, not moving version-16.
    # GitHub v16.33.1 tag resolves to the immutable FRAPPE_SHA below.
    bench init --frappe-branch v16.33.1 --skip-assets --skip-redis-config-generation \
      --python "$(command -v python)" "$BENCH_DIR"
    cd "$BENCH_DIR"
    [[ "$(git -C apps/frappe rev-parse HEAD)" == "$FRAPPE_SHA" ]]
    git -C apps/frappe fetch --depth 1 upstream "$FRAPPE_SHA"
    git -C apps/frappe checkout --detach "$FRAPPE_SHA"
    bench set-config -g redis_cache redis://127.0.0.1:13000
    bench set-config -g redis_queue redis://127.0.0.1:11000
    bench set-config -g redis_socketio redis://127.0.0.1:11000
    # Pin the initial get-app clone to the same verified release as the
    # immutable source checkout. The moving version-16 branch can require
    # a newer Frappe than this historical, intentionally pinned CI bench.
    # Upstream tag v16.34.2 resolves to ERPNEXT_SHA as verified on GitHub.
    bench get-app --branch v16.34.2 erpnext https://github.com/frappe/erpnext
    # Fail closed if the upstream release tag is ever retargeted. Do not
    # silently install arbitrary tag contents and later reset to a pinned SHA.
    [[ "$(git -C apps/erpnext rev-parse HEAD)" == "$ERPNEXT_SHA" ]]
    git -C apps/erpnext fetch --depth 1 upstream "$ERPNEXT_SHA"
    git -C apps/erpnext checkout --detach "$ERPNEXT_SHA"
    bench get-app ccpcerp "$APP_CHECKOUT"
    # Build #86 showed that even direct fetch into bench get-app's inherited
    # partial-clone repository fails. Avoid carrying its promisor state at all.
    # Construct, verify and check out the exact pinned revision in a NEW,
    # empty scratch Git repo, using only CircleCI's authenticated HTTPS origin.
    # This bench is newly disposable, with no site or user data.
    FRESH_SOURCE_DIR="$(mktemp -d "$BENCH_DIR/.ccpc-pinned-source.XXXXXXXX")"
    if ! timeout --signal=TERM --kill-after=5s 420 \
      python "$APP_CHECKOUT/.circleci/ci_fresh_pinned_source.py" \
        "$APP_CHECKOUT" "$FRESH_SOURCE_DIR"; then
      echo "Fresh pinned historical app source could not be verified" >&2
      exit 1
    fi
    # Preserve the original incomplete local clone inside disposable bench
    # instead of deleting it. Never act on a site/DEV/PROD path.
    [[ -d "$BENCH_DIR/apps/ccpcerp" && ! -L "$BENCH_DIR/apps/ccpcerp" ]]
    [[ ! -e "$BENCH_DIR/.ccpc-inherited-app-clone" &&
       ! -L "$BENCH_DIR/.ccpc-inherited-app-clone" ]]
    mv "$BENCH_DIR/apps/ccpcerp" "$BENCH_DIR/.ccpc-inherited-app-clone"
    mv "$FRESH_SOURCE_DIR" "$BENCH_DIR/apps/ccpcerp"
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
    echo "PINNED CLEAN RESTORE BENCH SOURCES: PASS"
    ;;
  *)
    echo "Expected prerequisites or bench phase" >&2
    exit 2
    ;;
esac

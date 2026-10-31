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
    # GitHub App CircleCI checkout uses an HTTPS credential helper, NOT an
    # SSH deploy key. Fetch the pinned ancestor through that exact authenticated
    # checkout; then transfer it into the disposable bench from the local path.
    # No new token, SSH trust-on-first-use, credential files, or interactive
    # prompts. If access is unavailable, stop with a sanitized fixed message.
    SOURCE_REMOTE="$(git -C "$APP_CHECKOUT" remote get-url origin 2>/dev/null || true)"
    case "$SOURCE_REMOTE" in
      https://github.com/ccpcerp/ccpcerp|https://github.com/ccpcerp/ccpcerp.git) ;;
      *) echo "Expected authenticated checkout origin missing" >&2; exit 1 ;;
    esac
    unset SOURCE_REMOTE
    export GIT_TERMINAL_PROMPT=0
    export GCM_INTERACTIVE=Never
    if ! timeout --signal=TERM --kill-after=5s 120 git -C "$APP_CHECKOUT" \
      -c fetch.fsckObjects=true fetch --no-tags --depth 1 origin \
      "$ORIGINAL_APP_SHA" >/dev/null 2>&1; then
      echo "Pinned original commit unavailable from authenticated checkout" >&2
      exit 1
    fi
    if ! timeout --signal=TERM --kill-after=5s 60 git -C apps/ccpcerp \
      -c fetch.fsckObjects=true fetch --no-tags --depth 1 \
      "$APP_CHECKOUT" "$ORIGINAL_APP_SHA" >/dev/null 2>&1; then
      echo "Pinned original commit could not be copied into isolated bench" >&2
      exit 1
    fi
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

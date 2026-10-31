"""Opt-in no-Context GitHub App original-source transfer diagnostic.

No B2 credentials, age keys, DB, Frappe bench, or deployments. A small CircleCI
job uses its already-authenticated checkout to test precisely the source
fetch / local clone / original SHA transfer that failed in Builds #74 and #80.
Raw Git command stderr, URLs and credential-helper output are never logged.
"""
import os
from pathlib import Path
import subprocess
import tempfile



BRANCH = "review/pr32-integration2-isolated-restore-2026-10-30"
ORIGINAL_SHA = "0c223d7cbe8b27c2392d8b2303c45a99dd4d8b41"
ORIGIN = ("https://github.com/ccpcerp/ccpcerp",
          "https://github.com/ccpcerp/ccpcerp.git")
LOCAL_REF = "refs/ccpc/ci-source-probe/original"


def classify(stderr):
    """Return fixed category only: never echo remote output or credentials."""
    e = stderr.lower()
    if "lazy fetching disabled" in e or "promisor remote" in e:
        return "promisor-missing-objects"
    if "not our ref" in e or "unadvertised object" in e or "server does not allow" in e:
        return "unadvertised-object"
    if "shallow" in e:
        return "shallow-boundary"
    if "missing" in e and ("blob" in e or "object" in e):
        return "missing-objects"
    if "permission denied" in e or "authentication failed" in e or "could not read username" in e:
        return "authentication"
    if "repository corruption" in e or "corrupt" in e or "bad object" in e:
        return "git-integrity"
    if "not a git repository" in e:
        return "repository-structure"
    return "other"


def run_git(*args, timeout=60, no_lazy_fetch=False):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    if no_lazy_fetch:
        env["GIT_NO_LAZY_FETCH"] = "1"
    try:
        return subprocess.run(
            ["git", *map(str, args)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["git"], 124, "", "")


def gate(condition, stage):
    if not condition:
        raise RuntimeError("SOURCE GIT PROBE: " + stage + " DENIED")


def require_ok(result, stage):
    if result.returncode:
        raise RuntimeError(
            "SOURCE GIT PROBE: " + stage + " FAIL (exit "
            + str(result.returncode) + ", class " + classify(result.stderr) + ")"
        )
    return result.stdout.strip()


def git_integrity(bench):
    check = run_git("-C", bench, "checkout", "--quiet", "--detach", ORIGINAL_SHA)
    require_ok(check, "checkout")
    check = run_git("-C", bench, "fsck", "--full", "--strict", "--no-reflogs")
    require_ok(check, "full-fsck")
    check = run_git("-C", bench, "status", "--porcelain=v1", "--untracked-files=no")
    gate(not require_ok(check, "tracked-status"), "dirty-worktree")
    actual = require_ok(run_git("-C", bench, "rev-parse", "HEAD"), "head")
    gate(actual == ORIGINAL_SHA, "original-sha-mismatch")


def main():
    env = os.environ
    gate(env.get("CIRCLECI") == "true", "not-circleci")
    gate(env.get("CIRCLE_PROJECT_USERNAME") == "CCPCerp_2026", "wrong-owner")
    gate(env.get("CIRCLE_PROJECT_REPONAME") == "ccpcerp", "wrong-repository")
    gate(env.get("CIRCLE_BRANCH") == BRANCH, "wrong-branch")
    gate(env.get("CIRCLE_JOB") == "pr32-git-source-probe", "wrong-job")
    gate(bool(env.get("CIRCLE_PIPELINE_ID") and env.get("CIRCLE_WORKFLOW_ID")), "missing-ci-identity")
    gate(bool(env.get("CIRCLE_BUILD_NUM")), "missing-build-identity")
    checkout = Path.cwd().resolve(strict=True)
    gate(require_ok(run_git("-C", checkout, "rev-parse", "HEAD"), "review-head")
         == env.get("CIRCLE_SHA1"), "review-sha-mismatch")
    origin = require_ok(
        run_git("-C", checkout, "remote", "get-url", "origin"), "checkout-origin"
    )
    gate(origin in ORIGIN, "unexpected-https-origin")
    with tempfile.TemporaryDirectory(prefix="ccpc-git-source-probe-") as scratch:
        bench = Path(scratch) / "bench-ccpcerp"
        # Match bench get-app local clone order: copy the CircleCI checkout
        # BEFORE fetching its older pinned SHA from the authenticated origin.
        require_ok(
            run_git("clone", "--quiet", "--branch", BRANCH, checkout, bench),
            "clone-disposable-bench-source",
        )
        # Build #84: local upload-pack fails even after all eight historical
        # blobs are hydrated. Use the GitHub App authenticated HTTPS origin
        # directly from the bench repository, not the partial clone as a
        # temporary Git server. This is the exact method in restore_stage.sh.
        result = run_git("-C", bench, "-c", "fetch.fsckObjects=true",
                         "fetch", "--quiet", "--no-tags", "--no-filter",
                         "--depth", "1", origin, ORIGINAL_SHA, timeout=180)
        require_ok(result, "authenticated-direct-origin-fetch")
        git_integrity(bench)
        print("SOURCE GIT PROBE: DIRECT AUTHENTICATED ORIGIN TRANSFER PASS", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Only our fixed-category RuntimeErrors are permitted to reach logs.
        if isinstance(error, RuntimeError) and str(error).startswith("SOURCE GIT PROBE:"):
            print(str(error), flush=True)
        else:
            print("SOURCE GIT PROBE: unexpected diagnostic failure (sanitized)", flush=True)
        raise SystemExit(1) from None

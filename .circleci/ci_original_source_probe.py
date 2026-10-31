"""Opt-in no-Context GitHub App pinned-source diagnostic.

Builds #82/#84/#86 found missing objects or failed fetches when reusing
CircleCI's inherited partial clone as either source or destination.
This probe validates a new empty Git repository, then tests the exact
disposable bench swap. No B2/age keys, Frappe, database or deployment.
Raw Git stderr, URLs and credential-helper output never reach CI logs.
"""
import os
from pathlib import Path
import subprocess
import tempfile

from ci_fresh_pinned_source import SourceFailure, prepare_fresh_source


BRANCH = "review/pr32-integration2-isolated-restore-2026-10-30"
ORIGINAL_SHA = "0c223d7cbe8b27c2392d8b2303c45a99dd4d8b41"
ORIGIN = ("https://github.com/ccpcerp/ccpcerp",
          "https://github.com/ccpcerp/ccpcerp.git")


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
        fresh = Path(scratch) / "fresh-pinned-ccpcerp"
        prior = Path(scratch) / "inherited-partial-clone"
        # Match restore bench get-app: first clone inherited CircleCI source
        # into the new disposable bench location.
        require_ok(run_git("clone", "--quiet", "--branch", BRANCH, checkout, bench),
                   "clone-inherited-disposable-bench-source")
        # Fetch the pinned historical source into an independent EMPTY Git
        # repository, not into the inherited blobless/promisor bench clone.
        fresh.mkdir()
        try:
            prepare_fresh_source(fresh, origin, ORIGINAL_SHA)
        except SourceFailure as error:
            raise RuntimeError("SOURCE GIT PROBE: fresh-" + str(error) + " FAIL") from None
        print("SOURCE GIT PROBE: FRESH PINNED REPOSITORY FULL FSCK PASS", flush=True)
        # The real restore swaps the incomplete local checkout only inside a
        # newly created disposable bench, retaining the prior clone safely.
        bench.rename(prior)
        fresh.rename(bench)
        git_integrity(bench)
        print("SOURCE GIT PROBE: FRESH SOURCE BENCH SWAP PASS", flush=True)


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

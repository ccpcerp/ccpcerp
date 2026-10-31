"""Construct a fully verified pinned source from a FRESH disposable Git repository.

Never fetch the historical source into CircleCI's inherited blobless/partial
clone. Raw Git stderr and credential-helper output are captured, not printed.
No B2, age identity, MariaDB, deployment or unrelated host resources.
"""
import os
from pathlib import Path
import re
import subprocess
import sys


BRANCH = "review/pr32-integration2-isolated-restore-2026-10-30"
PINNED_SHA = "0c223d7cbe8b27c2392d8b2303c45a99dd4d8b41"
ORIGINS = (
    "https://github.com/ccpcerp/ccpcerp",
    "https://github.com/ccpcerp/ccpcerp.git",
)


class SourceFailure(Exception):
    """Only fixed category text is ever exposed to CI logs."""


def git(*args, stage, timeout=60, no_lazy_fetch=False):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    if no_lazy_fetch:
        env["GIT_NO_LAZY_FETCH"] = "1"
    else:
        env.pop("GIT_NO_LAZY_FETCH", None)
    try:
        result = subprocess.run(
            ["git", *map(str, args)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise SourceFailure(stage + "-timeout") from None
    if result.returncode:
        raise SourceFailure(stage + "-failed")
    return result.stdout.strip()


def prepare_fresh_source(target, origin, sha=PINNED_SHA):
    """Prepare a fresh EMPTY scratch repo and prove its pinned Git integrity.

    The caller is responsible for requiring an allowlisted origin. The CLI
    and CI probe both enforce it. Tests may use a generated file:// fixture.
    """
    target = Path(target)
    if not target.is_dir() or target.is_symlink() or any(target.iterdir()):
        raise SourceFailure("scratch-not-empty")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SourceFailure("invalid-sha")
    git("init", "--quiet", target, stage="fresh-init")
    git("-C", target, "remote", "add", "origin", origin, stage="fresh-origin")
    git("-C", target, "-c", "fetch.fsckObjects=true", "fetch", "--quiet",
        "--no-tags", "--no-filter", "--depth", "1", "origin", sha,
        stage="fresh-origin-fetch", timeout=180)
    git("-C", target, "checkout", "--quiet", "--detach", sha,
        stage="pinned-checkout")
    missing = git("-C", target, "rev-list", "--objects", "--missing=print", sha,
                  stage="pinned-missing-scan", no_lazy_fetch=True)
    if any(line.startswith("?") for line in missing.splitlines()):
        raise SourceFailure("pinned-objects-missing")
    git("-C", target, "fsck", "--full", "--strict", "--no-reflogs",
        stage="strict-full-fsck", timeout=90)
    if git("-C", target, "status", "--porcelain=v1", "--untracked-files=no",
           stage="tracked-status"):
        raise SourceFailure("dirty-pinned-worktree")
    if git("-C", target, "rev-parse", "HEAD", stage="pinned-head") != sha:
        raise SourceFailure("pinned-sha-mismatch")


def main():
    env = os.environ
    if env.get("CIRCLECI") != "true" or env.get("CIRCLE_PROJECT_USERNAME") != "CCPCerp_2026":
        raise SourceFailure("ci-project-denied")
    if env.get("CIRCLE_PROJECT_REPONAME") != "ccpcerp" or env.get("CIRCLE_BRANCH") != BRANCH:
        raise SourceFailure("ci-branch-denied")
    if env.get("CIRCLE_JOB") != "integration2-backup-restore-isolated":
        raise SourceFailure("ci-job-denied")
    if not (env.get("CIRCLE_SHA1") and env.get("CIRCLE_PIPELINE_ID")
            and env.get("CIRCLE_WORKFLOW_ID") and env.get("CIRCLE_BUILD_NUM")):
        raise SourceFailure("ci-identity-incomplete")
    if len(sys.argv) != 3:
        raise SourceFailure("expected-checkout-and-scratch")
    checkout = Path(sys.argv[1]).resolve(strict=True)
    scratch = Path(sys.argv[2]).resolve(strict=True)
    if not scratch.is_relative_to(checkout.parent) and not scratch.is_relative_to(Path.home()):
        raise SourceFailure("scratch-outside-disposable-home")
    actual_sha = git("-C", checkout, "rev-parse", "HEAD", stage="review-sha")
    if actual_sha != env["CIRCLE_SHA1"]:
        raise SourceFailure("review-sha-mismatch")
    origin = git("-C", checkout, "remote", "get-url", "origin", stage="origin")
    if origin not in ORIGINS:
        raise SourceFailure("origin-not-allowlisted")
    prepare_fresh_source(scratch, origin)
    print("PINNED FRESH SOURCE: FULL GIT INTEGRITY PASS", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SourceFailure as exc:
        print("PINNED FRESH SOURCE: FAIL (" + str(exc) + ")", flush=True)
        raise SystemExit(1) from None
    except Exception:
        print("PINNED FRESH SOURCE: FAIL (sanitized)", flush=True)
        raise SystemExit(1) from None

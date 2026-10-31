"""Hydrate a pinned original Git tree before copying a partial checkout locally.

The existing CircleCI GitHub App credential helper is used indirectly by
Git's promisor lazy-fetch mechanism. Never log Git stderr or object IDs.
"""

import os
from pathlib import Path
import re
import subprocess
import sys


ORIGINAL_SHA = "0c223d7cbe8b27c2392d8b2303c45a99dd4d8b41"
MAX_MISSING_OBJECTS = 128


class HydrationFailure(Exception):
    """Fixed-message diagnostic; may be printed without exposing Git stderr."""


def _git(repo, args, *, no_lazy_fetch=False, timeout=60):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    if no_lazy_fetch:
        env["GIT_NO_LAZY_FETCH"] = "1"
    else:
        env.pop("GIT_NO_LAZY_FETCH", None)
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise HydrationFailure("git-operation-timeout") from None
    if result.returncode != 0:
        raise HydrationFailure("git-operation-failed")
    return result.stdout


def missing_object_ids(repo, sha):
    """Inventory without accidental network hydration or printing object IDs."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise HydrationFailure("invalid-pinned-sha")
    output = _git(repo, ["rev-list", "--objects", "--missing=print", sha],
                  no_lazy_fetch=True)
    missing = []
    for line in output.splitlines():
        if line.startswith("?"):
            oid = line[1:]
            if not re.fullmatch(r"[0-9a-f]{40}", oid):
                raise HydrationFailure("unexpected-missing-object-format")
            missing.append(oid)
    return missing


def hydrate_missing(repo, sha=ORIGINAL_SHA):
    """Fetch promised historical objects, then prove none remain missing.

    Existing remote and authenticated checkout are unchanged. Only local Git
    object storage is hydrated. Do not disable fsck or SHA pinning.
    """
    if not Path(repo).is_dir():
        raise HydrationFailure("checkout-missing")
    before = missing_object_ids(repo, sha)
    if len(before) > MAX_MISSING_OBJECTS:
        raise HydrationFailure("missing-object-limit-exceeded")
    for oid in before:
        # Accessing a promised object asks Git's configured promisor remote to
        # supply it. Never use GIT_NO_LAZY_FETCH for this command.
        _git(repo, ["cat-file", "-e", oid], timeout=45)
    after = missing_object_ids(repo, sha)
    if after:
        raise HydrationFailure("promised-objects-still-missing")
    return len(before)


def main():
    # Entrypoint is used only after the existing restore_guard has verified CI.
    if len(sys.argv) != 2:
        raise HydrationFailure("expected-one-checkout-path")
    count = hydrate_missing(sys.argv[1])
    print("SOURCE HYDRATION: missing-before=" + str(count), flush=True)
    print("SOURCE HYDRATION: PINNED ORIGINAL OBJECTS READY", flush=True)


if __name__ == "__main__":
    try:
        main()
    except HydrationFailure as exc:
        print("SOURCE HYDRATION: FAIL (" + str(exc) + ")", flush=True)
        raise SystemExit(1) from None
    except Exception:
        print("SOURCE HYDRATION: FAIL (sanitized)", flush=True)
        raise SystemExit(1) from None

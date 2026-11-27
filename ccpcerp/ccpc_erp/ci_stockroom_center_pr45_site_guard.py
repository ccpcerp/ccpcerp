# Copyright (c) 2026, CCPC and contributors
"""Independent GitHub Actions PR45-only disposable Finance DB test gate.

This is NOT a generic financial workflow authorizer or a substitute for actual
Accounting approval. Never use it for a DEVELOPMENT or PRODUCTION site.
The unrelated CircleCI Finance lifecycle guard remains unchanged.
"""
import json
import os
import re

REPO = "ccpcerp/ccpcerp"
WORKFLOW = "CCPC Stockroom Center PR45 - disposable Finance DB"
WORKFLOW_FILE = "stockroom-center-pr45-disposable-db.yml"
PR_NUMBER = 45
PR_HEAD_BRANCH = "review/stockroom-center-proposer-2026-11-27"
PR_BASE_BRANCH = "review/phase1-maintenance-integrated-tests-2026-11-24"
SITE = "ci-stockroom-center-pr45.localhost"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def is_reviewed_stockroom_pr45_disposable_site(site):
    env = os.environ
    head = env.get("CCPC_STOCKROOM_PR_HEAD", "")
    event_path = env.get("GITHUB_EVENT_PATH", "")
    if not (
        type(site) is str and site == SITE
        and env.get("GITHUB_ACTIONS") == "true"
        and env.get("CI") == "true"
        and env.get("GITHUB_EVENT_NAME") == "pull_request"
        and env.get("GITHUB_REPOSITORY") == REPO
        and env.get("GITHUB_HEAD_REF") == PR_HEAD_BRANCH
        and env.get("GITHUB_BASE_REF") == PR_BASE_BRANCH
        and env.get("GITHUB_REF") == "refs/pull/45/merge"
        and env.get("GITHUB_WORKFLOW") == WORKFLOW
        and env.get("GITHUB_JOB") == "isolated-stockroom-center-db"
        and env.get("GITHUB_RUN_ATTEMPT") == "1"
        and (env.get("GITHUB_RUN_ID") or "").isdigit()
        and int(env.get("GITHUB_RUN_ID") or "0") > 0
        and env.get("CCPC_ALLOW_STOCKROOM_PR45_DB_TEST") == "disposable-only"
        and SHA_RE.fullmatch(head)
        and event_path and os.path.isfile(event_path)
    ):
        return False
    try:
        with open(event_path, encoding="utf-8") as handle:
            event = json.load(handle)
        pr = event["pull_request"]
        return (
            event.get("action") == "synchronize"
            and pr["number"] == PR_NUMBER
            and pr["head"]["repo"]["full_name"] == REPO
            and pr["head"]["ref"] == PR_HEAD_BRANCH
            and pr["head"]["sha"] == head
            and pr["base"]["repo"]["full_name"] == REPO
            and pr["base"]["ref"] == PR_BASE_BRANCH
            and pr.get("draft") is True
        )
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return False


def assert_stockroom_pr45_disposable_site(site):
    if not is_reviewed_stockroom_pr45_disposable_site(site):
        raise RuntimeError(
            "Stockroom Finance database integration requires the exact "
            "disposable GitHub PR45 site and verified review event."
        )

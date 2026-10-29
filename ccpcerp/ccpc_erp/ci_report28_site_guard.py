# Copyright (c) 2026, CCPC and contributors
"""Independent fail-closed gate for PR #28 disposable CircleCI report tests.

No DEV/PROD site, arbitrary branch, local bench, PR label or pipeline boolean
can authorize the tests. The approved SHA comes from a separately controlled
CircleCI project variable set only for a bounded manually held run.
"""
import os
import re

REPORT_BRANCH = "review/intercompany-source-billing-report-2026-10-29"
REPORT_JOB = "intercompany-report28-isolated-integration"
REPORT_SITE = "ci-intercompany-report28.localhost"
_SHA = re.compile(r"^[0-9a-f]{40}$")


def is_reviewed_report28_disposable_site(site):
    env = os.environ
    actual = env.get("CIRCLE_SHA1") or ""
    approved = env.get("CCPC_REPORT28_APPROVED_SHA") or ""
    return (
        type(site) is str
        and site == REPORT_SITE
        and env.get("CIRCLECI") == "true"
        and env.get("CIRCLE_PROJECT_USERNAME") == "CCPCerp_2026"
        and env.get("CIRCLE_PROJECT_REPONAME") == "ccpcerp"
        and env.get("CIRCLE_BRANCH") == REPORT_BRANCH
        and env.get("CIRCLE_JOB") == REPORT_JOB
        and all(env.get(key) for key in (
            "CIRCLE_PIPELINE_ID", "CIRCLE_WORKFLOW_ID", "CIRCLE_BUILD_NUM",
        ))
        and bool(_SHA.fullmatch(actual))
        and bool(_SHA.fullmatch(approved))
        and actual == approved
        and env.get("CCPC_ALLOW_REPORT28_TESTS") == "isolated-only"
    )


def assert_report28_disposable_site(site):
    if not is_reviewed_report28_disposable_site(site):
        raise RuntimeError(
            "PR28 report integration requires an explicitly approved, "
            "exact-SHA disposable CircleCI site and job."
        )

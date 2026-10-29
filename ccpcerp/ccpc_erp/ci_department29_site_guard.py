# Copyright (c) 2026, CCPC and contributors
"""Independent exact-SHA gate for PR29 synthetic Department report MariaDB tests.

The report's five integration tests must never run on an arbitrary bench,
DEVELOPMENT, PRODUCTION, or the PR28/Finance disposables. Approval is supplied
through a separately controlled CircleCI project environment variable, not a
pipeline parameter or a GitHub PR label.
"""
import os
import re

DEPT_BRANCH = "review/department-original-material-consumption-2026-10-29"
DEPT_JOB = "department-report29-isolated-integration"
DEPT_SITE = "ci-department-report29.localhost"
_SHA = re.compile(r"^[0-9a-f]{40}$")


def is_approved_department29_disposable_site(site):
    env = os.environ
    actual = env.get("CIRCLE_SHA1") or ""
    approved = env.get("CCPC_DEPT29_APPROVED_SHA") or ""
    return (
        type(site) is str
        and site == DEPT_SITE
        and env.get("CIRCLECI") == "true"
        and env.get("CIRCLE_PROJECT_USERNAME") == "CCPCerp_2026"
        and env.get("CIRCLE_PROJECT_REPONAME") == "ccpcerp"
        and env.get("CIRCLE_BRANCH") == DEPT_BRANCH
        and env.get("CIRCLE_JOB") == DEPT_JOB
        and all(env.get(key) for key in (
            "CIRCLE_PIPELINE_ID", "CIRCLE_WORKFLOW_ID", "CIRCLE_BUILD_NUM",
        ))
        and bool(_SHA.fullmatch(actual))
        and bool(_SHA.fullmatch(approved))
        and actual == approved
        and env.get("CCPC_ALLOW_DEPT29_TESTS") == "isolated-only"
    )


def assert_department29_disposable_site(site):
    if not is_approved_department29_disposable_site(site):
        raise RuntimeError(
            "PR29 Department report tests require an explicitly approved "
            "exact-SHA disposable CircleCI job and site."
        )

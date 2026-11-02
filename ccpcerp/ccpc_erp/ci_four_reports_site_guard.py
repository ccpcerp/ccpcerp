# Copyright (c) 2026, CCPC and contributors
"""Exact-SHA, CircleCI-only, disposable database gate for combined reports.

The approved SHA is a project environment variable set by the operator, not
a pipeline parameter or a value committed here. No real CCPC site is allowed.
"""
import os
import re

BRANCH = "review/four-day-one-reports-integration-2026-10-29"
JOB = "four-reports-isolated-integration"
SITE = "ci-four-reports.localhost"
APPROVED_VARIABLE = "CCPC_FOUR_REPORTS_APPROVED_SHA"
SHA = re.compile(r"^[a-f0-9]{40}$")


def is_approved_site(site):
    env = os.environ
    actual = env.get("CIRCLE_SHA1", "")
    approved = env.get(APPROVED_VARIABLE, "")
    return (
        type(site) is str
        and site == SITE
        and env.get("CIRCLECI") == "true"
        and env.get("CIRCLE_PROJECT_USERNAME") == "CCPCerp_2026"
        and env.get("CIRCLE_PROJECT_REPONAME") == "ccpcerp"
        and env.get("CIRCLE_BRANCH") == BRANCH
        and env.get("CIRCLE_JOB") == JOB
        and all(env.get(name) for name in (
            "CIRCLE_PIPELINE_ID", "CIRCLE_WORKFLOW_ID", "CIRCLE_BUILD_NUM",
        ))
        and bool(SHA.fullmatch(actual))
        and bool(SHA.fullmatch(approved))
        and actual == approved
        and env.get("CCPC_ALLOW_FOUR_REPORTS_TESTS") == "isolated-only"
    )


def assert_approved_site(site):
    if not is_approved_site(site):
        raise RuntimeError(
            "Combined report fixture requires exact-SHA, held CircleCI "
            "job and disposable site; DEVELOPMENT/PRODUCTION forbidden."
        )

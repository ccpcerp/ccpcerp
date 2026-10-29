# Copyright (c) 2026, CCPC and contributors
"""Independent exact-SHA and disposable-site CI gate for PR31.

The approved SHA comes from CircleCI project settings, NEVER a pipeline param.
Block DEVELOPMENT/PRODUCTION and all unrelated CircleCI jobs/sites.
"""
import os
import re

BRANCH = "review/company-source-pnl-gl-reconciliation-2026-10-29"
JOB = "company-gl-report31-isolated-integration"
SITE = "ci-company-gl-report31.localhost"
SHA = re.compile(r"^[a-f0-9]{40}$")


def is_approved_site(site):
    env = os.environ
    actual = env.get("CIRCLE_SHA1", "")
    approved = env.get("CCPC_COMPANY31_APPROVED_SHA", "")
    return (
        type(site) is str
        and site == SITE
        and env.get("CIRCLECI") == "true"
        and env.get("CIRCLE_PROJECT_USERNAME") == "CCPCerp_2026"
        and env.get("CIRCLE_PROJECT_REPONAME") == "ccpcerp"
        and env.get("CIRCLE_BRANCH") == BRANCH
        and env.get("CIRCLE_JOB") == JOB
        and all(env.get(key) for key in (
            "CIRCLE_PIPELINE_ID", "CIRCLE_WORKFLOW_ID", "CIRCLE_BUILD_NUM",
        ))
        and bool(SHA.fullmatch(actual))
        and bool(SHA.fullmatch(approved))
        and actual == approved
        and env.get("CCPC_ALLOW_COMPANY31_TESTS") == "isolated-only"
    )


def assert_approved_site(site):
    if not is_approved_site(site):
        raise RuntimeError(
            "PR31 Company31 integration requires exact-SHA approved "
            "CircleCI job/site; no DEVELOPMENT or PRODUCTION test permitted."
        )

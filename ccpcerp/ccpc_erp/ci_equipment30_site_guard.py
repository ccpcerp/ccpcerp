# Copyright (c) 2026, CCPC and contributors
"""Independent exact-SHA and disposable-site CI gate for PR30.

The approved SHA comes from CircleCI project settings, NEVER a pipeline param.
Block DEVELOPMENT/PRODUCTION and all unrelated CircleCI jobs/sites.
"""
import os
import re

BRANCH = "review/equipment-job-source-cost-report-2026-10-29"
JOB = "equipment-report30-isolated-integration"
SITE = "ci-equipment-report30.localhost"
SHA = re.compile(r"^[a-f0-9]{40}$")


def is_approved_site(site):
    env = os.environ
    actual = env.get("CIRCLE_SHA1", "")
    approved = env.get("CCPC_EQUIP30_APPROVED_SHA", "")
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
        and env.get("CCPC_ALLOW_EQUIP30_TESTS") == "isolated-only"
    )


def assert_approved_site(site):
    if not is_approved_site(site):
        raise RuntimeError(
            "PR30 Equipment30 integration requires exact-SHA approved "
            "CircleCI job/site; no DEVELOPMENT or PRODUCTION test permitted."
        )

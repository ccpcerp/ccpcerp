# Copyright (c) 2026, CCPC and contributors
"""Independent fail-closed authorization for disposable Finance lifecycle CI tests.

This is deliberately separate from the original CircleCI Withdrawal/GL guard.
It does not authorize persistent site access or any real Accounting decision.
The approved SHA must come from separately administered CI project settings;
a pipeline parameter or a PR label is not an authorization source.
"""
import os
import re

FINANCE_BRANCH = "feature/finance-mapping-lifecycle-2026-10-28"
FINANCE_JOB = "finance-lifecycle-isolated-integration"
FINANCE_SITE = "ci-finance-lifecycle.localhost"
_SHA = re.compile(r"^[0-9a-f]{40}$")


def is_reviewed_finance_lifecycle_disposable_site(site):
    env = os.environ
    source = env.get("CIRCLE_SHA1") or ""
    authorized = env.get("CCPC_FINANCE_APPROVED_SHA") or ""
    return (
        type(site) is str
        and site == FINANCE_SITE
        and env.get("CIRCLECI") == "true"
        and env.get("CIRCLE_PROJECT_USERNAME") == "CCPCerp_2026"
        and env.get("CIRCLE_PROJECT_REPONAME") == "ccpcerp"
        and env.get("CIRCLE_BRANCH") == FINANCE_BRANCH
        and env.get("CIRCLE_JOB") == FINANCE_JOB
        and all(env.get(key) for key in (
            "CIRCLE_PIPELINE_ID", "CIRCLE_WORKFLOW_ID", "CIRCLE_BUILD_NUM",
        ))
        and bool(_SHA.fullmatch(source))
        and bool(_SHA.fullmatch(authorized))
        and source == authorized
        and env.get("CCPC_ALLOW_FINANCE_LIFECYCLE_TESTS") == "isolated-only"
    )


def assert_finance_disposable_site(site):
    if not is_reviewed_finance_lifecycle_disposable_site(site):
        raise RuntimeError(
            "Finance integration is restricted to an explicitly authorized "
            "disposable CircleCI site and reviewed commit."
        )

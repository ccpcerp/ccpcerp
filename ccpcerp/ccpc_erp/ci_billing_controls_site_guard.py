# Copyright (c) 2026, CCPC and contributors
"""Independent, exact-SHA guard for one disposable Billing Controls CI acceptance.

This does not accept the older Finance CI authorization or expose any live site.
A project-level CircleCI variable provides the reviewed commit, independently of
manually selected pipeline parameters or PR labels.
"""
import os
import re

BILLING_BRANCH = "review/billing-authorization-correction-2026-11-03"
BILLING_JOB = "billing-controls-isolated-integration"
BILLING_SITE = "ci-billing-review.localhost"
_SHA = re.compile(r"^[0-9a-f]{40}$")


def is_reviewed_billing_controls_disposable_site(site):
    env = os.environ
    actual = env.get("CIRCLE_SHA1") or ""
    approved = env.get("CCPC_BILLING_APPROVED_SHA") or ""
    return (
        type(site) is str
        and site == BILLING_SITE
        and env.get("CIRCLECI") == "true"
        and env.get("CIRCLE_PROJECT_USERNAME") == "CCPCerp_2026"
        and env.get("CIRCLE_PROJECT_REPONAME") == "ccpcerp"
        and env.get("CIRCLE_BRANCH") == BILLING_BRANCH
        and env.get("CIRCLE_JOB") == BILLING_JOB
        and all(env.get(key) for key in (
            "CIRCLE_PIPELINE_ID", "CIRCLE_WORKFLOW_ID", "CIRCLE_BUILD_NUM",
        ))
        and bool(_SHA.fullmatch(actual))
        and bool(_SHA.fullmatch(approved))
        and actual == approved
        and env.get("CCPC_ALLOW_BILLING_CONTROLS_TESTS") == "isolated-only"
    )


def assert_billing_controls_disposable_site(site):
    if not is_reviewed_billing_controls_disposable_site(site):
        raise RuntimeError(
            "Billing controls test requires the approved CircleCI branch, "
            "exact commit and disposable site."
        )

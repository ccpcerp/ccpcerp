# Copyright (c) 2026, CCPC and contributors
"""Strictly identify approved disposable CI sites for destructive test-only probes.

This module is imported by tests and their spawned worker only. It is NOT a
production permission bypass and does not authorize posting in real sites.
"""
import os


_CIRCLECI_BRANCH = "review/circleci-isolated-erpnext-phase2-2026-10-26"
_WITHDRAWAL_GH_BRANCHES = frozenset({
    "review/withdrawal-cancellation-ledger-ci-2026-10-16",
    "review/withdrawal-issue-accounting-ci-2026-10-17",
    "review/withdrawal-beneficiary-equipment-integration-2026-10-18",
})
_INTERCOMPANY_GH_BRANCH = "review/intercompany-invoice-transaction-2026-10-20"


def is_reviewed_circleci_disposable_site(site):
    """Every condition is required; merely setting CI=true is insufficient."""
    env = os.environ
    return (
        site == "ci-circleci.localhost"
        and env.get("CIRCLECI") == "true"
        and env.get("CIRCLE_PROJECT_USERNAME") == "ccpcerp"
        and env.get("CIRCLE_PROJECT_REPONAME") == "ccpcerp"
        and env.get("CIRCLE_BRANCH") == _CIRCLECI_BRANCH
        and env.get("CIRCLE_JOB") == "isolated-erpnext-integration"
        and bool(env.get("CIRCLE_PIPELINE_ID"))
        and bool(env.get("CIRCLE_WORKFLOW_ID"))
        and bool(env.get("CIRCLE_BUILD_NUM"))
        and len(env.get("CIRCLE_SHA1", "")) == 40
        and env.get("CCPC_ALLOW_COMMIT_PROBE") == "isolated-only"
    )


def is_reviewed_withdrawal_disposable_site(site):
    env = os.environ
    old_approved_github_site = (
        site == "ci-withdrawal.localhost"
        and env.get("GITHUB_ACTIONS") == "true"
        and env.get("GITHUB_REPOSITORY") == "ccpcerp/ccpcerp"
        and env.get("GITHUB_HEAD_REF") in _WITHDRAWAL_GH_BRANCHES
        and env.get("CCPC_ALLOW_COMMIT_PROBE") == "isolated-only"
    )
    return old_approved_github_site or is_reviewed_circleci_disposable_site(site)


def is_reviewed_intercompany_disposable_site(site):
    env = os.environ
    old_approved_github_site = (
        site == "ci-intercompany.localhost"
        and env.get("GITHUB_ACTIONS") == "true"
        and env.get("GITHUB_REPOSITORY") == "ccpcerp/ccpcerp"
        and env.get("GITHUB_HEAD_REF") == _INTERCOMPANY_GH_BRANCH
    )
    return old_approved_github_site or is_reviewed_circleci_disposable_site(site)

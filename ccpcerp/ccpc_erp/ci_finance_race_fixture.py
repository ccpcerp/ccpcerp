# Copyright (c) 2026, CCPC and contributors
"""Explicitly authorized synthetic Finance race fixtures for a disposable CI site.

NOT a Frappe hook, an ordinary test fixture, an HTTP endpoint or a CircleCI job.
This builder commits synthetic data solely to make it visible to two independent
MariaDB connections; never call it from a rolled-back ERPNextTestSuite.
It uses the real proposal, signed Accounting decision and lifecycle services.
"""
import os
from pathlib import Path
import subprocess

import frappe

from ccpcerp.ccpc_erp.bc_finance_evidence import sign_mapping_decision
from ccpcerp.ccpc_erp.bc_finance_lifecycle_events import (
    get_retirement_request_review, request_mapping_retirement,
)
from ccpcerp.ccpc_erp.bc_mapping_workflow import (
    approve_mapping, get_mapping_review, propose_mapping,
)
from ccpcerp.ccpc_erp.ci_finance_lifecycle_site_guard import assert_finance_disposable_site
from ccpcerp.ccpc_erp.ci_finance_setup_connection import isolated_finance_setup_connection
from ccpcerp.ccpc_erp.ci_finance_master_bootstrap import verify_finance_master_records

COMPANY = "_Test Company"
ACCOUNT = "Stock Adjustment - _TC"
MAKER = "ccpc-finance-ci-maker@invalid.test"
CHECKER = "ccpc-finance-ci-checker@invalid.test"
GROUPS = {
    "Approve": "CCPC Finance CI Race Approve Fixture",
    "Retire": "CCPC Finance CI Race Retire Fixture",
}
REV = "CCPC Expense Mapping Revision"
DECISION_USE = "CCPC Mapping Decision Use"


def _preflight(action):
    # Must be the first operation, before any database read or role change.
    assert_finance_disposable_site(getattr(frappe.local, "site", None))
    if os.environ.get("CCPC_ALLOW_FINANCE_FIXTURE_PREP") != "isolated-only":
        raise RuntimeError("Disposable Finance fixture preparation is not authorized.")
    if os.environ.get("CCPC_ALLOW_FINANCE_FIXTURE_COMMIT") != "isolated-only":
        raise RuntimeError("Separate synthetic fixture COMMIT approval is missing.")
    if action not in GROUPS:
        raise RuntimeError("Only the exact Approve/Retire synthetic races are supported.")
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    ).strip()
    if sha != os.environ.get("CCPC_FINANCE_APPROVED_SHA"):
        raise RuntimeError("Fixture builder checkout differs from approved Finance SHA.")
    if frappe.conf.db_type != "mariadb":
        raise RuntimeError("Synthetic Finance fixtures require disposable MariaDB.")
    if not frappe.conf.get("allow_tests"):
        raise RuntimeError("Fixture builder requires test-only site configuration.")
    verify_finance_master_records()
    account = frappe.db.get_value(
        "Account", ACCOUNT, ["company", "root_type", "is_group", "disabled"],
        as_dict=True,
    )
    if not account or (account.company, account.root_type) != (COMPANY, "Expense"):
        raise RuntimeError("Canonical disposable Expense Account is unavailable.")
    if int(account.is_group) or int(account.disabled):
        raise RuntimeError("Fixture account is not an enabled leaf Expense Account.")
    if not frappe.db.exists("Item Group", "All Item Groups"):
        raise RuntimeError("Canonical Item Group tree is missing.")
    if frappe.db.exists("Item Group", GROUPS[action]):
        raise RuntimeError("Synthetic race fixture already exists; do not replay.")


def _actor(user_id, role):
    if not frappe.db.exists("Role", role):
        frappe.get_doc({"doctype": "Role", "role_name": role}).insert(
            ignore_permissions=True,
        )
    if not frappe.db.exists("User", user_id):
        frappe.get_doc({
            "doctype": "User", "email": user_id,
            "first_name": "Disposable Finance CI",
            "enabled": 1, "user_type": "System User",
            "send_welcome_email": 0,
        }).insert(ignore_permissions=True)
    user = frappe.get_doc("User", user_id)
    if int(user.enabled) != 1 or user.user_type != "System User":
        raise RuntimeError("Fixture actor must be an enabled synthetic System User.")
    if user_id != MAKER and user_id != CHECKER:
        raise RuntimeError("Unexpected synthetic actor.")
    if any(r.role == "Stockroom Personnel" for r in user.roles):
        raise RuntimeError("Finance fixture actor must not be a Stockroom Personnel.")
    if not any(r.role == role for r in user.roles):
        user.append("roles", {"role": role})
        user.save(ignore_permissions=True)
    if not frappe.db.exists("User Permission", {
        "user": user_id, "allow": "Company", "for_value": COMPANY,
        "apply_to_all_doctypes": 1,
    }):
        frappe.get_doc({
            "doctype": "User Permission", "user": user_id,
            "allow": "Company", "for_value": COMPANY,
            "apply_to_all_doctypes": 1,
        }).insert(ignore_permissions=True)


def prepare_finance_race_fixture(action):
    """Commit one independently approved synthetic race fixture, once only.

    Returns only synthetic identifiers; caller must use exactly those values
    with verify_finance_business_race() after a second authorization gate.
    No passwords or real-user credentials are provisioned by this helper.
    """
    # Establish a private DB session before any fixture reads or writes.
    # A caller with a pending transaction cannot have those rows committed.
    assert_finance_disposable_site(getattr(frappe.local, "site", None))
    with isolated_finance_setup_connection():
        _preflight(action)
        original_actor = frappe.session.user
        committed = False
        try:
            # The fixture builder owns a fresh disposable transaction. Never call
            # it with existing uncommitted business operations.
            if original_actor != "Administrator":
                raise RuntimeError("Fixture setup requires the isolated bench Administrator.")
            group = frappe.get_doc({
                "doctype": "Item Group", "item_group_name": GROUPS[action],
                "parent_item_group": "All Item Groups", "is_group": 0,
            }).insert(ignore_permissions=True)
            _actor(MAKER, "CCPC Mapping Preparer")
            _actor(CHECKER, "CCPC Accounting Mapping Approver")

            frappe.set_user(MAKER)
            draft = propose_mapping("Expense", COMPANY, group.name, ACCOUNT)
            if draft["state"] != "Draft":
                raise RuntimeError("Real Finance proposal did not produce Draft.")
            revision = draft["name"]
            frappe.set_user(CHECKER)
            reviewed = get_mapping_review("Expense", revision)
            if reviewed["owner"] != MAKER or reviewed["state"] != "Draft":
                raise RuntimeError("Independent synthetic review was not validated.")

            if action == "Approve":
                signed = sign_mapping_decision(
                    "Expense", revision, reviewed["modified"], "Approve",
                )
                target = revision
                expected_modified = reviewed["modified"]
            else:
                approval = sign_mapping_decision(
                    "Expense", revision, reviewed["modified"], "Approve",
                )
                approved = approve_mapping(
                    "Expense", revision, reviewed["modified"], approval["name"],
                )
                if approved["state"] != "Approved":
                    raise RuntimeError("Real Finance approval did not succeed.")
                frappe.set_user(MAKER)
                request = request_mapping_retirement(
                    "Expense", revision, "Synthetic Finance CI retirement race",
                )
                if request["state"] != "Pending":
                    raise RuntimeError("Synthetic retirement was not Pending.")
                target = request["name"]
                frappe.set_user(CHECKER)
                review = get_retirement_request_review("Expense", target)
                if review["state"] != "Pending" or review["prepared_by"] != MAKER:
                    raise RuntimeError("Retirement review is not independently authorized.")
                signed = sign_mapping_decision(
                    "Expense", revision,
                    str(frappe.db.get_value(REV, revision, "modified")),
                    "Retire", request_name=target,
                )
                expected_modified = review["modified"]

            if signed["state"] != "Signed":
                raise RuntimeError("Synthetic Accounting decision was not Signed.")
            decision_id = signed["name"]
            if frappe.db.exists(DECISION_USE, {"decision": decision_id}):
                raise RuntimeError("Race decision was consumed before competitor start.")
            if action == "Approve" and frappe.db.get_value(
                REV, revision, "approval_state"
            ) != "Draft":
                raise RuntimeError("Approval race fixture changed out of Draft.")
            if action == "Retire" and frappe.db.get_value(
                REV, revision, "approval_state"
            ) != "Approved":
                raise RuntimeError("Retirement race lost its Approved history.")
            frappe.db.commit()
            committed = True
            return {
                "action": action, "target": target,
                "expected_modified": expected_modified, "decision_id": decision_id,
            }
        except Exception:
            if not committed:
                frappe.db.rollback()
            raise
        finally:
            frappe.set_user(original_actor)

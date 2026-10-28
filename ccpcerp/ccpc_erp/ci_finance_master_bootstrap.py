# Copyright (c) 2026, CCPC and contributors
"""Opt-in bootstrap of canonical synthetic Finance test masters ONLY.

This code is never auto-run by CircleCI. It may create a test Company and its
ERPNext chart of accounts, which must be committed on a disposable site only.
No business, stock, invoice or ledger transactions are submitted here.
"""
import os
from pathlib import Path
import subprocess

import frappe

from ccpcerp.ccpc_erp.ci_finance_lifecycle_site_guard import assert_finance_disposable_site
from ccpcerp.ccpc_erp.ci_finance_setup_connection import isolated_finance_setup_connection

COMPANY = "_Test Company"
ACCOUNT = "Stock Adjustment - _TC"
ITEM_GROUP_ROOT = "All Item Groups"


def _gate():
    assert_finance_disposable_site(getattr(frappe.local, "site", None))
    if os.environ.get("CCPC_ALLOW_FINANCE_MASTER_BOOTSTRAP") != "isolated-only":
        raise RuntimeError("Synthetic master bootstrap approval is missing.")
    if os.environ.get("CCPC_ALLOW_FINANCE_MASTER_COMMIT") != "isolated-only":
        raise RuntimeError("Separate synthetic master COMMIT approval is missing.")
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    ).strip()
    if sha != os.environ.get("CCPC_FINANCE_APPROVED_SHA"):
        raise RuntimeError("Synthetic master bootstrap checkout SHA mismatch.")
    if frappe.conf.db_type != "mariadb" or not frappe.conf.get("allow_tests"):
        raise RuntimeError("Disposable MariaDB with allow_tests is required.")
    if frappe.session.user != "Administrator":
        raise RuntimeError("Bootstrap requires isolated bench Administrator.")


def verify_finance_master_records():
    """Read-only readiness checks. No fixture creation or commit."""
    assert_finance_disposable_site(getattr(frappe.local, "site", None))
    company = frappe.db.get_value(
        "Company", COMPANY, ["abbr", "default_currency", "country"],
        as_dict=True,
    )
    if not company or (
        company.abbr, company.default_currency, company.country
    ) != ("_TC", "INR", "India"):
        raise RuntimeError("Expected canonical fictional Company is missing or altered.")
    account = frappe.db.get_value(
        "Account", ACCOUNT, ["company", "root_type", "is_group", "disabled"],
        as_dict=True,
    )
    if not account or (account.company, account.root_type) != (COMPANY, "Expense"):
        raise RuntimeError("Canonical fictional Expense Account is missing.")
    if int(account.is_group) != 0 or int(account.disabled) != 0:
        raise RuntimeError("Fictional Expense Account must be an enabled leaf.")
    if not frappe.db.exists("Item Group", ITEM_GROUP_ROOT):
        raise RuntimeError("Canonical ERPNext Item Group root is missing.")


def bootstrap_finance_test_masters():
    """Create exactly one canonical synthetic Company in its own DB connection.

    Never load all ERPNext test_records, submit Stock Entries, or reuse an
    existing Company; any unexpected master shape or missing generated
    account makes this fail closed. Runtime approval and disposal are separate.
    """
    _gate()
    with isolated_finance_setup_connection():
        if frappe.db.exists("Company", COMPANY):
            raise RuntimeError("Master bootstrap is not replayable.")
        if frappe.db.exists("Account", ACCOUNT):
            raise RuntimeError("Unowned synthetic account already exists.")
        if not frappe.db.exists("Item Group", ITEM_GROUP_ROOT):
            raise RuntimeError("ERPNext Item Group root must be installed first.")

        # Exact canonical values from pinned ERPNext Company test_records.json,
        # minus optional linked test-only holiday list and other dependencies.
        # Company controller is responsible for the Standard chart of accounts.
        frappe.get_doc({
            "doctype": "Company",
            "company_name": COMPANY,
            "abbr": "_TC",
            "country": "India",
            "default_currency": "INR",
            "domain": "Manufacturing",
            "chart_of_accounts": "Standard",
            "enable_perpetual_inventory": 0,
        }).insert(ignore_permissions=True)
        verify_finance_master_records()
        # Commits only this newly opened connection; parent bench transaction,
        # if dirty, remains isolated and is NEVER committed by this module.
        frappe.db.commit()
        return {"company": COMPANY, "account": ACCOUNT}

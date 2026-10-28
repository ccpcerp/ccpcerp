# Copyright (c) 2026, CCPC and contributors
"""Manual, disposable-only, two-connection MariaDB Company-lock probe.

Run only via an explicitly approved bench execute in the isolated Finance
CircleCI site. This proves real InnoDB row-lock contention, NOT simultaneous
business-method approval idempotency or stock/GL correctness. No commits,
new fixtures, or financial operations are performed.
"""
import os
import subprocess
from pathlib import Path

import frappe

from ccpcerp.ccpc_erp.ci_finance_lifecycle_site_guard import (
    assert_finance_disposable_site,
)

SITE = "ci-finance-lifecycle.localhost"
COMPANY = "_Test Company"


def verify_finance_company_lock_contention():
    # This guard must execute before querying any live database row.
    assert_finance_disposable_site(getattr(frappe.local, "site", None))
    if os.environ.get("CCPC_ALLOW_FINANCE_LOCK_PROBE") != "isolated-only":
        raise RuntimeError("Separate disposable lock probe approval is required.")
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
    ).strip()
    if sha != os.environ.get("CCPC_FINANCE_APPROVED_SHA"):
        raise RuntimeError("Finance lock probe checkout SHA is not approved.")
    if frappe.conf.db_type != "mariadb":
        raise RuntimeError("Finance lock test requires isolated MariaDB.")

    primary = frappe.db
    second = None
    # Only an existing, fully committed fictional Company may be locked.
    # No test-user roles, mapping revisions or GL rows are created.
    try:
        primary.sql("SET SESSION innodb_lock_wait_timeout = 2")
        name = primary.sql(
            "SELECT name FROM tabCompany WHERE name=%s", (COMPANY,),
            as_dict=True,
        )
        if len(name) != 1:
            raise RuntimeError("Missing exact fictional Finance CI Company.")

        # Frappe's pinned v16 connect() opens a distinct DB connection without
        # switching the site when called with no site argument.
        frappe.connect(set_admin_as_user=False)
        second = frappe.db
        if second is primary:
            raise RuntimeError("A second independent connection was not opened.")
        visible = second.sql(
            "SELECT name FROM tabCompany WHERE name=%s", (COMPANY,),
            as_dict=True,
        )
        if len(visible) != 1:
            raise RuntimeError("Company fixture is not committed for second connection.")

        frappe.local.db = primary
        locked = primary.sql(
            "SELECT name FROM tabCompany WHERE name=%s FOR UPDATE",
            (COMPANY,), as_dict=True,
        )
        if len(locked) != 1:
            raise RuntimeError("Primary Company locking read failed.")

        frappe.local.db = second
        # NOWAIT should fail with InnoDB lock error 1205 (MariaDB) or 3572
        # on compatible MySQL. Other failures are not interpreted as PASS.
        try:
            second.sql(
                "SELECT name FROM tabCompany WHERE name=%s FOR UPDATE NOWAIT",
                (COMPANY,),
            )
        except Exception as error:
            number = error.args[0] if error.args else None
            if number not in (1205, 3572):
                raise
        else:
            raise RuntimeError("Second transaction acquired an already locked Company.")
        print("Finance disposable MariaDB two-connection Company lock: PASS")
        print("No approval race or financial posting was performed.")
    finally:
        if second is not None:
            frappe.local.db = second
            try:
                second.rollback()
            finally:
                second.close()
        frappe.local.db = primary
        # The whole probe is read-only but rollback releases row locks.
        primary.rollback()

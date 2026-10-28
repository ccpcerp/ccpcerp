# Copyright (c) 2026, CCPC and contributors
"""Dedicated database connection for manually approved disposable Finance setup.

A new Frappe connection isolates a fixture commit from any pending transaction
on the calling bench connection. This is not a substitute for disposable-site,
host isolation, authorization, or checking framework-side effects.
"""
from contextlib import contextmanager

import frappe

from ccpcerp.ccpc_erp.ci_finance_lifecycle_site_guard import (
    assert_finance_disposable_site,
)


@contextmanager
def isolated_finance_setup_connection():
    # Validate immutable CircleCI/site identity *before* opening a connection.
    assert_finance_disposable_site(getattr(frappe.local, "site", None))
    original_db = frappe.db
    original_site = frappe.local.site
    original_user = frappe.session.user
    isolated_db = None
    try:
        frappe.connect(set_admin_as_user=False)
        isolated_db = frappe.db
        if isolated_db is original_db:
            # Never rollback/close the caller if a mocked or broken connect()
            # unexpectedly returns the very same connection.
            isolated_db = None
            raise RuntimeError("Fixture setup did not obtain an independent DB connection.")
        if frappe.local.site != original_site:
            raise RuntimeError("Fixture setup switched to an unexpected site.")
        if isolated_db.sql("SELECT DATABASE()")[0][0] != frappe.conf.db_name:
            raise RuntimeError("Fixture connection is not using the expected database.")
        yield isolated_db
    finally:
        if isolated_db is not None:
            frappe.local.db = isolated_db
            try:
                isolated_db.rollback()
            finally:
                try:
                    isolated_db.close()
                finally:
                    frappe.local.db = original_db
        else:
            frappe.local.db = original_db
        # Set user while original DB is active and clear any cached role state.
        frappe.set_user(original_user)

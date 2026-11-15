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
    original_flags = frappe.local.flags
    if original_flags.get("read_only"):
        raise RuntimeError("Finance fixture setup refuses a read-only caller.")
    isolated_db = None
    try:
        # A DB connection alone does not isolate Frappe's thread-local flags.
        # Use a fresh Frappe-init-shaped flag bag rather than copying the
        # caller's possibly permissive ignore_* flags into fixture creation.
        # This also isolates nested values such as currently_saving.
        frappe.local.flags = frappe._dict({
            "currently_saving": [],
            "redirect_location": "",
            "in_install_db": False,
            "in_install_app": False,
            "in_import": False,
            "in_test": frappe.in_test,
            "mute_messages": False,
            "ignore_links": False,
            "mute_emails": False,
            "has_dataurl": False,
            "new_site": False,
            "read_only": False,
        })
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
        try:
            if isolated_db is not None:
                frappe.local.db = isolated_db
                try:
                    isolated_db.rollback()
                finally:
                    isolated_db.close()
        finally:
            # Restore caller context even if rollback/close or set_user fails.
            frappe.local.db = original_db
            try:
                # Set user while original DB is active; clear cached role state.
                frappe.set_user(original_user)
            finally:
                frappe.local.flags = original_flags

# Copyright (c) 2026, CCPC and contributors
# See license.txt
"""Lightweight CI-only spawned worker; no ERPNext test-data imports.

Never invoke this helper outside the explicitly allowed disposable CI site.
"""

import os

import frappe


def _ci_concurrent_issue_worker(slot, site, sites_path, slip_name, issuer, result_queue):
    """In a spawned process: separate Frappe site, MariaDB connection and commit.

    A test-only wrapper instruments entry to the ORIGINAL row-lock method.
    It neither bypasses the lock nor mocks Stock Entry / stock / GL posting.
    """
    try:
        if not (
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "ccpcerp/ccpcerp"
            and os.environ.get("GITHUB_HEAD_REF")
            in {
                "review/withdrawal-cancellation-ledger-ci-2026-10-16",
                "review/withdrawal-issue-accounting-ci-2026-10-17",
            }
            and os.environ.get("CCPC_ALLOW_COMMIT_PROBE") == "isolated-only"
            and os.environ.get("CCPC_ALLOW_CONCURRENCY_PROBE") == "isolated-only"
            and site == "ci-withdrawal.localhost"
        ):
            raise RuntimeError("Concurrent worker may only run on the allowed disposable Withdrawal CI site")

        frappe.init(site=site, sites_path=sites_path, force=True)
        if getattr(frappe.local, "site", None) != "ci-withdrawal.localhost":
            raise RuntimeError("Unexpected initialized Frappe site")
        frappe.connect()
        frappe.set_user(issuer)
        # Deadlock/long-wait fail-closed; do not allow an indefinite CI hang.
        frappe.db.sql("SET SESSION innodb_lock_wait_timeout=15")
        db_connection_id = int(frappe.db.sql("SELECT CONNECTION_ID()")[0][0])

        from ccpcerp.ccpc_erp import withdrawal_slip_stock as issue_module

        original_lock = issue_module._lock_withdrawal_slip
        reported_initial_attempt = False

        def report_actual_lock_attempt(locked_slip):
            nonlocal reported_initial_attempt
            if locked_slip != slip_name:
                raise AssertionError("Worker attempted to lock the wrong Withdrawal Slip")
            # issue_withdrawal_slip locks once directly and may lock AGAIN via
            # Stock Entry.before_submit. Instrument the FIRST attempt only;
            # every real production lock invocation still calls original_lock.
            if not reported_initial_attempt:
                reported_initial_attempt = True
                result_queue.put(("attempt", slot, db_connection_id))
            return original_lock(locked_slip)

        issue_module._lock_withdrawal_slip = report_actual_lock_attempt
        try:
            result = issue_module.issue_withdrawal_slip(slip_name)
            if result.get("submitted") != 1:
                raise AssertionError("Issuer did not return submitted=1")
            frappe.db.commit()
            result_queue.put(("success", slot, result["name"]))
        except frappe.ValidationError as exc:
            frappe.db.rollback()
            result_queue.put(("rejected", slot, str(exc)[:500]))
        finally:
            issue_module._lock_withdrawal_slip = original_lock
    except Exception as exc:
        if getattr(frappe.local, "db", None):
            frappe.db.rollback()
        result_queue.put(("error", slot, type(exc).__name__ + ": " + str(exc)[:500]))
    finally:
        if getattr(frappe.local, "initialised", False):
            frappe.destroy()


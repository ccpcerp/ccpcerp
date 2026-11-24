# Copyright (c) 2026, CCPC and contributors
"""Lightweight, separately spawned Phase1 integrated disposable-site concurrent SQL worker.

Never import ERPNext's heavyweight unittest bootstrap before frappe.init().
This module is imported by multiprocessing.spawn before a site is connected,
and contains NO application permissions bypass or persistent environment path.
"""
import os
import re
from contextlib import ExitStack
from unittest.mock import patch

import frappe


def _phase1_integrated_concurrent_worker(slot, site, sites_path, slip_name, beneficiary, result_queue):
    """Compete on real row locks via independent MariaDB sessions."""
    try:
        env = os.environ
        if not all((
            site == "ci-phase1-integrated.localhost",
            env.get("GITHUB_ACTIONS") == "true",
            env.get("GITHUB_REPOSITORY") == "ccpcerp/ccpcerp",
            env.get("GITHUB_EVENT_NAME") == "pull_request",
            bool(re.fullmatch(r"refs/pull/[1-9][0-9]*/merge", env.get("GITHUB_REF", ""))),
            env.get("GITHUB_HEAD_REF") == "review/phase1-maintenance-integrated-tests-2026-11-24",
            env.get("GITHUB_BASE_REF") == "review/phase1-maintenance-release-integration-2026-11-24",
            env.get("GITHUB_WORKFLOW") == "CCPC Phase1 - manual integrated Bearings MariaDB acceptance",
            bool(env.get("GITHUB_RUN_ID")),
            len(env.get("CCPC_PHASE1_TEST_SHA", "")) == 40,
            env.get("CCPC_ALLOW_MAINTENANCE_DB_PROBE") == "phase1-integrated-only",
        )):
            raise RuntimeError("Phase1 integrated concurrent worker restricted to exact disposable GitHub CI")
        frappe.init(site=site, sites_path=sites_path, force=True)
        if not all((
            getattr(frappe.local, "site", None) == site,
            frappe.conf.get("db_host") == "127.0.0.1",
            frappe.conf.get("db_type", "mariadb") == "mariadb",
        )):
            raise RuntimeError("Phase1 integrated worker site/database endpoint mismatch")
        frappe.connect()
        frappe.set_user("ci-stockroom-integration@invalid.test")
        frappe.db.sql("SET SESSION innodb_lock_wait_timeout=30")
        connection = int(frappe.db.sql("SELECT CONNECTION_ID()")[0][0])

        from ccpcerp.ccpc_erp import withdrawal_slip_stock as stock
        original_lock = stock._lock_withdrawal_slip
        first_attempt = False

        def reporting_lock(name):
            nonlocal first_attempt
            if name != slip_name:
                raise AssertionError("Wrong Withdrawal Slip passed to original row lock")
            if not first_attempt:
                first_attempt = True
                result_queue.put(("attempt", slot, connection))
            return original_lock(name)

        with ExitStack() as stack:
            # Translate pilot labels only inside this isolated child process,
            # not in application code or any persistent site records.
            for symbol, value in (
                ("ccpcerp.ccpc_erp.phase1_maintenance_use.PILOT_COMPANY", "_Test Company"),
                ("ccpcerp.ccpc_erp.phase1_maintenance_use.PILOT_ITEM_GROUP", "_Test Item Group"),
                ("ccpcerp.ccpc_erp.phase1_maintenance_use.PILOT_BENEFICIARY", beneficiary),
                ("ccpcerp.ccpc_erp.withdrawal_slip_stock.PILOT_COMPANY", "_Test Company"),
                ("ccpcerp.ccpc_erp.withdrawal_slip_stock.PILOT_ITEM_GROUP", "_Test Item Group"),
            ):
                stack.enter_context(patch(symbol, value))
            stack.enter_context(patch.object(stock, "_lock_withdrawal_slip", reporting_lock))
            try:
                result = stock.issue_withdrawal_slip(slip_name)
                if result.get("submitted") != 1:
                    raise AssertionError("No submitted Stock Entry returned")
                frappe.db.commit()  # Only ephemeral synthetic Github MariaDB.
                result_queue.put(("success", slot, result["name"]))
            except frappe.ValidationError as exc:
                frappe.db.rollback()
                result_queue.put(("rejected", slot, str(exc)[:300]))
    except Exception as exc:
        if getattr(frappe.local, "db", None):
            frappe.db.rollback()
        result_queue.put(("error", slot, type(exc).__name__ + ": " + str(exc)[:300]))
    finally:
        if getattr(frappe.local, "initialised", False):
            frappe.destroy()

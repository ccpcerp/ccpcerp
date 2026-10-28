"""Pure-stdlib mocked behavioral checks of Finance isolated DB setup.

This module does not import real Frappe, connect to a database, access HTTP,
start CircleCI, or execute the actual fixture builder.
"""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = (Path(__file__).resolve().parents[1] / "ccpcerp" / "ccpc_erp"
          / "ci_finance_setup_connection.py")


class FakeDB:
    def __init__(self, db_name="ci_finance_disposable"):
        self.name = db_name
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def sql(self, sql):
        if sql != "SELECT DATABASE()":
            raise AssertionError("Unexpected SQL in connection helper")
        return [[self.name]]

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class FrappeStub(types.ModuleType):
    def __getattribute__(self, name):
        if name == "db":
            return self.local.db
        return super().__getattribute__(name)


class SetupConnectionBehaviorTests(unittest.TestCase):
    def fake_environment(self, db_name="ci_finance_disposable", reuse=False,
                         connect_failure=False):
        original = FakeDB()
        child = FakeDB(db_name)
        frappe = FrappeStub("frappe")
        frappe.local = types.SimpleNamespace(
            db=original, site="ci-finance-lifecycle.localhost",
        )
        frappe.conf = types.SimpleNamespace(db_name="ci_finance_disposable")
        frappe.session = types.SimpleNamespace(user="Administrator")
        calls = []

        def connect(set_admin_as_user=True):
            self.assertFalse(set_admin_as_user)
            calls.append("connect")
            if connect_failure:
                raise RuntimeError("Synthetic connection failure")
            frappe.local.db = original if reuse else child

        def set_user(user):
            calls.append(("restore", user))
            frappe.session.user = user

        frappe.connect = connect
        frappe.set_user = set_user
        guard = types.ModuleType(
            "ccpcerp.ccpc_erp.ci_finance_lifecycle_site_guard",
        )
        guard.assert_finance_disposable_site = lambda site: (
            self.assertEqual(site, "ci-finance-lifecycle.localhost")
        )
        modules = {
            "frappe": frappe,
            "ccpcerp": types.ModuleType("ccpcerp"),
            "ccpcerp.ccpc_erp": types.ModuleType("ccpcerp.ccpc_erp"),
            "ccpcerp.ccpc_erp.ci_finance_lifecycle_site_guard": guard,
        }
        return frappe, original, child, calls, modules

    def load_context(self, modules):
        namespace = {"__name__": "_ccpc_offline_setup_test"}
        with patch.dict(sys.modules, modules):
            exec(compile(SOURCE.read_text(encoding="utf-8"),
                         str(SOURCE), "exec"), namespace)
        return namespace["isolated_finance_setup_connection"]

    def test_fixture_commit_cannot_commit_parent_connection(self):
        frappe, original, child, calls, modules = self.fake_environment()
        isolated = self.load_context(modules)
        with isolated() as db:
            self.assertIs(db, child)
            self.assertIs(frappe.db, child)
            db.commit()
        self.assertIs(frappe.db, original)
        self.assertEqual(original.commits, 0)
        self.assertEqual(original.rollbacks, 0)
        self.assertEqual(original.closes, 0)
        self.assertEqual(child.commits, 1)
        self.assertEqual(child.rollbacks, 1)
        self.assertEqual(child.closes, 1)
        self.assertIn(("restore", "Administrator"), calls)

    def test_failure_rolls_back_only_child(self):
        frappe, original, child, _, modules = self.fake_environment()
        isolated = self.load_context(modules)
        with self.assertRaisesRegex(RuntimeError, "synthetic worker failure"):
            with isolated():
                raise RuntimeError("synthetic worker failure")
        self.assertIs(frappe.db, original)
        self.assertEqual(original.rollbacks, 0)
        self.assertEqual(original.closes, 0)
        self.assertEqual(child.rollbacks, 1)
        self.assertEqual(child.closes, 1)

    def test_wrong_database_fails_closed_and_restores_parent(self):
        frappe, original, child, _, modules = self.fake_environment(
            db_name="NOT_THE_DISPOSABLE_DATABASE",
        )
        isolated = self.load_context(modules)
        with self.assertRaisesRegex(RuntimeError, "not using the expected database"):
            with isolated():
                self.fail("Wrong DB must never enter the fixture body")
        self.assertIs(frappe.db, original)
        self.assertEqual(original.rollbacks, 0)
        self.assertEqual(original.closes, 0)
        self.assertEqual(child.rollbacks, 1)
        self.assertEqual(child.closes, 1)

    def test_same_connection_is_never_rolled_back_or_closed(self):
        frappe, original, _, _, modules = self.fake_environment(reuse=True)
        isolated = self.load_context(modules)
        with self.assertRaisesRegex(RuntimeError, "independent DB connection"):
            with isolated():
                self.fail("Same DB must never enter the fixture body")
        self.assertIs(frappe.db, original)
        self.assertEqual(original.rollbacks, 0)
        self.assertEqual(original.closes, 0)

    def test_failed_connect_restores_parent(self):
        frappe, original, _, _, modules = self.fake_environment(
            connect_failure=True,
        )
        isolated = self.load_context(modules)
        with self.assertRaisesRegex(RuntimeError, "Synthetic connection failure"):
            with isolated():
                self.fail("Failed connect must not enter fixture body")
        self.assertIs(frappe.db, original)
        self.assertEqual(original.rollbacks, 0)
        self.assertEqual(original.closes, 0)


if __name__ == "__main__":
    unittest.main()

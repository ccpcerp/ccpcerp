"""Frappe-free, fail-closed regression for nine protected Finance DocTypes.

The real Frappe v16 install / migrate + Stockroom->Finance Controller SQL test
is a SEPARATE disposable GitHub Actions gate. Tests here never import Frappe,
write a DB, or touch ERPNext permissions.
"""
import ast
from pathlib import Path
import types
import unittest


ROOT = Path(__file__).resolve().parent
SECURITY = ROOT / "finance_doctype_security.py"
SETUP = ROOT.parent / "setup.py"
HOOKS = ROOT.parent / "hooks.py"


def security_namespace(fake_frappe):
    tree = ast.parse(SECURITY.read_text())
    accepted = ("PROTECTED_FINANCE_DOCTYPES", "DEFAULT_RIGHTS",
                "OTHER_RIGHTS", "SCAN_COLUMNS")
    nodes = []
    for x in tree.body:
        if isinstance(x, (ast.Assign, ast.FunctionDef)):
            if isinstance(x, ast.FunctionDef) or any(
                isinstance(y, ast.Name) and y.id in accepted for y in x.targets
            ):
                nodes.append(x)
    assert len(nodes) == 8, "Unexpected Finance security policy AST"
    ns = {"frappe": fake_frappe}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=nodes, type_ignores=[])), str(SECURITY), "exec"), ns)
    return ns


class FakeFinanceDB:
    def __init__(self, doctypes):
        self.doctypes = set(doctypes)
        self.perms = {name: [] for name in doctypes}
        self.custom = {name: [] for name in doctypes}
        self.deletions = []

    def exists(self, kind, query):
        if kind == "DocType":
            return query in self.doctypes
        if kind == "Custom DocPerm":
            return bool(self.custom[query["parent"]])
        raise AssertionError((kind, query))

    def sql(self, query, args, as_dict=True):
        assert as_dict and len(args) == 1
        assert "FOR UPDATE" in query or "LIMIT 1" in query
        name, = args
        if "`tabDocPerm`" in query:
            return [dict(row) for row in self.perms[name]]
        if "`tabCustom DocPerm`" in query:
            return [dict(row) for row in self.custom[name]]
        raise AssertionError(query)

    def delete(self, kind, conditions):
        assert kind == "DocPerm"
        assert conditions["role"] == "System Manager"
        name = conditions["parent"]
        self.deletions.append((kind, dict(conditions)))
        self.perms[name] = [
            row for row in self.perms[name]
            if row["name"] != conditions["name"]
        ]


class SecurityStaticTests(unittest.TestCase):
    def setUp(self):
        expected = ("CCPC Expense Mapping Scope", "CCPC Expense Mapping Revision",
                    "CCPC Center Mapping Scope", "CCPC Center Mapping Revision",
                    "CCPC Accounting Mapping Decision", "CCPC Mapping Decision Use",
                    "CCPC Mapping Rejection Event", "CCPC Mapping Retirement Request",
                    "CCPC Mapping Retirement Event")
        self.db = FakeFinanceDB(expected)
        self.flags = types.SimpleNamespace(in_install=False, in_migrate=True)
        self.cleared = []
        self.frappe = types.SimpleNamespace(db=self.db, flags=self.flags,
                                            clear_cache=lambda **kw: self.cleared.append(kw))
        self.ns = security_namespace(self.frappe)
        self.assertEqual(self.ns["PROTECTED_FINANCE_DOCTYPES"], expected)

    def default(self, doctype, identifier):
        row = {
            "name": identifier,
            "parent": doctype,
            "parenttype": "DocType",
            "parentfield": "permissions",
            "role": "System Manager", "permlevel": 0, "if_owner": 0,
        }
        row.update({key: 1 for key in self.ns["DEFAULT_RIGHTS"]})
        row.update({key: 0 for key in self.ns["OTHER_RIGHTS"]})
        return row

    def load_defaults(self):
        for i, name in enumerate(self.ns["PROTECTED_FINANCE_DOCTYPES"]):
            self.db.perms[name].append(self.default(name, f"auto-{i}"))

    def clean(self):
        self.ns["harden_finance_mapping_doctype_permissions"]()

    def test_fresh_install_removes_only_all_nine_known_framework_defaults(self):
        self.flags.in_install, self.flags.in_migrate = "ccpcerp", False
        self.load_defaults()
        self.clean()
        self.assertEqual(len(self.db.deletions), 9)
        self.assertEqual(len(self.cleared), 9)
        self.assertTrue(all(not a for a in self.db.perms.values()))

    def test_migrate_idempotent_after_clean_install(self):
        self.load_defaults()
        self.clean()
        self.clean()
        self.assertEqual(len(self.db.deletions), 9)

    def test_no_lifecycle_context_refuses_even_valid_default(self):
        self.load_defaults()
        self.flags.in_migrate = False
        with self.assertRaises(RuntimeError):
            self.clean()
        self.assertEqual(self.db.deletions, [])

    def test_unexpected_role_fails_before_any_deletion(self):
        self.load_defaults()
        last = list(self.db.perms)[-1]
        self.db.perms[last][0]["role"] = "Finance Controller"
        with self.assertRaisesRegex(RuntimeError, "non-default permissions"):
            self.clean()
        self.assertEqual(self.db.deletions, [])

    def test_modified_default_rights_fails_closed(self):
        self.load_defaults()
        last = list(self.db.perms)[-1]
        self.db.perms[last][0]["export"] = 0
        with self.assertRaisesRegex(RuntimeError, "non-default permissions"):
            self.clean()
        self.assertEqual(self.db.deletions, [])

    def test_custom_role_grant_fails_without_deleting_source_row(self):
        self.load_defaults()
        last = list(self.db.perms)[-1]
        self.db.custom[last].append({"name": "manually-customized"})
        with self.assertRaisesRegex(RuntimeError, "customized permissions"):
            self.clean()
        self.assertEqual(self.db.deletions, [])

    def test_missing_protected_doctype_fails_closed(self):
        self.db.doctypes.remove("CCPC Mapping Retirement Event")
        with self.assertRaisesRegex(RuntimeError, "Missing protected"):
            self.clean()

    def test_document_and_query_hooks_block_all_protected_names(self):
        for doctype in self.ns["PROTECTED_FINANCE_DOCTYPES"]:
            with self.subTest(doctype=doctype):
                doc = types.SimpleNamespace(doctype=doctype)
                self.assertIs(self.ns["deny_protected_finance_document_access"](
                    doc, "read", "system-manager@example.invalid"), False)
                self.assertEqual(self.ns["deny_protected_finance_list_access"](
                    user="system-manager@example.invalid", doctype=doctype), "1=0")
        self.assertIsNone(self.ns["deny_protected_finance_document_access"](
            types.SimpleNamespace(doctype="MRS")))
        self.assertIsNone(self.ns["deny_protected_finance_list_access"](doctype="MRS"))

    def test_hooks_registered_and_install_migrate_hardening_first(self):
        h = HOOKS.read_text()
        setup = SETUP.read_text()
        self.assertIn('after_install = "ccpcerp.ccpc_erp.finance_doctype_security.', h)
        self.assertIn('"CCPC Accounting Mapping Decision"', h)
        self.assertIn('finance_doctype_security.deny_protected_finance_document_access', h)
        self.assertIn('finance_doctype_security.deny_protected_finance_list_access', h)
        self.assertIn('harden_finance_mapping_doctype_permissions()', setup)
        self.assertLess(setup.index('harden_finance_mapping_doctype_permissions()'),
                        setup.index('ensure_withdrawal_section_foundation()', setup.index('def after_migrate():')))

    def test_only_protected_docs_no_other_business_or_accounting_changes(self):
        source = SECURITY.read_text()
        for banned in ('frappe.db.commit(', 'frappe.set_user(', 'Stock Entry',
                       'GL Entry', 'Stock Ledger Entry', 'frappe.delete_doc(',
                       'delete("User"', 'delete("Role"'):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()

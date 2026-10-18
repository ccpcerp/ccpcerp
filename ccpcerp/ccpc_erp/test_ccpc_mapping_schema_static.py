"""Static-only gates for inert B/C DocType draft schema (no Frappe/site needed)."""
import ast
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent
SCHEMAS = {
    "ccpc_expense_mapping_scope": ("CCPC Expense Mapping Scope", "scope_key", "item_group"),
    "ccpc_expense_mapping_revision": ("CCPC Expense Mapping Revision", "revision_key", "expense_account"),
    "ccpc_center_mapping_scope": ("CCPC Center Mapping Scope", "scope_key", "beneficiary_department"),
    "ccpc_center_mapping_revision": ("CCPC Center Mapping Revision", "revision_key", "cost_center"),
}


class MappingSchemaStaticTests(unittest.TestCase):
    def test_schema_is_inert_and_unique(self):
        for slug, (name, key, important) in SCHEMAS.items():
            with self.subTest(doctype=name):
                doc = json.loads((ROOT / "doctype" / slug / (slug + ".json")).read_text())
                self.assertEqual(doc["name"], name)
                self.assertEqual(doc["module"], "CCPC ERP")
                self.assertEqual(doc["permissions"], [], "No Accounting or Stockroom grants before review")
                self.assertEqual(doc["autoname"], "hash")
                self.assertTrue(doc["track_changes"])
                fields = {field["fieldname"]: field for field in doc["fields"]}
                self.assertEqual(list(fields), doc["field_order"])
                self.assertEqual(fields[key]["unique"], 1)
                self.assertEqual(fields[key]["read_only"], 1)
                self.assertEqual(fields[key]["reqd"], 1)
                self.assertIn(important, fields)
                self.assertEqual(fields["company"]["options"], "Company") if "scope" in slug else None

    def test_all_controllers_are_syntactically_valid_and_protect_mutations(self):
        for slug, (name, _key, _important) in SCHEMAS.items():
            with self.subTest(doctype=name):
                controller = ROOT / "doctype" / slug / (slug + ".py")
                source = controller.read_text()
                tree = ast.parse(source, filename=str(controller))
                classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
                self.assertEqual(len(classes), 1)
                methods = {node.name for node in classes[0].body if isinstance(node, ast.FunctionDef)}
                self.assertTrue({"before_validate", "validate", "before_rename", "on_trash"} <= methods)
                self.assertIn("mapping_key(", source)
                self.assertIn("get_doc_before_save", source)
                self.assertIn("frappe.throw(", source)
                if "revision" in slug:
                    self.assertIn('!= "Draft"', source)
                    self.assertIn("approved_by", source)
                    self.assertIn("approved_at", source)

    def test_mapping_ids_are_deterministic_and_unambiguous(self):
        spec = importlib.util.spec_from_file_location("mapping_scope_keys", ROOT / "mapping_scope_keys.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        key = module.mapping_key
        self.assertEqual(key("expense", "X", "Y"), key("expense", "X", "Y"))
        self.assertNotEqual(key("expense", "X|Y", "Z"), key("expense", "X", "Y|Z"))
        self.assertNotEqual(key("expense", "X", "Y"), key("center", "X", "Y"))
        self.assertEqual(len(key("expense", "Company", "Category")), 64)
        with self.assertRaises(ValueError):
            key("expense", "", "group")


if __name__ == "__main__":
    unittest.main()

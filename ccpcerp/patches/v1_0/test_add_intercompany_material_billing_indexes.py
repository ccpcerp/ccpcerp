from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import frappe

from ccpcerp.patches.v1_0 import add_intercompany_material_billing_indexes as index_patch


def _rows(index_name, columns, *, non_unique=1, index_type="BTREE", sub_part=None):
	return [
		frappe._dict(
			index_name=index_name,
			non_unique=non_unique,
			index_type=index_type,
			seq_in_index=position,
			column_name=column,
			sub_part=sub_part,
		)
		for position, column in enumerate(columns, start=1)
	]


def _raise_validation_error(message, exc=frappe.ValidationError):
	raise exc(message)


class TestAddIntercompanyMaterialBillingIndexes(TestCase):
	def setUp(self):
		original_db = index_patch.frappe.__dict__["db"]
		self.db = MagicMock()
		index_patch.frappe.__dict__["db"] = self.db
		self.addCleanup(index_patch.frappe.__dict__.__setitem__, "db", original_db)
		self.enterContext(patch.object(index_patch.frappe, "throw", side_effect=_raise_validation_error))
		self.enterContext(patch.object(index_patch, "_", side_effect=lambda message: message))

	def test_creates_and_verifies_both_indexes(self):
		scope = index_patch.INDEX_SPECS[0]
		child = index_patch.INDEX_SPECS[1]
		self.db.sql.side_effect = [
			[],
			_rows(scope["index_name"], scope["columns"]),
			[],
			_rows(child["index_name"], child["columns"]),
		]

		index_patch.execute()

		self.assertEqual(
			self.db.add_index.call_args_list,
			[
				call(scope["doctype"], list(scope["columns"]), index_name=scope["index_name"]),
				call(child["doctype"], list(child["columns"]), index_name=child["index_name"]),
			],
		)

	def test_existing_correct_indexes_are_idempotent(self):
		self.db.sql.side_effect = [
			_rows(spec["index_name"], spec["columns"]) for spec in index_patch.INDEX_SPECS
		]

		index_patch.execute()

		self.db.add_index.assert_not_called()

	def test_partial_completion_creates_only_missing_index(self):
		scope = index_patch.INDEX_SPECS[0]
		child = index_patch.INDEX_SPECS[1]
		self.db.sql.side_effect = [
			_rows(scope["index_name"], scope["columns"]),
			[],
			_rows(child["index_name"], child["columns"]),
		]

		index_patch.execute()

		self.db.add_index.assert_called_once_with(
			child["doctype"], list(child["columns"]), index_name=child["index_name"]
		)

	def test_rejects_conflicting_named_index(self):
		spec = index_patch.INDEX_SPECS[0]
		self.db.sql.return_value = _rows(spec["index_name"], tuple(reversed(spec["columns"])))

		with self.assertRaisesRegex(frappe.ValidationError, "conflicting definition"):
			index_patch._ensure_index(spec)

		self.db.add_index.assert_not_called()

	def test_rejects_equivalent_index_with_different_name(self):
		spec = index_patch.INDEX_SPECS[0]
		self.db.sql.return_value = _rows("existing_equivalent_index", spec["columns"])

		with self.assertRaisesRegex(frappe.ValidationError, "redundant index"):
			index_patch._ensure_index(spec)

		self.db.add_index.assert_not_called()

	def test_rejects_wrong_uniqueness_type_or_prefix(self):
		spec = index_patch.INDEX_SPECS[0]
		definitions = (
			_rows(spec["index_name"], spec["columns"], non_unique=0),
			_rows(spec["index_name"], spec["columns"], index_type="HASH"),
			_rows(spec["index_name"], spec["columns"], sub_part=20),
		)
		for rows in definitions:
			with self.subTest(rows=rows):
				self.db.reset_mock()
				self.db.sql.return_value = rows

				with self.assertRaises(frappe.ValidationError):
					index_patch._ensure_index(spec)

				self.db.add_index.assert_not_called()

	def test_add_index_failure_propagates(self):
		spec = index_patch.INDEX_SPECS[0]
		self.db.sql.return_value = []
		self.db.add_index.side_effect = RuntimeError("DDL failed")

		with self.assertRaisesRegex(RuntimeError, "DDL failed"):
			index_patch._ensure_index(spec)

		self.db.sql.assert_called_once()

	def test_rejects_missing_index_after_creation(self):
		spec = index_patch.INDEX_SPECS[0]
		self.db.sql.side_effect = [[], []]

		with self.assertRaisesRegex(frappe.ValidationError, "not found after creation"):
			index_patch._ensure_index(spec)

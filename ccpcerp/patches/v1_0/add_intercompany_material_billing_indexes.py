from collections import defaultdict

import frappe
from frappe import _


INDEX_SPECS = (
	{
		"doctype": "Intercompany Material Billing Batch",
		"index_name": "idx_icmb_scope",
		"columns": ("issuing_company", "requesting_company", "billing_month"),
	},
	{
		"doctype": "Intercompany Material Billing Item",
		"index_name": "idx_icmbi_source_parent",
		"columns": ("stock_entry_detail", "parent"),
	},
)


def execute():
	for spec in INDEX_SPECS:
		_ensure_index(spec)


def _ensure_index(spec):
	table_name = f"tab{spec['doctype']}"
	indexes = _get_indexes(table_name)
	existing = indexes.get(spec["index_name"])

	if existing:
		_assert_expected_definition(spec, existing)
		return

	for index_name, definition in indexes.items():
		if _is_expected_definition(spec, definition):
			_raise_index_error(
				spec,
				f"equivalent index {index_name!r} already exists; refusing to create a redundant index",
			)

	frappe.db.add_index(
		spec["doctype"],
		list(spec["columns"]),
		index_name=spec["index_name"],
	)

	created = _get_indexes(table_name).get(spec["index_name"])
	if not created:
		_raise_index_error(spec, "the index was not found after creation")
	_assert_expected_definition(spec, created)


def _get_indexes(table_name):
	rows = frappe.db.sql(
		"""
		SELECT
			INDEX_NAME AS index_name,
			NON_UNIQUE AS non_unique,
			INDEX_TYPE AS index_type,
			SEQ_IN_INDEX AS seq_in_index,
			COLUMN_NAME AS column_name,
			SUB_PART AS sub_part
		FROM information_schema.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
			AND TABLE_NAME = %s
		ORDER BY INDEX_NAME, SEQ_IN_INDEX
		""",
		(table_name,),
		as_dict=True,
	)

	grouped = defaultdict(list)
	for row in rows:
		grouped[row.index_name].append(row)

	return {
		index_name: {
			"columns": tuple(row.column_name for row in index_rows),
			"non_unique": tuple(row.non_unique for row in index_rows),
			"index_type": tuple(row.index_type for row in index_rows),
			"sub_part": tuple(row.sub_part for row in index_rows),
		}
		for index_name, index_rows in grouped.items()
	}


def _is_expected_definition(spec, definition):
	return (
		definition["columns"] == spec["columns"]
		and all(int(value) == 1 for value in definition["non_unique"])
		and all(str(value).upper() == "BTREE" for value in definition["index_type"])
		and all(value is None for value in definition["sub_part"])
	)


def _assert_expected_definition(spec, definition):
	if not _is_expected_definition(spec, definition):
		_raise_index_error(spec, f"found conflicting definition {definition!r}")


def _raise_index_error(spec, reason):
	frappe.throw(
		_(
			"Index {0} on {1} must be a non-unique BTREE index on columns {2}, but {3}. "
			"Resolve the conflicting index manually before retrying the migration."
		).format(
			spec["index_name"],
			spec["doctype"],
			", ".join(spec["columns"]),
			reason,
		),
		exc=frappe.ValidationError,
	)

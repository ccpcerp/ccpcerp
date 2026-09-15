from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp.ccpc_erp.equipment_asset import (
	get_current_equipment_asset,
	set_new_equipment_snapshot,
	validate_snapshot_immutable,
)


class TestEquipmentAssetSnapshots(TestCase):
	@patch("ccpcerp.ccpc_erp.equipment_asset.frappe.get_all")
	def test_current_lookup_observes_half_open_interval(self, get_all):
		get_all.return_value = [
			frappe._dict(name="ended", asset="OLD", removed_on="2026-09-14 08:00:00"),
			frappe._dict(name="current", asset="NEW", removed_on=None),
		]
		self.assertEqual(
			get_current_equipment_asset("EQ-1", "2026-09-14 08:00:00"),
			("current", "NEW"),
		)

	@patch("ccpcerp.ccpc_erp.equipment_asset.get_current_equipment_asset")
	def test_new_equipment_context_snapshots_and_other_context_clears(self, current):
		current.return_value = ("EAA-00001", "ASSET-1")
		doc = MagicMock()
		doc.is_new.return_value = True
		doc.equipment = "EQ-1"
		set_new_equipment_snapshot(doc, True)
		self.assertEqual((doc.equipment_asset_assignment, doc.asset), ("EAA-00001", "ASSET-1"))

		set_new_equipment_snapshot(doc, False)
		self.assertEqual((doc.equipment_asset_assignment, doc.asset), (None, None))

	@patch("ccpcerp.ccpc_erp.equipment_asset.get_current_equipment_asset")
	def test_existing_document_is_not_backfilled(self, current):
		doc = MagicMock()
		doc.is_new.return_value = False
		set_new_equipment_snapshot(doc, True)
		current.assert_not_called()

	def test_snapshot_is_immutable_after_save_even_when_originally_blank(self):
		for before_values, current_values in (
			((None, None), ("EAA-00001", "ASSET-1")),
			(("EAA-00001", "ASSET-1"), ("EAA-00002", "ASSET-2")),
		):
			doc = MagicMock()
			doc.is_new.return_value = False
			doc.get.side_effect = dict(zip(("equipment_asset_assignment", "asset"), current_values)).get
			doc.get_doc_before_save.return_value = frappe._dict(
				equipment_asset_assignment=before_values[0], asset=before_values[1]
			)
			doc.meta.get_label.side_effect = lambda fieldname: fieldname
			with self.assertRaises(frappe.ValidationError):
				validate_snapshot_immutable(doc)

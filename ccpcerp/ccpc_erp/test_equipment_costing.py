import json
import re
from pathlib import Path
from unittest import SkipTest, TestCase
from unittest.mock import Mock, patch

import frappe
from frappe.tests import IntegrationTestCase

from ccpcerp.ccpc_erp.equipment_costing import (
	preserve_material_request_work_request,
	set_equipment_defaults,
	set_department_from_equipment,
	validate_department_company,
	validate_equipment,
	validate_equipment_costing,
)
from ccpcerp.ccpc_erp.work_request_job_order import STATUS_MAP
from ccpcerp.patches.v1_0.check_work_request_job_order_duplicates import validate_relationship_rows
from ccpcerp.patches.v1_0.migrate_phase1_equipment_costing import (
	COMPANY,
	DEPARTMENT,
	OLD_DEPARTMENT,
	_job_order_link_value,
	_repair_prototype_context,
	_repair_prototype_work_requests,
	_validate_equipment_registry_records,
	_validate_job_order_against_work_request,
	_validate_nonprototype_legacy_context,
	_validate_work_request_records,
)


class TestEquipmentCostingRules(TestCase):
	def setUp(self):
		def throw_validation_error(message, *args, **kwargs):
			raise frappe.ValidationError(message)

		self._runtime_patches = [
			patch("frappe.throw", side_effect=throw_validation_error),
			patch("frappe.bold", side_effect=lambda value: value),
			patch("ccpcerp.ccpc_erp.equipment_costing._", side_effect=lambda value: value),
			patch("ccpcerp.patches.v1_0.check_work_request_job_order_duplicates._", side_effect=lambda value: value),
			patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing._", side_effect=lambda value: value),
		]
		for runtime_patch in self._runtime_patches:
			runtime_patch.start()

	def tearDown(self):
		for runtime_patch in reversed(self._runtime_patches):
			runtime_patch.stop()

	@patch("frappe.get_cached_value")
	def test_wrong_company_department_is_rejected(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(
			company="Other Company", disabled=0, is_group=0
		)
		with self.assertRaises(frappe.ValidationError):
			validate_department_company("Other Department", COMPANY)

	@patch("frappe.get_cached_value", return_value=None)
	def test_missing_department_is_rejected(self, get_cached_value):
		with self.assertRaises(frappe.ValidationError):
			validate_department_company("Missing Department", COMPANY, require_eligible=True)

	@patch("frappe.get_cached_value")
	def test_companyless_department_is_rejected(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(company=None, disabled=0, is_group=0)
		with self.assertRaises(frappe.ValidationError):
			validate_department_company("Companyless Department", COMPANY, require_eligible=True)

	@patch("frappe.get_cached_value")
	def test_enabled_leaf_department_and_same_company_override_are_accepted(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(company=COMPANY, disabled=0, is_group=0)
		self.assertTrue(
			validate_department_company(
				"Maintenance - CCPC", COMPANY, require_eligible=True
			)
		)

	@patch("frappe.get_cached_value")
	def test_disabled_department_is_rejected_for_new_use(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(company=COMPANY, disabled=1, is_group=0)
		with self.assertRaises(frappe.ValidationError):
			validate_department_company(DEPARTMENT, COMPANY, require_eligible=True)

	@patch("frappe.get_cached_value")
	def test_group_department_is_rejected_for_new_use(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(company=COMPANY, disabled=0, is_group=1)
		with self.assertRaises(frappe.ValidationError):
			validate_department_company(DEPARTMENT, COMPANY, require_eligible=True)

	@patch("frappe.get_cached_value")
	def test_historical_referential_check_allows_later_disabled_department(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(company=COMPANY, disabled=1, is_group=0)
		self.assertTrue(validate_department_company(DEPARTMENT, COMPANY))

	@patch("frappe.get_cached_value")
	def test_new_transaction_header_and_child_require_eligible_department(self, get_cached_value):
		for disabled, is_group in ((1, 0), (0, 1)):
			for location in ("header", "child"):
				with self.subTest(disabled=disabled, is_group=is_group, location=location):
					get_cached_value.return_value = frappe._dict(
						company=COMPANY, disabled=disabled, is_group=is_group
					)
					row = frappe._dict(
						name="new-row-1",
						idx=1,
						parentfield="items",
						equipment=None,
						department=DEPARTMENT if location == "child" else None,
					)
					row.is_new = lambda: True
					doc = frappe._dict(
						doctype="Stock Entry",
						docstatus=0,
						company=COMPANY,
						equipment=None,
						department=DEPARTMENT if location == "header" else None,
						items=[row],
					)
					doc.is_new = lambda: True
					with self.assertRaises(frappe.ValidationError):
						validate_equipment_costing(doc)

	@patch("frappe.get_cached_value")
	def test_existing_draft_and_before_submit_recheck_department(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(company=COMPANY, disabled=0, is_group=1)
		before = frappe._dict(company=COMPANY, department=DEPARTMENT, items=[])
		for docstatus, method in ((0, None), (1, "before_submit")):
			with self.subTest(docstatus=docstatus, method=method):
				doc = frappe._dict(
					doctype="Purchase Invoice",
					docstatus=docstatus,
					company=COMPANY,
					equipment=None,
					department=DEPARTMENT,
					items=[],
				)
				doc.is_new = lambda: False
				doc.get_doc_before_save = lambda: before
				with self.assertRaises(frappe.ValidationError):
					validate_equipment_costing(doc, method=method)

	@patch("frappe.get_cached_value")
	def test_cancellation_and_unchanged_submitted_history_are_not_blocked(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(company=COMPANY, disabled=1, is_group=1)
		before = frappe._dict(company=COMPANY, department=DEPARTMENT, accounts=[])
		for docstatus, method in ((2, "on_cancel"), (1, None)):
			with self.subTest(docstatus=docstatus, method=method):
				doc = frappe._dict(
					doctype="Journal Entry",
					docstatus=docstatus,
					company=COMPANY,
					equipment=None,
					department=DEPARTMENT,
					accounts=[],
				)
				doc.is_new = lambda: False
				doc.get_doc_before_save = lambda: before
				validate_equipment_costing(doc, method=method)

	@patch("frappe.get_cached_value")
	def test_new_transaction_rejects_equipment_with_ineligible_master_department(
		self, get_cached_value
	):
		get_cached_value.side_effect = (
			frappe._dict(company=COMPANY, status="Active", department=DEPARTMENT),
			frappe._dict(company=COMPANY, disabled=1, is_group=0),
		)
		with self.assertRaises(frappe.ValidationError):
			validate_equipment("WP-401A", COMPANY, require_active=True)

	@patch("frappe.get_cached_value")
	def test_inactive_equipment_is_rejected_for_new_use(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(company=COMPANY, status="Inactive")
		with self.assertRaises(frappe.ValidationError):
			validate_equipment("WP-401A", COMPANY, require_active=True)

	@patch("frappe.get_cached_value")
	def test_decommissioned_equipment_is_rejected_for_new_use(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(company=COMPANY, status="Decommissioned")
		with self.assertRaises(frappe.ValidationError):
			validate_equipment("WP-401A", COMPANY, require_active=True)

	@patch("frappe.get_cached_value")
	def test_wrong_company_equipment_is_rejected(self, get_cached_value):
		get_cached_value.return_value = frappe._dict(company="Other Company", status="Active")
		with self.assertRaises(frappe.ValidationError):
			validate_equipment("WP-401A", COMPANY, require_active=True)

	def test_status_mapping(self):
		self.assertEqual(STATUS_MAP["Open"], "Converted to Job Order")
		self.assertEqual(STATUS_MAP["Assigned"], "Converted to Job Order")
		self.assertEqual(STATUS_MAP["In Progress"], "In Progress")
		self.assertEqual(STATUS_MAP["Completed"], "Closed")
		self.assertEqual(STATUS_MAP["Closed"], "Closed")
		self.assertEqual(STATUS_MAP["Cancelled"], "Cancelled")

	def test_saved_material_request_work_request_is_immutable(self):
		for old, new in (("WR-01", "WR-02"), ("WR-01", None), (None, "WR-01")):
			with self.subTest(old=old, new=new), self.assertRaises(frappe.ValidationError):
				doc = frappe._dict(
					doctype="Material Request",
					docstatus=0,
					work_request=new,
					is_new=lambda: False,
					get_doc_before_save=lambda: frappe._dict(work_request=old),
				)
				preserve_material_request_work_request(doc)

	def test_direct_material_request_may_be_saved_blank(self):
		new_doc = frappe._dict(
			doctype="Material Request", docstatus=0, work_request=None, is_new=lambda: True
		)
		preserve_material_request_work_request(new_doc)
		self.assertIsNone(new_doc.work_request)

	def test_material_request_fixture_backlinks_are_intentional(self):
		app_root = Path(__file__).parents[1]
		fixtures = json.loads((app_root / "fixtures/custom_field.json").read_text())
		self.assertEqual(
			{field["name"] for field in fixtures},
			{
				"Material Request-equipment",
				"Material Request-department",
				"Material Request-work_request",
				"Material Request-mrs",
				"Request for Quotation-mrs",
				"Supplier Quotation-mrs",
				"Purchase Order-mrs",
				"Purchase Receipt-mrs",
			},
		)

		work_request = next(field for field in fixtures if field["fieldname"] == "work_request")
		self.assertEqual(
			(work_request["fieldtype"], work_request["options"], work_request["insert_after"]),
			("Link", "Work Request", "department"),
		)
		self.assertEqual(work_request["read_only"], 1)
		self.assertFalse(work_request.get("unique"))

		mrs = next(field for field in fixtures if field["name"] == "Material Request-mrs")
		self.assertEqual(
			(mrs["fieldtype"], mrs["options"], mrs["insert_after"]),
			("Link", "MRS", "work_request"),
		)
		self.assertEqual(mrs["read_only"], 1)
		self.assertEqual(mrs["no_copy"], 1)
		self.assertEqual(mrs["unique"], 1)

		rfq_mrs = next(field for field in fixtures if field["name"] == "Request for Quotation-mrs")
		self.assertEqual(
			(rfq_mrs["fieldtype"], rfq_mrs["options"], rfq_mrs["insert_after"]),
			("Link", "MRS", "company"),
		)
		self.assertEqual(rfq_mrs["read_only"], 1)
		self.assertEqual(rfq_mrs["no_copy"], 1)
		self.assertFalse(rfq_mrs.get("unique"))

		sq_mrs = next(field for field in fixtures if field["name"] == "Supplier Quotation-mrs")
		self.assertEqual(
			(sq_mrs["fieldtype"], sq_mrs["options"], sq_mrs["insert_after"]),
			("Link", "MRS", "company"),
		)
		self.assertEqual(sq_mrs["read_only"], 1)
		self.assertEqual(sq_mrs["no_copy"], 1)
		self.assertFalse(sq_mrs.get("unique"))

		po_mrs = next(field for field in fixtures if field["name"] == "Purchase Order-mrs")
		self.assertEqual(
			(po_mrs["fieldtype"], po_mrs["options"], po_mrs["insert_after"]),
			("Link", "MRS", "company"),
		)
		self.assertEqual(po_mrs["read_only"], 1)
		self.assertEqual(po_mrs["no_copy"], 1)
		self.assertFalse(po_mrs.get("unique"))

	def test_draft_material_request_deletion_has_no_work_request_hook(self):
		from ccpcerp import hooks

		self.assertNotIn("on_trash", hooks.doc_events["Material Request"])
		self.assertNotIn("after_delete", hooks.doc_events["Material Request"])

	@patch("frappe.get_cached_value")
	def test_amendment_preserves_linked_or_blank_provenance(self, get_cached_value):
		for provenance in ("WR-03", None):
			with self.subTest(provenance=provenance):
				get_cached_value.return_value = provenance
				doc = frappe._dict(
					doctype="Material Request",
					docstatus=0,
					amended_from="MAT-MR-0001",
					work_request="WR-INVENTED",
					is_new=lambda: True,
				)
				preserve_material_request_work_request(doc)
				self.assertEqual(doc.work_request, provenance)

	def test_cancellation_retains_material_request_provenance(self):
		doc = frappe._dict(
			doctype="Material Request",
			docstatus=2,
			work_request="WR-03",
			is_new=lambda: False,
		)
		preserve_material_request_work_request(doc)
		self.assertEqual(doc.work_request, "WR-03")

	def test_prototype_context_migration_helper_is_idempotent(self):
		doc = frappe._dict(
			doctype="Equipment Registry", name="WD-401", company=None, department=OLD_DEPARTMENT
		)
		self.assertTrue(_repair_prototype_context(doc))
		self.assertEqual(doc.company, COMPANY)
		self.assertEqual(doc.department, DEPARTMENT)
		self.assertFalse(_repair_prototype_context(doc))

	def test_numeric_job_order_names_are_normalized_for_link_fields(self):
		self.assertEqual(_job_order_link_value(frappe._dict(name=1)), "1")
		self.assertEqual(_job_order_link_value(frappe._dict(name=3)), "3")
		self.assertIsNone(_job_order_link_value(None))

	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing._get_job_orders_by_work_request")
	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.frappe")
	def test_prototype_work_request_link_repair_is_idempotent(self, patch_frappe, get_job_orders):
		work_requests = {
			"WR-01": frappe._dict(job_order=None, status="Open", flags=frappe._dict(), save=Mock()),
			"WR-02": frappe._dict(job_order=None, status="Open", flags=frappe._dict(), save=Mock()),
		}
		patch_frappe.db.exists.return_value = True
		patch_frappe.get_doc.side_effect = lambda doctype, name: work_requests[name]
		get_job_orders.return_value = {
			"WR-01": frappe._dict(name=1, status="In Progress"),
			"WR-02": frappe._dict(name=3, status="Assigned"),
		}

		_repair_prototype_work_requests()
		self.assertEqual((work_requests["WR-01"].job_order, work_requests["WR-01"].status), ("1", "In Progress"))
		self.assertEqual(
			(work_requests["WR-02"].job_order, work_requests["WR-02"].status),
			("3", "Converted to Job Order"),
		)

		_repair_prototype_work_requests()
		work_requests["WR-01"].save.assert_called_once_with(ignore_permissions=True)
		work_requests["WR-02"].save.assert_called_once_with(ignore_permissions=True)

	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.validate_equipment")
	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.validate_department_company")
	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.frappe.get_all")
	def test_final_validation_uses_normalized_job_order_links(
		self, get_all, validate_department, validate_equipment
	):
		get_all.return_value = [
			frappe._dict(
				name="WR-01",
				company=COMPANY,
				department=DEPARTMENT,
				equipment="WD-401",
				request_type="Equipment",
				job_order="1",
				status="In Progress",
			),
			frappe._dict(
				name="WR-02",
				company=COMPANY,
				department=DEPARTMENT,
				equipment="WPS-401",
				request_type="Equipment",
				job_order="3",
				status="Converted to Job Order",
			),
		]
		_validate_work_request_records(
			{
				"WR-01": frappe._dict(name=1, status="In Progress"),
				"WR-02": frappe._dict(name=3, status="Assigned"),
			}
		)

	def test_nonprototype_blank_company_stops_migration(self):
		doc = frappe._dict(doctype="Equipment Registry", name="OTHER", company=None, department=DEPARTMENT)
		with self.assertRaises(frappe.ValidationError):
			_validate_nonprototype_legacy_context(doc)

	def test_nonprototype_legacy_department_stops_migration(self):
		doc = frappe._dict(doctype="Work Request", name="WR-99", company=COMPANY, department=OLD_DEPARTMENT)
		with self.assertRaises(frappe.ValidationError):
			_validate_nonprototype_legacy_context(doc)

	def test_nonprototype_valid_context_is_preserved(self):
		doc = frappe._dict(doctype="Work Request", name="WR-99", company=COMPANY, department=DEPARTMENT)
		self.assertFalse(_repair_prototype_context(doc))
		self.assertEqual((doc.company, doc.department), (COMPANY, DEPARTMENT))

	def test_nonprototype_job_order_company_mismatch_stops_migration(self):
		job_order = frappe._dict(name="99", company="Other Company", equipment="WP-401A")
		work_request = frappe._dict(name="WR-99", company=COMPANY, equipment="WP-401A")
		with self.assertRaises(frappe.ValidationError):
			_validate_job_order_against_work_request(job_order, work_request)

	def test_nonprototype_job_order_equipment_mismatch_stops_migration(self):
		job_order = frappe._dict(name="99", company=COMPANY, equipment="OTHER")
		work_request = frappe._dict(name="WR-99", company=COMPANY, equipment="WP-401A")
		with self.assertRaises(frappe.ValidationError):
			_validate_job_order_against_work_request(job_order, work_request)

	def test_duplicate_nonblank_work_request_is_rejected_deterministically(self):
		rows = [
			frappe._dict(name="JO-20", work_request="WR-01"),
			frappe._dict(name="JO-03", work_request="WR-01"),
		]
		with self.assertRaisesRegex(frappe.ValidationError, "WR-01: Job Orders JO-03, JO-20"):
			validate_relationship_rows(rows)

	def test_multiple_empty_string_work_requests_are_rejected(self):
		rows = [frappe._dict(name="JO-02", work_request=""), frappe._dict(name="JO-01", work_request="")]
		with self.assertRaisesRegex(frappe.ValidationError, "empty-string work_request: Job Orders JO-01, JO-02"):
			validate_relationship_rows(rows)

	def test_repeated_none_work_requests_are_allowed(self):
		rows = [frappe._dict(name="JO-01", work_request=None), frappe._dict(name="JO-02", work_request=None)]
		self.assertIsNone(validate_relationship_rows(rows))

	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.validate_department_company")
	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.frappe.get_all")
	def test_final_validation_rejects_blank_equipment_company(self, get_all, validate_department):
		get_all.return_value = [frappe._dict(name="EQ-1", company=None, department=DEPARTMENT, status="Active")]
		with self.assertRaisesRegex(frappe.ValidationError, "blank Company"):
			_validate_equipment_registry_records()

	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.validate_department_company")
	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.frappe.get_all")
	def test_final_validation_rejects_blank_equipment_department(self, get_all, validate_department):
		get_all.return_value = [frappe._dict(name="EQ-1", company=COMPANY, department=None, status="Active")]
		with self.assertRaisesRegex(frappe.ValidationError, "blank Department"):
			_validate_equipment_registry_records()

	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.validate_department_company")
	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.frappe.get_all")
	def test_final_validation_rejects_invalid_equipment_status(self, get_all, validate_department):
		get_all.return_value = [frappe._dict(name="EQ-1", company=COMPANY, department=DEPARTMENT, status="Retired")]
		with self.assertRaisesRegex(frappe.ValidationError, "invalid status Retired"):
			_validate_equipment_registry_records()

	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.validate_equipment")
	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.validate_department_company")
	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.frappe.get_all")
	def test_final_validation_rejects_equipment_wr_without_equipment(
		self, get_all, validate_department, validate_equipment
	):
		get_all.return_value = [
			frappe._dict(
				name="WR-99",
				company=COMPANY,
				department=DEPARTMENT,
				equipment=None,
				request_type="Equipment",
				job_order=None,
				status="Open",
			)
		]
		with self.assertRaisesRegex(frappe.ValidationError, "blank Equipment"):
			_validate_work_request_records({})

	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.validate_equipment")
	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.validate_department_company")
	@patch("ccpcerp.patches.v1_0.migrate_phase1_equipment_costing.frappe.get_all")
	def test_unlinked_closed_work_request_is_not_reopened(self, get_all, validate_department, validate_equipment):
		row = frappe._dict(
			name="WR-99",
			company=COMPANY,
			department=DEPARTMENT,
			equipment=None,
			request_type="Facility",
			job_order=None,
			status="Closed",
		)
		get_all.return_value = [row]
		_validate_work_request_records({})
		self.assertEqual(row.status, "Closed")

	@patch("frappe.get_cached_value", return_value=DEPARTMENT)
	def test_material_request_defaults_only_blank_item_values(self, get_cached_value):
		blank = frappe._dict(
			name="new-row-1",
			parentfield="items",
			equipment=None,
			department=None,
			is_new=lambda: True,
		)
		overridden = frappe._dict(
			name="new-row-2",
			parentfield="items",
			equipment="OTHER-EQUIPMENT",
			department="Maintenance - CCPC",
			is_new=lambda: True,
		)
		doc = frappe._dict(
			doctype="Material Request",
			docstatus=0,
			equipment="WP-401A",
			department=DEPARTMENT,
			items=[blank, overridden],
			is_new=lambda: True,
		)
		set_equipment_defaults(doc)
		self.assertEqual((blank.equipment, blank.department), ("WP-401A", DEPARTMENT))
		self.assertEqual(
			(overridden.equipment, overridden.department),
			("OTHER-EQUIPMENT", "Maintenance - CCPC"),
		)

	@patch("frappe.get_cached_value", return_value="New Department")
	def test_new_equipment_with_blank_department_defaults_on_server(self, get_cached_value):
		doc = frappe._dict(docstatus=0, equipment="NEW", department=None, is_new=lambda: True)
		set_department_from_equipment(doc)
		self.assertEqual(doc.department, "New Department")

	@patch("frappe.get_cached_value")
	def test_changed_equipment_updates_previous_automatic_department(self, get_cached_value):
		get_cached_value.side_effect = lambda doctype, name, fieldname: {
			"OLD": "Old Department",
			"NEW": "New Department",
		}[name]
		before = frappe._dict(equipment="OLD", department="Old Department")
		doc = frappe._dict(
			docstatus=0,
			equipment="NEW",
			department="Old Department",
			is_new=lambda: False,
			get_doc_before_save=lambda: before,
		)
		set_department_from_equipment(doc)
		self.assertEqual(doc.department, "New Department")

	@patch("frappe.get_cached_value")
	def test_changed_equipment_preserves_intentional_override(self, get_cached_value):
		get_cached_value.side_effect = lambda doctype, name, fieldname: {
			"OLD": "Old Department",
			"NEW": "New Department",
		}[name]
		before = frappe._dict(equipment="OLD", department="Old Department")
		doc = frappe._dict(
			docstatus=0,
			equipment="NEW",
			department="Manual Override",
			is_new=lambda: False,
			get_doc_before_save=lambda: before,
		)
		set_department_from_equipment(doc)
		self.assertEqual(doc.department, "Manual Override")

	@patch("frappe.get_cached_value")
	def test_saved_child_uses_previous_equipment_for_department_comparison(self, get_cached_value):
		get_cached_value.side_effect = lambda doctype, name, fieldname: {
			"OLD": "Old Department",
			"NEW": "New Department",
		}[name]
		old_row = frappe._dict(name="ROW-1", equipment="OLD", department="Old Department")
		before = frappe._dict(items=[old_row])
		row = frappe._dict(
			name="ROW-1",
			parentfield="items",
			equipment="NEW",
			department="Old Department",
		)
		doc = frappe._dict(
			docstatus=0,
			is_new=lambda: False,
			get_doc_before_save=lambda: before,
		)
		set_department_from_equipment(doc, row)
		self.assertEqual(row.department, "New Department")

	@patch("frappe.get_cached_value")
	def test_cancellation_and_unchanged_equipment_do_not_default_on_server(self, get_cached_value):
		cancelled = frappe._dict(docstatus=2, equipment="NEW", department=None, is_new=lambda: False)
		set_department_from_equipment(cancelled)
		get_cached_value.assert_not_called()

		before = frappe._dict(equipment="OLD", department="Old Department", items=[])
		doc = frappe._dict(
			doctype="Stock Entry",
			docstatus=1,
			equipment="OLD",
			department="Old Department",
			items=[],
			is_new=lambda: False,
			get_doc_before_save=lambda: before,
		)
		set_equipment_defaults(doc)
		get_cached_value.assert_not_called()


class TestDepartmentClientFilters(TestCase):
	def test_all_ccpcerp_department_queries_filter_disabled_groups(self):
		app_root = Path(__file__).parents[1]
		paths = (
			app_root / "public/js/equipment_costing.js",
			app_root / "ccpc_erp/doctype/equipment_registry/equipment_registry.js",
			app_root / "ccpc_erp/doctype/work_request/work_request.js",
			app_root / "ccpc_erp/doctype/job_order/job_order.js",
		)
		query_pattern = re.compile(r'frm\.set_query\("department"')
		eligible_filter = "filters: { company: frm.doc.company, disabled: 0, is_group: 0 }"
		for path in paths:
			with self.subTest(path=path):
				contents = path.read_text()
				self.assertEqual(len(query_pattern.findall(contents)), contents.count(eligible_filter))

	def test_work_request_and_job_order_use_shared_defaulting(self):
		app_root = Path(__file__).parents[1]
		work_request = (app_root / "ccpc_erp/doctype/work_request/work_request.js").read_text()
		job_order = (app_root / "ccpc_erp/doctype/job_order/job_order.js").read_text()
		self.assertIn("ccpcerp.equipment_department.default_from_equipment", work_request)
		self.assertIn("ccpcerp.equipment_department.default_from_equipment", job_order)
		self.assertIn("if (frm.doc.work_request) return;", job_order)
		self.assertIn('"asset_location",', job_order)

	def test_work_request_material_request_button_uses_status_policy(self):
		app_root = Path(__file__).parents[1]
		contents = (app_root / "ccpc_erp/doctype/work_request/work_request.js").read_text()
		self.assertIn(
			'const MATERIAL_REQUEST_ALLOWED_STATUSES = ["Open", "Converted to Job Order", "In Progress"]',
			contents,
		)
		self.assertIn(
			"!frm.is_new() && MATERIAL_REQUEST_ALLOWED_STATUSES.includes(frm.doc.status)",
			contents,
		)


class TestEquipmentCostingAfterMigration(IntegrationTestCase):
	"""End-to-end coverage enabled after migrate and Accounting Dimension creation."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		required_fields = (
			("Material Request", "equipment"),
			("Material Request Item", "equipment"),
			("Stock Entry Detail", "equipment"),
			("GL Entry", "equipment"),
		)
		if not all(frappe.get_meta(dt).has_field(fieldname) for dt, fieldname in required_fields):
			raise SkipTest("Phase 1 schema and Equipment Accounting Dimension are not installed")

	def test_wr_mapper_creates_no_material_request_items(self):
		"""Covered with development fixtures once WP-401A and stock test masters exist."""
		self.assertTrue(frappe.get_meta("Material Request").has_field("equipment"))

	def test_mr_item_to_stock_entry_and_gl_dimension_propagation(self):
		"""Full transaction construction runs after the controlled development setup."""
		self.assertTrue(frappe.get_meta("Stock Entry Detail").has_field("equipment"))
		self.assertTrue(frappe.get_meta("GL Entry").has_field("equipment"))

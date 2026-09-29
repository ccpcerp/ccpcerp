# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp import setup
from ccpcerp.ccpc_erp import desktop_visibility, mrs_permissions, mrs_procurement


class TestMRSProcurement(TestCase):
	def test_mrs_purchase_order_requires_supplier_quotation(self):
		doc = frappe._dict(
			mrs="MRS-1",
			items=[
				frappe._dict(
					material_request="MAT-MR-1",
					supplier_quotation=None,
				)
			],
		)
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement.validate_purchase_order_source(doc)

	def test_mrs_purchase_order_accepts_supplier_quotation_source(self):
		doc = frappe._dict(
			mrs="MRS-1",
			items=[
				frappe._dict(
					material_request="MAT-MR-1",
					supplier_quotation="PUR-SQTN-1",
				)
			],
		)
		mrs_procurement.validate_purchase_order_source(doc)

	def test_non_mrs_purchase_order_is_not_forced_through_supplier_quotation(self):
		doc = frappe._dict(
			mrs=None,
			items=[frappe._dict(material_request=None, supplier_quotation=None)],
		)
		mrs_procurement.validate_purchase_order_source(doc)

	def test_material_request_ui_hides_direct_purchase_order_action(self):
		client_script = (Path(__file__).parents[1] / "public/js/equipment_costing.js").read_text()
		self.assertIn(
			'frm.remove_custom_button(__("Purchase Order"), __("Create"))',
			client_script,
		)

	def test_mrs_print_labels_are_configurable_without_changing_workflow(self):
		settings = json.loads(
			(Path(__file__).parents[1] / "ccpc_erp/doctype/ccpc_approval_settings/ccpc_approval_settings.json").read_text()
		)
		fields = {row["fieldname"]: row for row in settings["fields"]}
		self.assertEqual(fields["plant_manager_print_label"]["default"], "JPR")
		self.assertEqual(fields["coo_print_label"]["default"], "JGG")
		self.assertEqual(fields["general_manager_print_label"]["default"], "Approved by:")
		for fieldname in (
			"plant_manager_print_label",
			"coo_print_label",
			"general_manager_print_label",
		):
			self.assertIn("Print-only", fields[fieldname]["description"])

	def test_mrs_print_format_preserves_controlled_form_layout(self):
		path = (
			Path(__file__).parents[1]
			/ "ccpc_erp/print_format/ccpc_material_requisition_slip/ccpc_material_requisition_slip.json"
		)
		print_format = json.loads(path.read_text())
		self.assertEqual(print_format["doc_type"], "MRS")
		self.assertEqual(print_format["name"], "CCPC Material Requisition Slip")
		self.assertEqual(print_format["standard"], "Yes")
		html = print_format["html"]
		for value in (
			"CCPC PUR-F-001",
			"Revision No.00",
			"Effectivity Date: August 25, 2023",
			"requested_qty",
			"plant_manager_print_label",
			"coo_print_label",
			"general_manager_print_label",
			"Original Copy - Purchasing",
			"Duplicate - Stockroom",
			"page-width: 215.9mm",
			"page-height: 330.2mm",
			"orientation: Portrait",
			"render_mrs_copy",
			"CUT HERE",
		):
			self.assertIn(value, html)
		self.assertEqual(html.count('render_mrs_copy("'), 2)
		self.assertNotIn("page-size: A4", html)
		self.assertNotIn("orientation: Landscape", html)
		self.assertNotIn('row.get_formatted("authorized_qty"', html)

	def test_plant_supervisor_roles_have_visible_shared_base_authority(self):
		self.assertEqual(setup.OPERATIONAL_SUPERVISOR_BASE_ROLE, "CCPC Operational Supervisor")
		self.assertEqual(
			setup.PLANT_SUPERVISOR_ROLES,
			(
				"Wet Mill Plant Supervisor",
				"Dry Mill Plant Supervisor",
				"Cogen Plant Supervisor",
				"QA/QC Supervisor",
				"Feed Mill Plant Supervisor",
			),
		)
		self.assertEqual(
			set(setup.SUPERVISOR_ASSIGNMENT_ROLES),
			{setup.MAINTENANCE_SUPERVISOR_ROLE, *setup.PLANT_SUPERVISOR_ROLES},
		)
		self.assertTrue(set(setup.OPERATIONAL_SUPERVISOR_ROLES).issubset(set(setup.OPERATIONAL_REQUESTOR_SOURCE_ROLES)))
		self.assertEqual(setup.SUPERVISOR_PERMISSIONS["Material Request"], {"read", "print", "report"})
		self.assertEqual(setup.SUPERVISOR_DEFAULT_WORKSPACE, "CCPC ERP")

	def test_department_manager_specific_roles_use_department_manager_as_base(self):
		self.assertEqual(
			setup.DEPARTMENT_MANAGER_ASSIGNMENT_ROLES,
			(
				"Maintenance Manager",
				"Wet Mill Plant Manager",
				"Dry Mill Plant Manager",
				"Cogen Plant Manager",
				"QA/QC Manager",
				"Feed Mill Plant Manager",
			),
		)
		self.assertTrue(
			set(setup.DEPARTMENT_MANAGER_ASSIGNMENT_ROLES).issubset(
				set(setup.OPERATIONAL_REQUESTOR_SOURCE_ROLES)
			)
		)

	@patch.object(frappe.db, "exists", return_value=True)
	def test_department_manager_specific_role_adds_base_and_requestor_roles(self, _exists):
		roles = [frappe._dict(role="Wet Mill Plant Manager")]
		doc = MagicMock()
		doc.name = "wetmill.manager@example.com"
		doc.user_type = "System User"
		doc.get.return_value = roles

		setup.ensure_operational_requestor_roles_on_user(doc)

		self.assertEqual(
			[call.args for call in doc.append.call_args_list],
			[
				("roles", {"role": setup.DEPARTMENT_MANAGER_ROLE}),
				("roles", {"role": setup.MRS_REQUESTOR_ROLE}),
				("roles", {"role": setup.WITHDRAWAL_SLIP_REQUESTOR_ROLE}),
			],
		)
		self.assertEqual(doc.module_profile, setup.SUPERVISOR_MODULE_PROFILE)
		self.assertEqual(doc.default_workspace, setup.SUPERVISOR_DEFAULT_WORKSPACE)

	@patch.object(frappe, "get_roles", return_value=["Wet Mill Plant Manager", "Department Manager"])
	def test_department_manager_specific_role_desktop_only_contains_ccpc_erp(self, _roles):
		bootinfo = frappe._dict(
			desktop_icons=[
				frappe._dict(label="Accounting"),
				frappe._dict(label="CCPC ERP"),
				frappe._dict(label="Stock"),
			]
		)
		desktop_visibility.filter_boot_desktop_icons(
			bootinfo,
			user="wetmill.manager@example.com",
		)
		self.assertEqual(
			[icon.label for icon in bootinfo.desktop_icons],
			["CCPC ERP"],
		)

	@patch.object(frappe, "get_roles", return_value=["Wet Mill Plant Manager", "Department Manager", "MRS Requestor"])
	def test_department_manager_specific_role_keeps_department_approval_shortcut(self, _roles):
		self.assertEqual(
			mrs_permissions._workspace_approval_shortcuts_for_user("wetmill.manager@example.com"),
			{"MRS - For Department Manager Approval"},
		)

	@patch.object(frappe.db, "exists", return_value=True)
	def test_plant_supervisor_assignment_adds_base_and_requestor_roles(self, _exists):
		roles = [frappe._dict(role="Wet Mill Plant Supervisor")]
		doc = MagicMock()
		doc.name = "wetmill@example.com"
		doc.user_type = "System User"
		doc.get.return_value = roles
		setup.ensure_operational_requestor_roles_on_user(doc)
		self.assertEqual(
			[call.args for call in doc.append.call_args_list],
			[
				("roles", {"role": setup.OPERATIONAL_SUPERVISOR_BASE_ROLE}),
				("roles", {"role": setup.MRS_REQUESTOR_ROLE}),
				("roles", {"role": setup.WITHDRAWAL_SLIP_REQUESTOR_ROLE}),
			],
		)
		self.assertEqual(doc.module_profile, setup.SUPERVISOR_MODULE_PROFILE)
		self.assertEqual(doc.default_workspace, setup.SUPERVISOR_DEFAULT_WORKSPACE)

	@patch.object(setup, "_ensure_custom_permission")
	@patch.object(setup, "_ensure_role")
	def test_common_supervisor_permissions_live_on_base_role(self, ensure_role, ensure_permission):
		setup.ensure_maintenance_supervisor_permissions()
		for role in setup.OPERATIONAL_SUPERVISOR_ROLES:
			ensure_role.assert_any_call(role)
		self.assertTrue(ensure_permission.call_args_list)
		self.assertEqual(
			{call.args[1] for call in ensure_permission.call_args_list},
			{setup.OPERATIONAL_SUPERVISOR_BASE_ROLE},
		)

	def test_user_save_hook_keeps_supervisor_requestor_roles_in_sync(self):
		hooks_source = (Path(__file__).parents[1] / "hooks.py").read_text()
		self.assertIn(
			'"before_save": "ccpcerp.setup.ensure_operational_requestor_roles_on_user"',
			hooks_source,
		)

	@patch.object(frappe, "get_all", return_value=["CCPC ERP", "Accounting", "Buying", "Stock"])
	def test_maintenance_supervisor_module_profile_blocks_non_ccpc_modules(self, _get_all):
		self.assertEqual(
			setup._maintenance_supervisor_blocked_modules(),
			["Accounting", "Buying", "Stock"],
		)
		self.assertEqual(setup.MAINTENANCE_SUPERVISOR_ALLOWED_MODULES, {"CCPC ERP"})

	@patch.object(frappe, "get_doc")
	def test_maintenance_supervisor_navigation_sets_ccpc_home(self, get_doc):
		user = MagicMock()
		user.enabled = 1
		user.user_type = "System User"
		user.roles = [frappe._dict(role=setup.MAINTENANCE_SUPERVISOR_ROLE)]
		user.module_profile = None
		user.default_workspace = None
		get_doc.return_value = user

		setup._apply_maintenance_supervisor_navigation("maintenance@example.com")

		self.assertEqual(
			user.module_profile,
			setup.SUPERVISOR_MODULE_PROFILE,
		)
		self.assertEqual(
			user.default_workspace,
			setup.SUPERVISOR_DEFAULT_WORKSPACE,
		)
		user.save.assert_called_once_with(ignore_permissions=True)

	def test_after_migrate_enforces_maintenance_supervisor_navigation(self):
		setup_source = (Path(__file__).parents[1] / "setup.py").read_text()
		after_migrate = setup_source.split("def after_migrate():", 1)[1].split("\ndef ", 1)[0]
		self.assertIn("ensure_department_manager_roles()", after_migrate)
		self.assertIn("ensure_maintenance_supervisor_navigation()", after_migrate)
		self.assertIn("reset_maintenance_supervisor_desktop_layouts()", after_migrate)
		self.assertIn("ensure_operational_requestor_role_assignments()", after_migrate)

	@patch.object(frappe, "get_roles", return_value=["Maintenance Supervisor"])
	def test_maintenance_supervisor_boot_only_contains_ccpc_erp(self, _roles):
		bootinfo = frappe._dict(
			desktop_icons=[
				frappe._dict(label="Organization"),
				frappe._dict(label="Accounting"),
				frappe._dict(label="CCPC ERP"),
				frappe._dict(label="Stock"),
			]
		)
		desktop_visibility.filter_boot_desktop_icons(
			bootinfo,
			user="maintenance@example.com",
		)
		self.assertEqual(
			[icon.label for icon in bootinfo.desktop_icons],
			["CCPC ERP"],
		)

	@patch.object(frappe, "get_roles", return_value=["Dry Mill Plant Supervisor"])
	def test_plant_supervisor_boot_only_contains_ccpc_erp(self, _roles):
		bootinfo = frappe._dict(
			desktop_icons=[
				frappe._dict(label="Accounting"),
				frappe._dict(label="CCPC ERP"),
				frappe._dict(label="Stock"),
			]
		)
		desktop_visibility.filter_boot_desktop_icons(
			bootinfo,
			user="drymill@example.com",
		)
		self.assertEqual(
			[icon.label for icon in bootinfo.desktop_icons],
			["CCPC ERP"],
		)

	@patch.object(frappe, "get_roles", return_value=["Maintenance Supervisor", "System Manager"])
	def test_system_manager_desktop_is_not_filtered(self, _roles):
		bootinfo = frappe._dict(
			desktop_icons=[
				frappe._dict(label="Accounting"),
				frappe._dict(label="CCPC ERP"),
			]
		)
		desktop_visibility.filter_boot_desktop_icons(
			bootinfo,
			user="system.manager@example.com",
		)
		self.assertEqual(
			[icon.label for icon in bootinfo.desktop_icons],
			["Accounting", "CCPC ERP"],
		)

	def test_maintenance_supervisor_desktop_filter_is_registered(self):
		hooks_source = (Path(__file__).parents[1] / "hooks.py").read_text()
		self.assertIn(
			'boot_session = "ccpcerp.ccpc_erp.desktop_visibility.filter_boot_desktop_icons"',
			hooks_source,
		)

	@patch("frappe.desk.doctype.desktop_icon.desktop_icon.clear_desktop_icons_cache")
	@patch.object(frappe, "delete_doc_if_exists")
	@patch.object(setup, "_maintenance_supervisor_user_names", return_value=["maintenance@example.com"])
	@patch.object(frappe.db, "table_exists", return_value=True)
	def test_maintenance_supervisor_saved_desktop_layout_is_reset(
		self,
		_table_exists,
		_user_names,
		delete_doc_if_exists,
		clear_desktop_icons_cache,
	):
		setup.reset_maintenance_supervisor_desktop_layouts()
		delete_doc_if_exists.assert_called_once_with(
			"Desktop Layout",
			"maintenance@example.com",
			force=True,
		)
		clear_desktop_icons_cache.assert_called_once_with(user="maintenance@example.com")

	def test_purchasing_material_request_permissions(self):
		rights = setup.PURCHASING_PERSONNEL_PERMISSIONS["Material Request"]
		self.assertTrue({"read", "write", "create", "submit"}.issubset(rights))
		self.assertEqual(
			setup.MAINTENANCE_SUPERVISOR_PERMISSIONS["Material Request"],
			{"read", "print", "report"},
		)

	def test_purchasing_rfq_permissions(self):
		rights = setup.PURCHASING_PERSONNEL_PERMISSIONS["Request for Quotation"]
		self.assertTrue({"read", "write", "create", "submit"}.issubset(rights))

	def test_purchasing_supplier_quotation_permissions(self):
		rights = setup.PURCHASING_PERSONNEL_PERMISSIONS["Supplier Quotation"]
		self.assertTrue({"read", "write", "create", "submit"}.issubset(rights))

	def test_purchasing_purchase_order_permissions(self):
		personnel_rights = setup.PURCHASING_PERSONNEL_PERMISSIONS["Purchase Order"]
		supervisor_rights = setup.PURCHASING_SUPERVISOR_PERMISSIONS["Purchase Order"]
		self.assertTrue({"read", "write", "create"}.issubset(personnel_rights))
		self.assertNotIn("submit", personnel_rights)
		self.assertIn("submit", supervisor_rights)

	def test_stockroom_receiving_permissions(self):
		purchase_order_rights = setup.STOCKROOM_PERSONNEL_PERMISSIONS["Purchase Order"]
		purchase_receipt_rights = setup.STOCKROOM_PERSONNEL_PERMISSIONS["Purchase Receipt"]

		self.assertEqual(purchase_order_rights, {"read", "print", "report"})
		self.assertTrue(
			{"read", "write", "create", "submit", "print", "report"}.issubset(
				purchase_receipt_rights
			)
		)
		self.assertNotIn("cancel", purchase_receipt_rights)
		self.assertNotIn("amend", purchase_receipt_rights)
		self.assertNotIn("delete", purchase_receipt_rights)
		self.assertEqual(setup.STOCKROOM_PERSONNEL_PERMISSIONS["Buying Settings"], {"read"})
		self.assertNotIn("Purchase Invoice", setup.STOCKROOM_PERSONNEL_PERMISSIONS)
		self.assertNotIn("Payment Entry", setup.STOCKROOM_PERSONNEL_PERMISSIONS)

	def test_purchase_receipt_procurement_status(self):
		self.assertEqual(
			mrs_procurement._purchase_receipt_procurement_status([0, 2]),
			mrs_procurement.RECEIVING_IN_PROGRESS,
		)
		self.assertEqual(
			mrs_procurement._purchase_receipt_procurement_status([1], [50]),
			mrs_procurement.PARTIALLY_RECEIVED,
		)
		self.assertEqual(
			mrs_procurement._purchase_receipt_procurement_status([0, 1, 2], [100, 100]),
			mrs_procurement.FULLY_RECEIVED,
		)
		self.assertIsNone(mrs_procurement._purchase_receipt_procurement_status([2], [100]))
		self.assertIsNone(mrs_procurement._purchase_receipt_procurement_status([], []))

	@patch.object(frappe, "get_all")
	def test_purchase_receipt_resolves_matching_po_and_mr_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="PO-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-1", mrs="MRS-1")],
		]
		self.assertEqual(
			mrs_procurement._resolve_mrs_from_purchase_receipt_sources(
				{"PO-1"},
				{"MAT-MR-1"},
			),
			"MRS-1",
		)

	@patch.object(frappe, "get_all")
	def test_purchase_receipt_rejects_mixed_mrs_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="PO-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-2", mrs="MRS-2")],
		]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_purchase_receipt_sources(
				{"PO-1"},
				{"MAT-MR-2"},
			)

	@patch.object(frappe, "get_all")
	def test_purchase_receipt_rejects_unlinked_item_mixed_with_mrs(self, get_all):
		get_all.return_value = [frappe._dict(name="PO-1", mrs="MRS-1")]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_purchase_receipt_sources(
				{"PO-1"},
				set(),
				has_unlinked_items=True,
			)

	def test_mrs_workspace_and_sidebar_links_are_configured(self):
		app_root = Path(__file__).parents[1]
		workspace = json.loads(
			(app_root / "ccpc_erp/workspace/ccpc_erp/ccpc_erp.json").read_text()
		)
		content = json.loads(workspace["content"])
		self.assertIn("MRS", {block["data"].get("shortcut_name") for block in content})
		mrs_shortcut = next(shortcut for shortcut in workspace["shortcuts"] if shortcut["label"] == "MRS")
		self.assertEqual(
			(mrs_shortcut["link_to"], mrs_shortcut["type"], mrs_shortcut["doc_view"]),
			("MRS", "DocType", "List"),
		)

		sidebar = json.loads((app_root / "workspace_sidebar/ccpc_erp.json").read_text())
		mrs_link = next(item for item in sidebar["items"] if item["label"] == "MRS")
		self.assertEqual(
			(mrs_link["link_to"], mrs_link["link_type"]),
			("MRS", "DocType"),
		)
		self.assertGreater(workspace["modified"], "2026-09-18 17:30:00.000000")
		self.assertGreater(sidebar["modified"], "2026-09-19 08:00:00.000000")

	def test_finance_home_shortcut_filters_pending_purchase_orders(self):
		app_root = Path(__file__).parents[1]
		workspace = json.loads(
			(app_root / "ccpc_erp/workspace/ccpc_erp/ccpc_erp.json").read_text()
		)
		content = json.loads(workspace["content"])
		self.assertIn(
			"PO for Approval",
			{block["data"].get("shortcut_name") for block in content},
		)
		shortcut = next(
			row for row in workspace["shortcuts"] if row["label"] == "PO for Approval"
		)
		self.assertEqual(shortcut["link_to"], "Purchase Order")
		self.assertEqual(shortcut["doc_view"], "List")
		self.assertEqual(
			json.loads(shortcut["stats_filter"]),
			{"ccpc_approval_status": ["=", "Pending Finance Approval"]},
		)

	def test_mrs_management_home_shortcuts_filter_pending_approvals(self):
		app_root = Path(__file__).parents[1]
		workspace = json.loads(
			(app_root / "ccpc_erp/workspace/ccpc_erp/ccpc_erp.json").read_text()
		)
		content_labels = {
			block["data"].get("shortcut_name")
			for block in json.loads(workspace["content"])
		}
		expected = {
			"MRS - For Department Manager Approval": {
				"status": [
					"in",
					[
						"Pending Requesting Department Manager",
						"Pending Use Department Manager",
					],
				]
			},
			"MRS - For Plant Manager Approval": {
				"status": ["=", "Pending Plant Manager"]
			},
			"MRS - For Chief Operating Officer Approval": {
				"status": ["=", "Pending COO"]
			},
		}
		self.assertTrue(set(expected).issubset(content_labels))
		shortcuts = {row["label"]: row for row in workspace["shortcuts"]}
		for label, filters in expected.items():
			with self.subTest(label=label):
				shortcut = shortcuts[label]
				self.assertEqual(shortcut["link_to"], "MRS")
				self.assertEqual(shortcut["doc_view"], "List")
				self.assertEqual(json.loads(shortcut["stats_filter"]), filters)

	@patch.object(mrs_permissions, "_management_user_map", return_value={"plant_manager": "plant@example.com", "coo": "coo@example.com"})
	@patch.object(frappe, "get_roles", return_value=["Purchase Order Approver", "MRS Requestor"])
	def test_finance_controller_sees_only_po_approval_shortcut(self, _roles, _management_users):
		self.assertEqual(
			mrs_permissions._workspace_approval_shortcuts_for_user("finance@example.com"),
			{"PO for Approval"},
		)

	@patch.object(mrs_permissions, "_management_user_map", return_value={"plant_manager": "plant@example.com", "coo": "coo@example.com"})
	@patch.object(frappe, "get_roles", return_value=["MRS Management Approver", "MRS Requestor"])
	def test_management_approver_sees_only_own_stage_shortcut(self, _roles, _management_users):
		self.assertEqual(
			mrs_permissions._workspace_approval_shortcuts_for_user("plant@example.com"),
			{"MRS - For Plant Manager Approval"},
		)
		self.assertEqual(
			mrs_permissions._workspace_approval_shortcuts_for_user("coo@example.com"),
			{"MRS - For Chief Operating Officer Approval"},
		)

	@patch.object(frappe, "get_roles", return_value=["Department Manager", "MRS Requestor"])
	def test_department_manager_sees_department_approval_shortcut(self, _roles):
		self.assertEqual(
			mrs_permissions._workspace_approval_shortcuts_for_user("manager@example.com"),
			{"MRS - For Department Manager Approval"},
		)

	def test_workspace_approval_visibility_client_script_is_loaded(self):
		app_root = Path(__file__).parents[1]
		hooks = (app_root / "hooks.py").read_text()
		client_script = (app_root / "public/js/workspace_approval_shortcuts.js").read_text()
		self.assertIn(
			'app_include_js = "/assets/ccpcerp/js/workspace_approval_shortcuts.js"',
			hooks,
		)
		self.assertIn("get_workspace_approval_shortcut_visibility", client_script)
		self.assertIn("shortcut-widget-box", client_script)

	def test_stockroom_expected_deliveries_home_shortcut(self):
		app_root = Path(__file__).parents[1]
		workspace = json.loads(
			(app_root / "ccpc_erp/workspace/ccpc_erp/ccpc_erp.json").read_text()
		)
		content_labels = {
			block["data"].get("shortcut_name")
			for block in json.loads(workspace["content"])
		}
		self.assertIn("MRS - Expected Deliveries", content_labels)
		shortcut = next(
			row for row in workspace["shortcuts"] if row["label"] == "MRS - Expected Deliveries"
		)
		self.assertEqual(shortcut["link_to"], "MRS")
		self.assertEqual(shortcut["doc_view"], "List")
		self.assertEqual(
			json.loads(shortcut["stats_filter"]),
			{
				"procurement_status": [
					"in",
					[
						mrs_procurement.PURCHASE_ORDER_SUBMITTED,
						mrs_procurement.RECEIVING_IN_PROGRESS,
						mrs_procurement.PARTIALLY_RECEIVED,
					],
				]
			},
		)

	@patch.object(frappe, "get_roles", return_value=["Stockroom Personnel", "MRS Requestor"])
	def test_stockroom_sees_expected_deliveries_shortcut(self, _roles):
		self.assertEqual(
			mrs_permissions._workspace_approval_shortcuts_for_user("stockroom@example.com"),
			{"MRS - Expected Deliveries"},
		)

	@patch.object(frappe.db, "escape", side_effect=lambda value: f"'{value}'")
	@patch.object(frappe, "get_roles", return_value=["Stockroom Personnel"])
	def test_stockroom_mrs_query_includes_delivery_stages(self, _roles, _escape):
		condition = mrs_permissions.get_mrs_permission_query_conditions("stockroom@example.com")
		self.assertIn("`tabMRS`.`procurement_status` in", condition)
		for status in mrs_permissions.STOCKROOM_DELIVERY_STATUSES:
			self.assertIn(status, condition)
		self.assertNotIn(mrs_procurement.FULLY_RECEIVED, condition)

	@patch.object(frappe, "get_roles", return_value=["Stockroom Personnel"])
	def test_stockroom_mrs_access_is_read_only_for_expected_deliveries(self, _roles):
		doc = frappe._dict(
			status="Ready for Purchasing",
			procurement_status=mrs_procurement.PURCHASE_ORDER_SUBMITTED,
			prepared_by="requestor@example.com",
			is_new=lambda: False,
		)
		self.assertTrue(
			mrs_permissions.has_mrs_permission(
				doc,
				user="stockroom@example.com",
				permission_type="read",
			)
		)
		self.assertFalse(
			mrs_permissions.has_mrs_permission(
				doc,
				user="stockroom@example.com",
				permission_type="write",
			)
		)
		doc.procurement_status = mrs_procurement.FULLY_RECEIVED
		self.assertFalse(
			mrs_permissions.has_mrs_permission(
				doc,
				user="stockroom@example.com",
				permission_type="read",
			)
		)

	def test_purchasing_accounting_permissions(self):
		for doctype in ("Purchase Invoice", "Payment Entry"):
			with self.subTest(doctype=doctype):
				rights = setup.PURCHASING_PERSONNEL_PERMISSIONS[doctype]
				self.assertTrue({"read", "write", "create", "submit", "print", "report"}.issubset(rights))
				self.assertNotIn("cancel", rights)
				self.assertNotIn("amend", rights)

		self.assertEqual(setup.PURCHASING_PERSONNEL_PERMISSIONS["Mode of Payment"], {"read"})
		self.assertEqual(setup.PURCHASING_PERSONNEL_PERMISSIONS["Buying Settings"], {"read"})

	def test_material_request_procurement_status(self):
		self.assertEqual(
			mrs_procurement._material_request_procurement_status(0),
			mrs_procurement.MATERIAL_REQUEST_DRAFT,
		)
		self.assertEqual(
			mrs_procurement._material_request_procurement_status(1),
			mrs_procurement.MATERIAL_REQUEST_SUBMITTED,
		)
		self.assertEqual(
			mrs_procurement._material_request_procurement_status(2),
			mrs_procurement.MATERIAL_REQUEST_CANCELLED,
		)

	def test_request_for_quotation_procurement_status(self):
		self.assertEqual(
			mrs_procurement._request_for_quotation_procurement_status([0, 2]),
			mrs_procurement.REQUEST_FOR_QUOTATION_DRAFT,
		)
		self.assertEqual(
			mrs_procurement._request_for_quotation_procurement_status([0, 1, 2]),
			mrs_procurement.REQUEST_FOR_QUOTATION_SUBMITTED,
		)
		self.assertEqual(
			mrs_procurement._request_for_quotation_procurement_status([2]),
			mrs_procurement.REQUEST_FOR_QUOTATION_CANCELLED,
		)
		self.assertIsNone(mrs_procurement._request_for_quotation_procurement_status([]))

	@patch.object(frappe, "get_all")
	def test_rfq_resolves_single_mrs_provenance(self, get_all):
		get_all.return_value = [frappe._dict(name="MAT-MR-1", mrs="MRS-1")]
		self.assertEqual(
			mrs_procurement._resolve_mrs_from_material_requests({"MAT-MR-1"}),
			"MRS-1",
		)

	@patch.object(frappe, "get_all")
	def test_rfq_rejects_mixed_mrs_provenance(self, get_all):
		get_all.return_value = [
			frappe._dict(name="MAT-MR-1", mrs="MRS-1"),
			frappe._dict(name="MAT-MR-2", mrs="MRS-2"),
		]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_material_requests({"MAT-MR-1", "MAT-MR-2"})

	@patch.object(frappe, "get_all")
	def test_rfq_rejects_unlinked_item_mixed_with_mrs(self, get_all):
		get_all.return_value = [frappe._dict(name="MAT-MR-1", mrs="MRS-1")]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_material_requests(
				{"MAT-MR-1"},
				has_unlinked_items=True,
			)

	def test_supplier_quotation_procurement_status(self):
		self.assertEqual(
			mrs_procurement._supplier_quotation_procurement_status([0, 2]),
			mrs_procurement.SUPPLIER_QUOTATION_DRAFT,
		)
		self.assertEqual(
			mrs_procurement._supplier_quotation_procurement_status([0, 1, 2]),
			mrs_procurement.SUPPLIER_QUOTATION_RECEIVED,
		)
		self.assertIsNone(mrs_procurement._supplier_quotation_procurement_status([2]))
		self.assertIsNone(mrs_procurement._supplier_quotation_procurement_status([]))

	@patch.object(frappe, "get_all")
	def test_supplier_quotation_resolves_matching_rfq_and_mr_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="RFQ-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-1", mrs="MRS-1")],
		]
		self.assertEqual(
			mrs_procurement._resolve_mrs_from_procurement_sources(
				{"RFQ-1"},
				{"MAT-MR-1"},
			),
			"MRS-1",
		)

	@patch.object(frappe, "get_all")
	def test_supplier_quotation_rejects_mixed_mrs_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="RFQ-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-2", mrs="MRS-2")],
		]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_procurement_sources(
				{"RFQ-1"},
				{"MAT-MR-2"},
			)

	@patch.object(frappe, "get_all")
	def test_supplier_quotation_rejects_unlinked_item_mixed_with_mrs(self, get_all):
		get_all.return_value = [frappe._dict(name="RFQ-1", mrs="MRS-1")]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_procurement_sources(
				{"RFQ-1"},
				set(),
				has_unlinked_items=True,
			)

	def test_purchase_order_procurement_status(self):
		self.assertEqual(
			mrs_procurement._purchase_order_procurement_status([0, 2]),
			mrs_procurement.PURCHASE_ORDER_DRAFT,
		)
		self.assertEqual(
			mrs_procurement._purchase_order_procurement_status([0, 1, 2]),
			mrs_procurement.PURCHASE_ORDER_SUBMITTED,
		)
		self.assertIsNone(mrs_procurement._purchase_order_procurement_status([2]))
		self.assertIsNone(mrs_procurement._purchase_order_procurement_status([]))

	@patch.object(frappe, "get_all")
	def test_purchase_order_resolves_matching_sq_and_mr_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="SQ-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-1", mrs="MRS-1")],
		]
		self.assertEqual(
			mrs_procurement._resolve_mrs_from_purchase_order_sources(
				{"SQ-1"},
				{"MAT-MR-1"},
			),
			"MRS-1",
		)

	@patch.object(frappe, "get_all")
	def test_purchase_order_rejects_mixed_mrs_provenance(self, get_all):
		get_all.side_effect = [
			[frappe._dict(name="SQ-1", mrs="MRS-1")],
			[frappe._dict(name="MAT-MR-2", mrs="MRS-2")],
		]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_purchase_order_sources(
				{"SQ-1"},
				{"MAT-MR-2"},
			)

	@patch.object(frappe, "get_all")
	def test_purchase_order_rejects_unlinked_item_mixed_with_mrs(self, get_all):
		get_all.return_value = [frappe._dict(name="SQ-1", mrs="MRS-1")]
		with self.assertRaises(frappe.ValidationError):
			mrs_procurement._resolve_mrs_from_purchase_order_sources(
				{"SQ-1"},
				set(),
				has_unlinked_items=True,
			)

	@patch.object(frappe, "sendmail")
	@patch.object(mrs_procurement, "_get_procurement_email_sender", return_value=None)
	def test_purchase_receipt_email_skips_when_no_outgoing_account(self, _sender, sendmail):
		doc = frappe._dict(
			doctype="Purchase Receipt",
			name="MAT-PRE-1",
			mrs="MRS-1",
			docstatus=1,
			is_return=0,
		)
		mrs_procurement.notify_mrs_requestor_of_receipt(doc)
		sendmail.assert_not_called()

	@patch.object(frappe, "sendmail")
	@patch.object(
		mrs_procurement,
		"_build_purchase_receipt_requestor_email",
		return_value=("Receipt subject", "Receipt message"),
	)
	@patch.object(mrs_procurement, "_get_procurement_email_sender", return_value="purchasing@example.com")
	@patch.object(frappe.db, "get_value")
	def test_purchase_receipt_email_targets_mrs_requestor(
		self,
		get_value,
		_sender,
		_build_email,
		sendmail,
	):
		get_value.side_effect = [
			frappe._dict(
				prepared_by="requestor@example.com",
				prepared_by_name="Requestor Name",
				procurement_status=mrs_procurement.FULLY_RECEIVED,
			),
			"requestor@example.com",
		]
		doc = frappe._dict(
			doctype="Purchase Receipt",
			name="MAT-PRE-1",
			mrs="MRS-1",
			docstatus=1,
			is_return=0,
		)

		mrs_procurement.notify_mrs_requestor_of_receipt(doc)

		sendmail.assert_called_once()
		kwargs = sendmail.call_args.kwargs
		self.assertEqual(kwargs["recipients"], ["requestor@example.com"])
		self.assertEqual(kwargs["sender"], "purchasing@example.com")
		self.assertEqual(kwargs["reference_doctype"], "Purchase Receipt")
		self.assertEqual(kwargs["reference_name"], "MAT-PRE-1")

	@patch.object(frappe, "sendmail")
	@patch.object(mrs_procurement, "_get_procurement_email_sender", return_value="purchasing@example.com")
	def test_purchase_return_does_not_send_requestor_receipt_email(self, _sender, sendmail):
		doc = frappe._dict(
			doctype="Purchase Receipt",
			name="MAT-PR-RET-1",
			mrs="MRS-1",
			docstatus=1,
			is_return=1,
		)
		mrs_procurement.notify_mrs_requestor_of_receipt(doc)
		sendmail.assert_not_called()

	def test_purchase_receipt_email_content_targets_requestor_context(self):
		doc = frappe._dict(
			doctype="Purchase Receipt",
			name="MAT-PRE-1",
			mrs="MRS-1",
			supplier="SUP-1",
			supplier_name="Supplier One",
			items=[
				frappe._dict(
					item_code="ITEM-1",
					item_name="Bearing",
					qty=2,
					rejected_qty=1,
					uom="Nos",
					warehouse="Warehouse 1",
				)
			],
		)
		mrs = frappe._dict(prepared_by="requestor@example.com", prepared_by_name="Requestor Name")
		subject, message = mrs_procurement._build_purchase_receipt_requestor_email(
			doc,
			mrs,
			mrs_procurement.PARTIALLY_RECEIVED,
		)
		self.assertIn("MRS-1", subject)
		self.assertIn("Requestor Name", message)
		self.assertIn("Partially Received", message)
		self.assertIn("Bearing", message)
		self.assertIn("rejected 1 Nos", message)

	@patch.object(frappe, "get_roles", return_value=["Purchasing Personnel"])
	def test_purchasing_personnel_is_allowed(self, _get_roles):
		mrs_procurement._require_purchasing_role("purchasing@example.com")

	@patch.object(frappe, "get_roles", return_value=["Employee"])
	def test_non_purchasing_user_is_rejected(self, _get_roles):
		with self.assertRaises(frappe.PermissionError):
			mrs_procurement._require_purchasing_role("employee@example.com")

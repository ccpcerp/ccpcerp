# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe.permissions import add_permission
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import flt


MAINTENANCE_SUPERVISOR_ROLE = "Maintenance Supervisor"
MAINTENANCE_SUPERVISOR_REFERENCE_DOCTYPES = ("Company", "Department", "Warehouse", "Item", "UOM")
MAINTENANCE_SUPERVISOR_PERMISSIONS = {
	"Material Request": {"read", "print", "report"},
}
STOCKROOM_PERSONNEL_ROLE = "Stockroom Personnel"
STOCKROOM_PERSONNEL_REFERENCE_DOCTYPES = ("Company", "Department", "Warehouse", "Item", "UOM", "Cost Center")
STOCKROOM_PERSONNEL_PERMISSIONS = {
	"Stock Settings": {"read"},
	"Buying Settings": {"read"},
	"Purchase Order": {"read", "print", "report"},
	"Purchase Receipt": {"read", "write", "create", "submit", "print", "report"},
	"Supplier": {"read"},
	"Supplier Group": {"read", "report"},
	"Price List": {"read", "report"},
	"Item": {"read"},
	"UOM": {"read"},
	"Warehouse": {"read"},
	"Company": {"read"},
	"Department": {"read"},
	"Account": {"read", "report"},
	"Cost Center": {"read"},
	"Equipment Registry": {"read", "print", "report"},
}
LEGACY_WITHDRAWAL_SLIP_STOCKROOM_ROLE = "Withdrawal Slip Stockroom"
WITHDRAWAL_SLIP_REQUESTOR_ROLE = "Withdrawal Slip Requestor"
WITHDRAWAL_SLIP_REQUESTOR_REFERENCE_DOCTYPES = (
	"Company",
	"Department",
	"Warehouse",
	"Item",
	"UOM",
	"Equipment Registry",
	"Job Order",
)
PURCHASING_PERSONNEL_ROLE = "Purchasing Personnel"
PURCHASING_SUPERVISOR_ROLE = "Purchasing Supervisor"
PURCHASE_ORDER_APPROVER_ROLE = "Purchase Order Approver"
PURCHASE_ORDER_APPROVER_USERS = ("finance_controller@ccpc.com",)
PURCHASE_ORDER_WORKFLOW = "CCPC Purchase Order Approval"
MRS_REQUESTOR_ROLE = "MRS Requestor"
DEPARTMENT_MANAGER_ROLE = "Department Manager"
MRS_MANAGEMENT_APPROVER_ROLE = "MRS Management Approver"
MRS_GM_SIGNATURE_RECORDER_ROLE = "MRS GM Signature Recorder"
MRS_ROLES = (
	MRS_REQUESTOR_ROLE,
	DEPARTMENT_MANAGER_ROLE,
	MRS_MANAGEMENT_APPROVER_ROLE,
	MRS_GM_SIGNATURE_RECORDER_ROLE,
)
OPERATIONAL_REQUESTOR_SOURCE_ROLES = (
	MAINTENANCE_SUPERVISOR_ROLE,
	STOCKROOM_PERSONNEL_ROLE,
	PURCHASING_PERSONNEL_ROLE,
	PURCHASING_SUPERVISOR_ROLE,
	DEPARTMENT_MANAGER_ROLE,
	MRS_MANAGEMENT_APPROVER_ROLE,
)
MRS_REFERENCE_DOCTYPES = (
	"Company",
	"Department",
	"Item",
	"UOM",
	"Equipment Registry",
	"Job Order",
	"Work Request",
)
MRS_WORKFLOW = "CCPC MRS Workflow"

MRS_WORKFLOW_STATES = (
	("Draft", MRS_REQUESTOR_ROLE),
	("Pending Requesting Department Manager", DEPARTMENT_MANAGER_ROLE),
	("Pending Use Department Manager", DEPARTMENT_MANAGER_ROLE),
	("Returned for Correction", MRS_REQUESTOR_ROLE),
	("Pending Plant Manager", MRS_MANAGEMENT_APPROVER_ROLE),
	("Pending COO", MRS_MANAGEMENT_APPROVER_ROLE),
	("Pending General Manager Signature", MRS_GM_SIGNATURE_RECORDER_ROLE),
	("Ready for Purchasing", "System Manager"),
	("Rejected", "System Manager"),
)

PURCHASE_ORDER_WORKFLOW_STATES = (
	("Draft", "0", PURCHASING_PERSONNEL_ROLE),
	("Pending Finance Approval", "0", "System Manager"),
	("Returned for Correction", "0", PURCHASING_PERSONNEL_ROLE),
	("Submitted", "1", "System Manager"),
	("Cancelled", "2", "System Manager"),
)

PURCHASE_ORDER_WORKFLOW_TRANSITIONS = (
	{
		"state": "Draft",
		"action": "Send for Finance Approval",
		"next_state": "Pending Finance Approval",
		"allowed": PURCHASING_PERSONNEL_ROLE,
		"allow_self_approval": 1,
	},
	{
		"state": "Pending Finance Approval",
		"action": "Return for Correction",
		"next_state": "Returned for Correction",
		"allowed": PURCHASE_ORDER_APPROVER_ROLE,
		"allow_self_approval": 1,
	},
	{
		"state": "Returned for Correction",
		"action": "Resubmit for Finance Approval",
		"next_state": "Pending Finance Approval",
		"allowed": PURCHASING_PERSONNEL_ROLE,
		"allow_self_approval": 1,
	},
	{
		"state": "Pending Finance Approval",
		"action": "Approve & Submit",
		"next_state": "Submitted",
		"allowed": PURCHASE_ORDER_APPROVER_ROLE,
		"allow_self_approval": 1,
	},
)


MRS_WORKFLOW_TRANSITIONS = (
	{
		"state": "Draft",
		"action": "Submit for Approval",
		"next_state": "Pending Requesting Department Manager",
		"allowed": MRS_REQUESTOR_ROLE,
		"allow_self_approval": 1,
	},
	{
		"state": "Pending Requesting Department Manager",
		"action": "Approve",
		"next_state": "Pending Plant Manager",
		"allowed": DEPARTMENT_MANAGER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.requesting_department_manager and doc.requesting_department == doc.use_department",
	},
	{
		"state": "Pending Requesting Department Manager",
		"action": "Approve",
		"next_state": "Pending Use Department Manager",
		"allowed": DEPARTMENT_MANAGER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.requesting_department_manager and doc.requesting_department != doc.use_department",
	},
	{
		"state": "Pending Requesting Department Manager",
		"action": "Return for Correction",
		"next_state": "Returned for Correction",
		"allowed": DEPARTMENT_MANAGER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.requesting_department_manager",
	},
	{
		"state": "Pending Requesting Department Manager",
		"action": "Reject",
		"next_state": "Rejected",
		"allowed": DEPARTMENT_MANAGER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.requesting_department_manager",
	},
	{
		"state": "Pending Use Department Manager",
		"action": "Approve",
		"next_state": "Pending Plant Manager",
		"allowed": DEPARTMENT_MANAGER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.use_department_manager",
	},
	{
		"state": "Pending Use Department Manager",
		"action": "Return for Correction",
		"next_state": "Returned for Correction",
		"allowed": DEPARTMENT_MANAGER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.use_department_manager",
	},
	{
		"state": "Pending Use Department Manager",
		"action": "Reject",
		"next_state": "Rejected",
		"allowed": DEPARTMENT_MANAGER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.use_department_manager",
	},
	{
		"state": "Returned for Correction",
		"action": "Resubmit for Approval",
		"next_state": "Pending Requesting Department Manager",
		"allowed": MRS_REQUESTOR_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.prepared_by",
	},
	{
		"state": "Pending Plant Manager",
		"action": "Approve",
		"next_state": "Pending COO",
		"allowed": MRS_MANAGEMENT_APPROVER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.plant_manager and not doc.bypass_other_management_approval",
	},
	{
		"state": "Pending Plant Manager",
		"action": "Approve & Bypass COO",
		"next_state": "Pending General Manager Signature",
		"allowed": MRS_MANAGEMENT_APPROVER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.plant_manager and doc.bypass_other_management_approval and doc.bypass_reason",
	},
	{
		"state": "Pending Plant Manager",
		"action": "Approve in Place of Plant Manager",
		"next_state": "Pending General Manager Signature",
		"allowed": MRS_MANAGEMENT_APPROVER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.coo and doc.bypass_other_management_approval and doc.bypass_reason",
	},
	{
		"state": "Pending Plant Manager",
		"action": "Reject",
		"next_state": "Rejected",
		"allowed": MRS_MANAGEMENT_APPROVER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.plant_manager",
	},
	{
		"state": "Pending Plant Manager",
		"action": "Reject in Place of Plant Manager",
		"next_state": "Rejected",
		"allowed": MRS_MANAGEMENT_APPROVER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.coo and doc.bypass_other_management_approval and doc.bypass_reason",
	},
	{
		"state": "Pending COO",
		"action": "Approve",
		"next_state": "Pending General Manager Signature",
		"allowed": MRS_MANAGEMENT_APPROVER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.coo",
	},
	{
		"state": "Pending COO",
		"action": "Reject",
		"next_state": "Rejected",
		"allowed": MRS_MANAGEMENT_APPROVER_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.coo",
	},
	{
		"state": "Pending General Manager Signature",
		"action": "Record General Manager Signature",
		"next_state": "Ready for Purchasing",
		"allowed": MRS_GM_SIGNATURE_RECORDER_ROLE,
		"allow_self_approval": 1,
		"condition": "doc.owner_signed and doc.owner_signed_date",
	},
)

PURCHASING_PERSONNEL_PERMISSIONS = {
	"Material Request": {"read", "write", "create", "submit", "print", "report"},
	"Request for Quotation": {"read", "write", "create", "submit", "print", "email", "report"},
	"Supplier Quotation": {"read", "write", "create", "submit", "print", "report"},
	"Purchase Order": {"read", "write", "create", "print", "report"},
	"Supplier": {"read"},
	"Supplier Group": {"read", "report"},
	"Price List": {"read", "report"},
	"Item": {"read"},
	"UOM": {"read"},
	"Warehouse": {"read"},
	"Company": {"read"},
	"Department": {"read"},
	"Account": {"read", "report"},
	"Cost Center": {"read"},
	"Equipment Registry": {"read", "print", "report"},
	"Purchase Receipt": {"read", "print", "report"},
	"Purchase Invoice": {"read", "write", "create", "submit", "print", "report"},
	"Payment Entry": {"read", "write", "create", "submit", "print", "report"},
	"Mode of Payment": {"read"},
	"Buying Settings": {"read"},
}
PURCHASING_SUPERVISOR_PERMISSIONS = {
	**PURCHASING_PERSONNEL_PERMISSIONS,
	"Purchase Order": {
		"read",
		"write",
		"create",
		"submit",
		"cancel",
		"amend",
		"print",
		"email",
		"report",
	},
}

PURCHASE_ORDER_APPROVER_PERMISSIONS = {
	"Purchase Order": {"read", "write", "submit", "print", "report"},
	"Material Request": {"read", "print", "report"},
	"Request for Quotation": {"read", "print", "report"},
	"Supplier Quotation": {"read", "print", "report"},
	"Supplier": {"read"},
	"Supplier Group": {"read", "report"},
	"Price List": {"read", "report"},
	"Item": {"read"},
	"UOM": {"read"},
	"Warehouse": {"read"},
	"Company": {"read"},
	"Department": {"read"},
	"Account": {"read", "report"},
	"Cost Center": {"read"},
	"Equipment Registry": {"read", "print", "report"},
	"Buying Settings": {"read"},
}
CUSTOM_DOCPERM_RIGHTS = (
	"read",
	"write",
	"create",
	"submit",
	"cancel",
	"delete",
	"amend",
	"report",
	"export",
	"import",
	"share",
	"print",
	"email",
	"select",
)
WITHDRAWAL_SLIP_WORKFLOW = "CCPC Withdrawal Slip Workflow"

WORKFLOW_STATES = (
	("Draft", WITHDRAWAL_SLIP_REQUESTOR_ROLE),
	("Approved", STOCKROOM_PERSONNEL_ROLE),
	("Stockroom Review", STOCKROOM_PERSONNEL_ROLE),
	("Returned for Correction", WITHDRAWAL_SLIP_REQUESTOR_ROLE),
	("Fully Issued", "Stockroom Personnel"),
	("Issued with Shortage", "Stockroom Personnel"),
	("Cancelled", "System Manager"),
	("Closed", "System Manager"),
)

WORKFLOW_TRANSITIONS = (
	{
		"state": "Draft",
		"action": "Approve & Send to Stockroom",
		"next_state": "Approved",
		"allowed": WITHDRAWAL_SLIP_REQUESTOR_ROLE,
		"allow_self_approval": 1,
	},
	{
		"state": "Approved",
		"action": "Start Review",
		"next_state": "Stockroom Review",
		"allowed": "Stockroom Personnel",
		"allow_self_approval": 1,
	},
	{
		"state": "Stockroom Review",
		"action": "Return for Correction",
		"next_state": "Returned for Correction",
		"allowed": "Stockroom Personnel",
		"allow_self_approval": 1,
	},
	{
		"state": "Returned for Correction",
		"action": "Re-Approve",
		"next_state": "Approved",
		"allowed": WITHDRAWAL_SLIP_REQUESTOR_ROLE,
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.approving_supervisor",
	},
)


def after_migrate():
	ensure_maintenance_supervisor_permissions()
	ensure_mrs_foundation()
	ensure_mrs_workflow()
	backfill_mrs_display_names()
	backfill_mrs_item_search_text()
	from ccpcerp.ccpc_erp.mrs_procurement import backfill_mrs_procurement_status
	backfill_mrs_procurement_status()
	ensure_stockroom_personnel_permissions()
	ensure_purchasing_personnel_permissions()
	ensure_purchasing_supervisor_permissions()
	ensure_purchase_order_approver_permissions()
	backfill_purchase_order_approval_status()
	ensure_purchase_order_approval_workflow()
	ensure_withdrawal_slip_requestor_permissions()
	ensure_withdrawal_slip_stock_entry_fields()
	ensure_withdrawal_slip_workflow()
	backfill_withdrawal_slip_issue_qty()





def ensure_mrs_foundation():
	"""Create CCPC's lightweight personnel/routing foundation and MRS access roles."""
	for role in MRS_ROLES + (PURCHASING_PERSONNEL_ROLE, PURCHASING_SUPERVISOR_ROLE, PURCHASE_ORDER_APPROVER_ROLE):
		_ensure_role(role)

	create_custom_fields(
		{
			"Department": [
				{
					"fieldname": "ccpc_department_manager",
					"label": "CCPC Department Manager",
					"fieldtype": "Link",
					"options": "CCPC Personnel",
					"insert_after": "parent_department",
				}
			],
			"Material Request": [
				{
					"fieldname": "mrs",
					"label": "MRS",
					"fieldtype": "Link",
					"options": "MRS",
					"insert_after": "work_request",
					"read_only": 1,
					"no_copy": 1,
					"unique": 1,
				}
			],
			"Request for Quotation": [
				{
					"fieldname": "mrs",
					"label": "MRS",
					"fieldtype": "Link",
					"options": "MRS",
					"insert_after": "company",
					"read_only": 1,
					"no_copy": 1,
				}
			],
			"Supplier Quotation": [
				{
					"fieldname": "mrs",
					"label": "MRS",
					"fieldtype": "Link",
					"options": "MRS",
					"insert_after": "company",
					"read_only": 1,
					"no_copy": 1,
				}
			],
			"Purchase Order": [
				{
					"fieldname": "mrs",
					"label": "MRS",
					"fieldtype": "Link",
					"options": "MRS",
					"insert_after": "company",
					"read_only": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "ccpc_approval_status",
					"label": "Approval Status",
					"fieldtype": "Select",
					"options": "\nDraft\nPending Finance Approval\nReturned for Correction\nSubmitted\nCancelled",
					"default": "Draft",
					"insert_after": "mrs",
					"read_only": 1,
					"no_copy": 1,
					"in_list_view": 1,
					"in_standard_filter": 1,
				}
			],
			"Purchase Receipt": [
				{
					"fieldname": "mrs",
					"label": "MRS",
					"fieldtype": "Link",
					"options": "MRS",
					"insert_after": "company",
					"read_only": 1,
					"no_copy": 1,
				}
			],
		}
	)

	mrs_permissions = {
		MRS_REQUESTOR_ROLE: {"read", "write", "create", "print", "report"},
		DEPARTMENT_MANAGER_ROLE: {"read", "write", "print", "report"},
		MRS_MANAGEMENT_APPROVER_ROLE: {"read", "write", "print", "report"},
		MRS_GM_SIGNATURE_RECORDER_ROLE: {"read", "write", "print", "report"},
		PURCHASING_PERSONNEL_ROLE: {"read", "print", "report"},
		PURCHASING_SUPERVISOR_ROLE: {"read", "print", "report"},
		PURCHASE_ORDER_APPROVER_ROLE: {"read", "print", "report"},
	}
	for role, rights in mrs_permissions.items():
		_ensure_custom_permission("MRS", role, rights)

	personnel_read_roles = set(MRS_ROLES) | {
		PURCHASING_PERSONNEL_ROLE,
		PURCHASING_SUPERVISOR_ROLE,
	}
	for role in personnel_read_roles:
		_ensure_custom_permission("CCPC Personnel", role, {"read", "report"})

	for role in MRS_ROLES:
		for doctype in MRS_REFERENCE_DOCTYPES:
			_ensure_custom_permission(doctype, role, {"read"})

	frappe.clear_cache(doctype="Department")
	frappe.clear_cache(doctype="MRS")
	frappe.clear_cache(doctype="Request for Quotation")
	frappe.clear_cache(doctype="Supplier Quotation")
	frappe.clear_cache(doctype="Purchase Order")
	frappe.clear_cache(doctype="Purchase Receipt")
	frappe.clear_cache(doctype="CCPC Personnel")


def ensure_mrs_workflow():
	for role in MRS_ROLES:
		_ensure_role(role)
	for state, _allow_edit in MRS_WORKFLOW_STATES:
		_ensure_named_master("Workflow State", state, "workflow_state_name")
	for transition in MRS_WORKFLOW_TRANSITIONS:
		_ensure_named_master(
			"Workflow Action Master",
			transition["action"],
			"workflow_action_name",
		)

	workflow = (
		frappe.get_doc("Workflow", MRS_WORKFLOW)
		if frappe.db.exists("Workflow", MRS_WORKFLOW)
		else frappe.new_doc("Workflow")
	)
	workflow.workflow_name = MRS_WORKFLOW
	workflow.document_type = "MRS"
	workflow.is_active = 1
	workflow.override_status = 0
	workflow.send_email_alert = 0
	workflow.enable_action_confirmation = 1
	workflow.workflow_state_field = "status"

	workflow.set("states", [])
	for state, allow_edit in MRS_WORKFLOW_STATES:
		workflow.append(
			"states",
			{
				"state": state,
				"doc_status": "0",
				"allow_edit": allow_edit,
				"send_email": 0,
			}
		)

	workflow.set("transitions", [])
	for transition in MRS_WORKFLOW_TRANSITIONS:
		workflow.append("transitions", transition)

	if workflow.is_new():
		workflow.insert(ignore_permissions=True)
	else:
		workflow.save(ignore_permissions=True)

	frappe.clear_cache(doctype="MRS")
	frappe.cache.hdel("workflow", "MRS")


def backfill_mrs_display_names():
	"""Populate person-name snapshots without exposing User links on MRS forms."""
	if not frappe.db.table_exists("MRS"):
		return

	settings = frappe.get_single("CCPC Approval Settings")
	plant_personnel = _get_ccpc_personnel(settings.plant_manager)
	coo_personnel = _get_ccpc_personnel(settings.coo)

	for row in frappe.get_all(
		"MRS",
		fields=[
			"name",
			"prepared_by",
			"requesting_department",
			"use_department",
			"requesting_department_manager",
			"use_department_manager",
			"plant_manager",
			"coo",
			"management_bypass_by",
			"owner_signed_recorded_by",
		],
	):
		requesting_manager = _get_department_manager_personnel(row.requesting_department)
		use_manager = (
			_get_department_manager_personnel(row.use_department)
			if row.use_department and row.use_department != row.requesting_department
			else frappe._dict()
		)
		values = {
			"prepared_by_name": _get_personnel_name_by_user(row.prepared_by),
			"requesting_department_manager_name": _get_personnel_name_by_user(
				row.requesting_department_manager
			) or requesting_manager.get("personnel_name"),
			"use_department_manager_name": _get_personnel_name_by_user(
				row.use_department_manager
			) or use_manager.get("personnel_name"),
			"plant_manager_name": _get_personnel_name_by_user(row.plant_manager)
			or plant_personnel.get("personnel_name"),
			"coo_name": _get_personnel_name_by_user(row.coo) or coo_personnel.get("personnel_name"),
			"management_bypass_by_name": _get_personnel_name_by_user(row.management_bypass_by),
			"owner_signed_recorded_by_name": _get_personnel_name_by_user(row.owner_signed_recorded_by),
		}
		frappe.db.set_value("MRS", row.name, values, update_modified=False)

	frappe.clear_cache(doctype="MRS")


def backfill_mrs_item_search_text():
	"""Build searchable parent text from existing MRS item snapshots."""
	if not frappe.db.table_exists("MRS") or not frappe.db.table_exists("MRS Item"):
		return

	from ccpcerp.ccpc_erp.doctype.mrs.mrs import _build_item_search_text

	items_by_parent = defaultdict(list)
	for row in frappe.get_all(
		"MRS Item",
		fields=["parent", "item_code", "item_name", "description"],
		order_by="parent asc, idx asc",
	):
		items_by_parent[row.parent].append(row)

	for name in frappe.get_all("MRS", pluck="name"):
		search_text = _build_item_search_text(items_by_parent.get(name, []))
		if frappe.db.get_value("MRS", name, "item_search_text") == search_text:
			continue
		frappe.db.set_value(
			"MRS",
			name,
			"item_search_text",
			search_text,
			update_modified=False,
		)

	frappe.clear_cache(doctype="MRS")


def _get_ccpc_personnel(personnel_name):
	if not personnel_name:
		return frappe._dict()
	return frappe.db.get_value(
		"CCPC Personnel",
		personnel_name,
		["user", "personnel_name", "active"],
		as_dict=True,
	) or frappe._dict()


def _get_personnel_name_by_user(user):
	if not user:
		return None
	return frappe.db.get_value(
		"CCPC Personnel",
		{"user": user, "active": 1},
		"personnel_name",
	) or frappe.db.get_value("User", user, "full_name")


def _get_department_manager_personnel(department):
	if not department:
		return frappe._dict()
	personnel_name = frappe.db.get_value("Department", department, "ccpc_department_manager")
	return _get_ccpc_personnel(personnel_name)


def ensure_maintenance_supervisor_permissions():
	"""Keep supervisor request visibility read-only while preserving required reference access."""
	_ensure_role(MAINTENANCE_SUPERVISOR_ROLE)

	for doctype in MAINTENANCE_SUPERVISOR_REFERENCE_DOCTYPES:
		filters = {
			"parent": doctype,
			"role": MAINTENANCE_SUPERVISOR_ROLE,
			"permlevel": 0,
			"if_owner": 0,
		}
		custom_perm = frappe.db.get_value("Custom DocPerm", filters, "name")
		if custom_perm:
			frappe.db.set_value("Custom DocPerm", custom_perm, "read", 1, update_modified=False)
		else:
			add_permission(doctype, MAINTENANCE_SUPERVISOR_ROLE, permlevel=0, ptype="read")

		frappe.clear_cache(doctype=doctype)

	for doctype, rights in MAINTENANCE_SUPERVISOR_PERMISSIONS.items():
		filters = {
			"parent": doctype,
			"role": MAINTENANCE_SUPERVISOR_ROLE,
			"permlevel": 0,
			"if_owner": 0,
		}
		custom_perm = frappe.db.get_value("Custom DocPerm", filters, "name")
		if not custom_perm:
			add_permission(doctype, MAINTENANCE_SUPERVISOR_ROLE, permlevel=0, ptype="read")
			custom_perm = frappe.db.get_value("Custom DocPerm", filters, "name")

		values = {
			right: 1 if right in rights else 0
			for right in CUSTOM_DOCPERM_RIGHTS
		}
		frappe.db.set_value(
			"Custom DocPerm",
			custom_perm,
			values,
			update_modified=False,
		)
		frappe.clear_cache(doctype=doctype)


def ensure_stockroom_personnel_permissions():
	"""Keep CCPC stockroom workflow access separate from ERPNext's standard Stock User role."""
	_ensure_role(STOCKROOM_PERSONNEL_ROLE)

	# Remove the temporary development permission previously added to Stock User.
	for name in frappe.get_all(
		"Custom DocPerm",
		filters={
			"parent": "Stock Settings",
			"role": "Stock User",
			"permlevel": 0,
			"read": 1,
			"write": 0,
			"create": 0,
			"delete": 0,
			"submit": 0,
			"cancel": 0,
		},
		pluck="name",
	):
		frappe.delete_doc("Custom DocPerm", name, ignore_permissions=True, force=True)

	# Remove permissions created for the legacy custom role without deleting the role itself.
	for doctype in ("Stock Settings",) + STOCKROOM_PERSONNEL_REFERENCE_DOCTYPES:
		for name in frappe.get_all(
			"Custom DocPerm",
			filters={
				"parent": doctype,
				"role": LEGACY_WITHDRAWAL_SLIP_STOCKROOM_ROLE,
			},
			pluck="name",
		):
			frappe.delete_doc("Custom DocPerm", name, ignore_permissions=True, force=True)

	for doctype, rights in STOCKROOM_PERSONNEL_PERMISSIONS.items():
		_ensure_custom_permission(doctype, STOCKROOM_PERSONNEL_ROLE, rights)


def ensure_purchasing_personnel_permissions():
	"""Create CCPC's operational purchasing role without granting broad Purchase User rights."""
	_ensure_role(PURCHASING_PERSONNEL_ROLE)

	for doctype, rights in PURCHASING_PERSONNEL_PERMISSIONS.items():
		filters = {
			"parent": doctype,
			"role": PURCHASING_PERSONNEL_ROLE,
			"permlevel": 0,
			"if_owner": 0,
		}
		custom_perm = frappe.db.get_value("Custom DocPerm", filters, "name")
		if not custom_perm:
			add_permission(doctype, PURCHASING_PERSONNEL_ROLE, permlevel=0, ptype="read")
			custom_perm = frappe.db.get_value("Custom DocPerm", filters, "name")

		values = {
			right: 1 if right in rights else 0
			for right in CUSTOM_DOCPERM_RIGHTS
		}
		frappe.db.set_value(
			"Custom DocPerm",
			custom_perm,
			values,
			update_modified=False,
		)
		frappe.clear_cache(doctype=doctype)


def ensure_purchasing_supervisor_permissions():
	"""Create CCPC's purchasing approval role without stock-receiving or accounting-posting access."""
	_ensure_role(PURCHASING_SUPERVISOR_ROLE)

	for doctype, rights in PURCHASING_SUPERVISOR_PERMISSIONS.items():
		filters = {
			"parent": doctype,
			"role": PURCHASING_SUPERVISOR_ROLE,
			"permlevel": 0,
			"if_owner": 0,
		}
		custom_perm = frappe.db.get_value("Custom DocPerm", filters, "name")
		if not custom_perm:
			add_permission(doctype, PURCHASING_SUPERVISOR_ROLE, permlevel=0, ptype="read")
			custom_perm = frappe.db.get_value("Custom DocPerm", filters, "name")

		values = {
			right: 1 if right in rights else 0
			for right in CUSTOM_DOCPERM_RIGHTS
		}
		frappe.db.set_value(
			"Custom DocPerm",
			custom_perm,
			values,
			update_modified=False,
		)
		frappe.clear_cache(doctype=doctype)


def ensure_purchase_order_approver_permissions():
	"""Grant Finance a narrow PO approval role without purchasing execution or accounting posting rights."""
	_ensure_role(PURCHASE_ORDER_APPROVER_ROLE)
	for doctype, rights in PURCHASE_ORDER_APPROVER_PERMISSIONS.items():
		_ensure_custom_permission(doctype, PURCHASE_ORDER_APPROVER_ROLE, rights)


def backfill_purchase_order_approval_status():
	"""Initialize workflow state for Purchase Orders created before the CCPC approval workflow."""
	if not frappe.db.table_exists("Purchase Order"):
		return

	state_by_docstatus = {0: "Draft", 1: "Submitted", 2: "Cancelled"}
	for row in frappe.get_all(
		"Purchase Order",
		fields=["name", "docstatus", "ccpc_approval_status"],
	):
		if row.ccpc_approval_status:
			continue
		state = state_by_docstatus.get(int(row.docstatus))
		if state:
			frappe.db.set_value(
				"Purchase Order",
				row.name,
				"ccpc_approval_status",
				state,
				update_modified=False,
			)

	frappe.clear_cache(doctype="Purchase Order")


def ensure_purchase_order_approval_workflow():
	"""Require Finance approval for PO submission while returning corrections to Purchasing."""
	_ensure_role(PURCHASING_PERSONNEL_ROLE)
	_ensure_role(PURCHASE_ORDER_APPROVER_ROLE)

	for state, _docstatus, _allow_edit in PURCHASE_ORDER_WORKFLOW_STATES:
		_ensure_named_master("Workflow State", state, "workflow_state_name")
	for transition in PURCHASE_ORDER_WORKFLOW_TRANSITIONS:
		_ensure_named_master(
			"Workflow Action Master",
			transition["action"],
			"workflow_action_name",
		)

	other_active_workflow = frappe.db.get_value(
		"Workflow",
		{
			"document_type": "Purchase Order",
			"is_active": 1,
			"name": ["!=", PURCHASE_ORDER_WORKFLOW],
		},
		"name",
	)
	if other_active_workflow:
		frappe.throw(
			f"Cannot enable {PURCHASE_ORDER_WORKFLOW}: Purchase Order already uses active workflow {other_active_workflow}."
		)

	workflow = (
		frappe.get_doc("Workflow", PURCHASE_ORDER_WORKFLOW)
		if frappe.db.exists("Workflow", PURCHASE_ORDER_WORKFLOW)
		else frappe.new_doc("Workflow")
	)
	workflow.workflow_name = PURCHASE_ORDER_WORKFLOW
	workflow.document_type = "Purchase Order"
	workflow.is_active = 1
	workflow.override_status = 0
	workflow.send_email_alert = 0
	workflow.enable_action_confirmation = 1
	workflow.workflow_state_field = "ccpc_approval_status"

	workflow.set("states", [])
	for state, docstatus, allow_edit in PURCHASE_ORDER_WORKFLOW_STATES:
		workflow.append(
			"states",
			{
				"state": state,
				"doc_status": docstatus,
				"allow_edit": allow_edit,
				"send_email": 0,
			}
		)

	workflow.set("transitions", [])
	for transition in PURCHASE_ORDER_WORKFLOW_TRANSITIONS:
		workflow.append("transitions", transition)

	if workflow.is_new():
		workflow.insert(ignore_permissions=True)
	else:
		workflow.save(ignore_permissions=True)

	frappe.clear_cache(doctype="Purchase Order")
	frappe.cache.hdel("workflow", "Purchase Order")


def ensure_withdrawal_slip_requestor_permissions():
	"""Grant the dedicated requestor role only the references needed to prepare Withdrawal Slips."""
	_ensure_role(WITHDRAWAL_SLIP_REQUESTOR_ROLE)
	for doctype in WITHDRAWAL_SLIP_REQUESTOR_REFERENCE_DOCTYPES:
		_ensure_custom_permission(doctype, WITHDRAWAL_SLIP_REQUESTOR_ROLE, {"read"})


def ensure_withdrawal_slip_stock_entry_fields():
	create_custom_fields(
		{
			"Stock Entry": [
				{
					"fieldname": "withdrawal_slip",
					"label": "Withdrawal Slip",
					"fieldtype": "Link",
					"options": "Withdrawal Slip",
					"insert_after": "purchase_order",
					"read_only": 1,
					"no_copy": 1,
				},
				{
					"fieldname": "withdrawal_slip_issued_by",
					"label": "Withdrawal Slip Issued By",
					"fieldtype": "Link",
					"options": "User",
					"insert_after": "withdrawal_slip",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
				},
			],
			"Stock Entry Detail": [
				{
					"fieldname": "withdrawal_slip_item",
					"label": "Withdrawal Slip Item",
					"fieldtype": "Data",
					"insert_after": "material_request_item",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
				},
			],
		}
	)


def ensure_withdrawal_slip_workflow():
	_ensure_role(MAINTENANCE_SUPERVISOR_ROLE)
	_ensure_role(WITHDRAWAL_SLIP_REQUESTOR_ROLE)
	_ensure_role(STOCKROOM_PERSONNEL_ROLE)
	for state, _allow_edit in WORKFLOW_STATES:
		_ensure_named_master("Workflow State", state, "workflow_state_name")
	for transition in WORKFLOW_TRANSITIONS:
		_ensure_named_master(
			"Workflow Action Master",
			transition["action"],
			"workflow_action_name",
		)

	workflow = (
		frappe.get_doc("Workflow", WITHDRAWAL_SLIP_WORKFLOW)
		if frappe.db.exists("Workflow", WITHDRAWAL_SLIP_WORKFLOW)
		else frappe.new_doc("Workflow")
	)
	workflow.workflow_name = WITHDRAWAL_SLIP_WORKFLOW
	workflow.document_type = "Withdrawal Slip"
	workflow.is_active = 1
	workflow.override_status = 0
	workflow.send_email_alert = 0
	workflow.enable_action_confirmation = 1
	workflow.workflow_state_field = "status"

	workflow.set("states", [])
	for state, allow_edit in WORKFLOW_STATES:
		workflow.append(
			"states",
			{
				"state": state,
				"doc_status": "0",
				"allow_edit": allow_edit,
				"send_email": 0,
			},
		)

	workflow.set("transitions", [])
	for transition in WORKFLOW_TRANSITIONS:
		workflow.append("transitions", transition)

	if workflow.is_new():
		workflow.insert(ignore_permissions=True)
	else:
		workflow.save(ignore_permissions=True)

	frappe.clear_cache(doctype="Withdrawal Slip")
	frappe.cache.hdel("workflow", "Withdrawal Slip")


def backfill_withdrawal_slip_issue_qty():
	"""Initialize Issue Qty for pre-existing review/issued slips created before the field existed."""
	rows = frappe.get_all(
		"Withdrawal Slip Item",
		filters={"issue_qty": ["<=", 0]},
		fields=["name", "parent", "requested_qty", "issued_qty"],
	)
	if not rows:
		return

	parent_names = list({row.parent for row in rows})
	status_by_parent = {
		row.name: row.status
		for row in frappe.get_all(
			"Withdrawal Slip",
			filters={"name": ["in", parent_names]},
			fields=["name", "status"],
		)
	}

	for row in rows:
		status = status_by_parent.get(row.parent)
		if status == "Stockroom Review":
			issue_qty = flt(row.requested_qty)
		elif status in {"Fully Issued", "Issued with Shortage"}:
			issue_qty = flt(row.issued_qty) or flt(row.requested_qty)
		else:
			continue

		if issue_qty > 0:
			frappe.db.set_value(
				"Withdrawal Slip Item",
				row.name,
				"issue_qty",
				issue_qty,
				update_modified=False,
			)



def _ensure_custom_permission(doctype, role, rights):
	filters = {
		"parent": doctype,
		"role": role,
		"permlevel": 0,
		"if_owner": 0,
	}
	custom_perm = frappe.db.get_value("Custom DocPerm", filters, "name")
	if not custom_perm:
		add_permission(doctype, role, permlevel=0, ptype="read")
		custom_perm = frappe.db.get_value("Custom DocPerm", filters, "name")

	values = {right: 1 if right in rights else 0 for right in CUSTOM_DOCPERM_RIGHTS}
	frappe.db.set_value(
		"Custom DocPerm",
		custom_perm,
		values,
		update_modified=False,
	)
	frappe.clear_cache(doctype=doctype)


def _ensure_role(role_name):
	if frappe.db.exists("Role", role_name):
		return
	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": role_name,
			"desk_access": 1,
			"is_custom": 1,
		}
	).insert(ignore_permissions=True)


def _ensure_named_master(doctype, name, fieldname):
	if frappe.db.exists(doctype, name):
		return
	frappe.get_doc(
		{
			"doctype": doctype,
			fieldname: name,
		}
	).insert(ignore_permissions=True)

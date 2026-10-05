# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

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
LEGACY_WITHDRAWAL_SLIP_STOCKROOM_ROLE = "Withdrawal Slip Stockroom"
PURCHASING_PERSONNEL_ROLE = "Purchasing Personnel"
PURCHASING_SUPERVISOR_ROLE = "Purchasing Supervisor"
PURCHASING_PERSONNEL_PERMISSIONS = {
	"Material Request": {"read", "print", "report"},
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
	"Purchase Invoice": {"read", "print", "report"},
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
	("Draft", "Maintenance Supervisor"),
	("Approved", "Stockroom Personnel"),
	("Stockroom Review", "Stockroom Personnel"),
	("Returned for Correction", "Maintenance Supervisor"),
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
		"allowed": "Maintenance Supervisor",
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
		"allowed": "Maintenance Supervisor",
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.approving_supervisor",
	},
)


def after_migrate():
	ensure_maintenance_supervisor_permissions()
	ensure_stockroom_personnel_permissions()
	ensure_purchasing_personnel_permissions()
	ensure_purchasing_supervisor_permissions()
	ensure_withdrawal_slip_stock_entry_fields()
	ensure_withdrawal_slip_workflow()
	backfill_withdrawal_slip_issue_qty()




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

	for doctype in ("Stock Settings",) + STOCKROOM_PERSONNEL_REFERENCE_DOCTYPES:
		filters = {
			"parent": doctype,
			"role": STOCKROOM_PERSONNEL_ROLE,
			"permlevel": 0,
			"if_owner": 0,
		}
		custom_perm = frappe.db.get_value("Custom DocPerm", filters, "name")
		if custom_perm:
			frappe.db.set_value("Custom DocPerm", custom_perm, "read", 1, update_modified=False)
		else:
			add_permission(doctype, STOCKROOM_PERSONNEL_ROLE, permlevel=0, ptype="read")

		frappe.clear_cache(doctype=doctype)

	# Purchase Receipts carry accounting references that Stock User can transact on but cannot read.
	account_filters = {
		"parent": "Account",
		"role": STOCKROOM_PERSONNEL_ROLE,
		"permlevel": 0,
		"if_owner": 0,
	}
	account_perm = frappe.db.get_value("Custom DocPerm", account_filters, "name")
	if account_perm:
		frappe.db.set_value(
			"Custom DocPerm",
			account_perm,
			{"read": 1, "report": 1},
			update_modified=False,
		)
	else:
		add_permission("Account", STOCKROOM_PERSONNEL_ROLE, permlevel=0, ptype="read")
		account_perm = frappe.db.get_value("Custom DocPerm", account_filters, "name")
		frappe.db.set_value(
			"Custom DocPerm",
			account_perm,
			"report",
			1,
			update_modified=False,
		)
	frappe.clear_cache(doctype="Account")


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

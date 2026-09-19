# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


WITHDRAWAL_SLIP_SUPERVISOR_ROLE = "Withdrawal Slip Supervisor"
WITHDRAWAL_SLIP_WORKFLOW = "CCPC Withdrawal Slip Workflow"

WORKFLOW_STATES = (
	("Draft", "Withdrawal Slip Supervisor"),
	("Approved", "Stock User"),
	("Stockroom Review", "Stock User"),
	("Returned for Correction", "Withdrawal Slip Supervisor"),
	("Partially Issued", "Stock User"),
	("Fully Issued", "Stock User"),
	("Cancelled", "System Manager"),
	("Closed", "System Manager"),
)

WORKFLOW_TRANSITIONS = (
	{
		"state": "Draft",
		"action": "Approve & Send to Stockroom",
		"next_state": "Approved",
		"allowed": "Withdrawal Slip Supervisor",
		"allow_self_approval": 1,
	},
	{
		"state": "Approved",
		"action": "Start Review",
		"next_state": "Stockroom Review",
		"allowed": "Stock User",
		"allow_self_approval": 1,
	},
	{
		"state": "Stockroom Review",
		"action": "Return for Correction",
		"next_state": "Returned for Correction",
		"allowed": "Stock User",
		"allow_self_approval": 1,
	},
	{
		"state": "Returned for Correction",
		"action": "Re-Approve",
		"next_state": "Approved",
		"allowed": "Withdrawal Slip Supervisor",
		"allow_self_approval": 1,
		"condition": "frappe.session.user == doc.approving_supervisor",
	},
)


def after_migrate():
	ensure_withdrawal_slip_stock_entry_fields()
	ensure_withdrawal_slip_workflow()



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
	_ensure_role(WITHDRAWAL_SLIP_SUPERVISOR_ROLE)
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

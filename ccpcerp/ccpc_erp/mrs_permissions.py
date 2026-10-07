# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe


WORKSPACE_APPROVAL_SHORTCUTS = {
	"department_manager": "MRS - For Department Manager Approval",
	"plant_manager": "MRS - For Plant Manager Approval",
	"coo": "MRS - For Chief Operating Officer Approval",
	"purchase_order_approver": "PO for Approval",
	"stockroom_expected_deliveries": "MRS - Expected Deliveries",
}
ALL_WORKSPACE_APPROVAL_SHORTCUTS = set(WORKSPACE_APPROVAL_SHORTCUTS.values())
STOCKROOM_DELIVERY_STATUSES = {
	"Purchase Order Submitted",
	"Receiving in Progress",
	"Partially Received",
}



@frappe.whitelist()
def get_workspace_approval_shortcut_visibility():
	"""Return the approval shortcuts relevant to the logged-in user's responsibilities."""
	return {
		"allowed": sorted(_workspace_approval_shortcuts_for_user(frappe.session.user)),
		"managed": sorted(ALL_WORKSPACE_APPROVAL_SHORTCUTS),
	}


def _workspace_approval_shortcuts_for_user(user):
	roles = set(frappe.get_roles(user))
	if user == "Administrator" or "System Manager" in roles:
		return set(ALL_WORKSPACE_APPROVAL_SHORTCUTS)

	allowed = set()
	if "Department Manager" in roles:
		allowed.add(WORKSPACE_APPROVAL_SHORTCUTS["department_manager"])
	if "Purchase Order Approver" in roles:
		allowed.add(WORKSPACE_APPROVAL_SHORTCUTS["purchase_order_approver"])
	if "Stockroom Personnel" in roles:
		allowed.add(WORKSPACE_APPROVAL_SHORTCUTS["stockroom_expected_deliveries"])

	if "MRS Management Approver" in roles:
		management_users = _management_user_map()
		if user == management_users.get("plant_manager"):
			allowed.add(WORKSPACE_APPROVAL_SHORTCUTS["plant_manager"])
		if user == management_users.get("coo"):
			allowed.add(WORKSPACE_APPROVAL_SHORTCUTS["coo"])

	return allowed


def _management_user_map():
	settings = frappe.get_single("CCPC Approval Settings")
	users = {}
	for stage, personnel_name in (
		("plant_manager", settings.plant_manager),
		("coo", settings.coo),
	):
		if personnel_name:
			users[stage] = frappe.db.get_value("CCPC Personnel", personnel_name, "user")
	return users


def get_mrs_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return None

	roles = set(frappe.get_roles(user))
	if "MRS Management Approver" in roles and user in _management_users():
		return None

	conditions = []
	user_sql = frappe.db.escape(user)
	if "MRS Requestor" in roles:
		conditions.append(f"`tabMRS`.`prepared_by` = {user_sql}")

	if "Department Manager" in roles:
		departments = _managed_departments(user)
		if departments:
			department_sql = ", ".join(frappe.db.escape(department) for department in departments)
			conditions.append(
				f"(`tabMRS`.`requesting_department` in ({department_sql}) or `tabMRS`.`use_department` in ({department_sql}))"
			)

	if "MRS GM Signature Recorder" in roles:
		conditions.append("`tabMRS`.`status` in ('Pending General Manager Signature', 'Ready for Purchasing')")

	if roles.intersection({"Purchasing Personnel", "Purchasing Supervisor", "Purchase Order Approver"}):
		conditions.append("`tabMRS`.`status` = 'Ready for Purchasing'")

	if "Stockroom Personnel" in roles:
		delivery_statuses = ", ".join(
			frappe.db.escape(status) for status in sorted(STOCKROOM_DELIVERY_STATUSES)
		)
		conditions.append(f"`tabMRS`.`procurement_status` in ({delivery_statuses})")

	return " or ".join(f"({condition})" for condition in conditions) if conditions else "1=0"


def has_mrs_permission(doc, user=None, permission_type=None):
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return True

	roles = set(frappe.get_roles(user))
	if getattr(doc, "is_new", lambda: False)() and "MRS Requestor" in roles:
		return True
	if permission_type == "write" and (doc.status or "Draft") in {"Draft", "Returned for Correction"}:
		return "MRS Requestor" in roles and doc.prepared_by == user
	if "MRS Management Approver" in roles and user in _management_users():
		return True
	if "MRS Requestor" in roles and doc.prepared_by == user:
		return True

	managed_departments = _managed_departments(user) if "Department Manager" in roles else set()
	if managed_departments and (
		doc.requesting_department in managed_departments or doc.use_department in managed_departments
	):
		return True
	if "MRS GM Signature Recorder" in roles and doc.status in {"Pending General Manager Signature", "Ready for Purchasing"}:
		return True
	if roles.intersection({"Purchasing Personnel", "Purchasing Supervisor", "Purchase Order Approver"}) and doc.status == "Ready for Purchasing":
		return True
	if (
		permission_type in (None, "read", "print", "report")
		and "Stockroom Personnel" in roles
		and doc.procurement_status in STOCKROOM_DELIVERY_STATUSES
	):
		return True
	return False


def _managed_departments(user):
	personnel = frappe.get_all("CCPC Personnel", filters={"user": user, "active": 1}, pluck="name")
	if not personnel:
		return set()
	return set(
		frappe.get_all(
			"Department",
			filters={"ccpc_department_manager": ["in", personnel]},
			pluck="name",
		)
	)


def _management_users():
	return {user for user in _management_user_map().values() if user}

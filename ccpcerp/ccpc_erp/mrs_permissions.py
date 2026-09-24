# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe


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

	if roles.intersection({"Purchasing Personnel", "Purchasing Supervisor"}):
		conditions.append("`tabMRS`.`status` = 'Ready for Purchasing'")

	return " or ".join(f"({condition})" for condition in conditions) if conditions else "1=0"


def has_mrs_permission(doc, user=None, permission_type=None):
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return True

	roles = set(frappe.get_roles(user))
	if getattr(doc, "is_new", lambda: False)() and "MRS Requestor" in roles:
		return True
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
	if roles.intersection({"Purchasing Personnel", "Purchasing Supervisor"}) and doc.status == "Ready for Purchasing":
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
	settings = frappe.get_single("CCPC Approval Settings")
	users = set()
	for personnel_name in (settings.plant_manager, settings.coo):
		if personnel_name:
			user = frappe.db.get_value("CCPC Personnel", personnel_name, "user")
			if user:
				users.add(user)
	return users

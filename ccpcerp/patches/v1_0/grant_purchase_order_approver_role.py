import frappe

from ccpcerp.setup import (
	PURCHASE_ORDER_APPROVER_ROLE,
	PURCHASE_ORDER_APPROVER_USERS,
	_ensure_role,
)


def execute():
	"""Assign the dedicated PO approval role to configured Finance Controller users."""
	_ensure_role(PURCHASE_ORDER_APPROVER_ROLE)

	for user in PURCHASE_ORDER_APPROVER_USERS:
		if not frappe.db.exists("User", user):
			continue
		user_doc = frappe.get_doc("User", user)
		if not user_doc.enabled:
			continue
		if PURCHASE_ORDER_APPROVER_ROLE in {row.role for row in user_doc.roles}:
			continue

		user_doc.append_roles(PURCHASE_ORDER_APPROVER_ROLE)
		user_doc.save(ignore_permissions=True)
		frappe.clear_cache(user=user)

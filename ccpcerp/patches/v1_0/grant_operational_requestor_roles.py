import frappe

from ccpcerp.setup import (
	MRS_REQUESTOR_ROLE,
	OPERATIONAL_REQUESTOR_SOURCE_ROLES,
	WITHDRAWAL_SLIP_REQUESTOR_ROLE,
	_ensure_role,
)


def execute():
	"""Preserve existing operational access while separating request creation from job authority."""
	_ensure_role(MRS_REQUESTOR_ROLE)
	_ensure_role(WITHDRAWAL_SLIP_REQUESTOR_ROLE)

	users = sorted(
		set(
			frappe.get_all(
				"Has Role",
				filters={
					"parenttype": "User",
					"role": ["in", list(OPERATIONAL_REQUESTOR_SOURCE_ROLES)],
				},
				pluck="parent",
			)
		)
	)
	for user in users:
		if user in {"Administrator", "Guest"}:
			continue
		user_doc = frappe.get_doc("User", user)
		if not user_doc.enabled:
			continue

		current_roles = {row.role for row in user_doc.roles}
		roles_to_add = [
			role
			for role in (MRS_REQUESTOR_ROLE, WITHDRAWAL_SLIP_REQUESTOR_ROLE)
			if role not in current_roles
		]
		if not roles_to_add:
			continue

		user_doc.append_roles(*roles_to_add)
		user_doc.save(ignore_permissions=True)
		frappe.clear_cache(user=user)

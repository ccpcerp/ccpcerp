# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe

from ccpcerp import setup


def filter_boot_desktop_icons(bootinfo, user=None):
	"""Show only the CCPC ERP desktop tile to Maintenance Supervisor users."""
	user = user or frappe.session.user
	if not _is_restricted_maintenance_supervisor(user):
		return

	bootinfo.desktop_icons = [
		icon
		for icon in (bootinfo.get("desktop_icons") or [])
		if icon.get("label") == setup.MAINTENANCE_SUPERVISOR_DEFAULT_WORKSPACE
	]


def _is_restricted_maintenance_supervisor(user):
	if not user or user in {"Administrator", "Guest"}:
		return False

	roles = set(frappe.get_roles(user))
	return (
		setup.MAINTENANCE_SUPERVISOR_ROLE in roles
		and "System Manager" not in roles
	)

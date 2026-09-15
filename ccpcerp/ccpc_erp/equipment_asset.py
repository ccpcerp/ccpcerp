import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime


SNAPSHOT_FIELDS = ("equipment_asset_assignment", "asset")


def get_current_equipment_asset(equipment, at_time=None):
	"""Return the assignment and Asset occupying Equipment at a point in time."""
	if not equipment:
		return (None, None)

	at_time = get_datetime(at_time or now_datetime())
	assignments = frappe.get_all(
		"Equipment Asset Assignment",
		filters={"equipment": equipment, "installed_on": ["<=", at_time]},
		fields=["name", "asset", "removed_on"],
		order_by="installed_on desc",
	)
	for assignment in assignments:
		if not assignment.removed_on or get_datetime(assignment.removed_on) > at_time:
			return (assignment.name, assignment.asset)
	return (None, None)


def set_new_equipment_snapshot(doc, equipment_context):
	"""Set an authoritative snapshot on a new Work Request or standalone Job Order."""
	if not doc.is_new():
		return
	assignment, asset = get_current_equipment_asset(doc.equipment) if equipment_context else (None, None)
	doc.equipment_asset_assignment = assignment
	doc.asset = asset


def validate_snapshot_immutable(doc):
	"""Prevent changes to snapshots after first save, including historical blanks."""
	if doc.is_new():
		return
	before = doc.get_doc_before_save()
	if not before:
		return
	for fieldname in SNAPSHOT_FIELDS:
		if doc.get(fieldname) != before.get(fieldname):
			frappe.throw(_("{0} cannot be changed after the document is saved.").format(
				frappe.bold(doc.meta.get_label(fieldname))
			))

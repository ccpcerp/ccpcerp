import frappe
from frappe import _


SUPPORTED_CHILD_TABLES = {
	"Material Request": "items",
	"Stock Entry": "items",
	"Purchase Invoice": "items",
	"Purchase Receipt": "items",
	"Journal Entry": "accounts",
}


def preserve_material_request_work_request(doc, method=None):
	"""Preserve immutable Work Request provenance without creating a reciprocal link."""
	if doc.doctype != "Material Request" or doc.docstatus == 2:
		return
	if doc.is_new():
		if doc.get("amended_from"):
			doc.work_request = frappe.get_cached_value(
				"Material Request", doc.amended_from, "work_request"
			)
		return

	before = doc.get_doc_before_save()
	if before and (before.get("work_request") or None) != (doc.get("work_request") or None):
		frappe.throw(_("Work Request provenance cannot be changed after the Material Request is saved."))


def set_equipment_defaults(doc, method=None):
	"""Set missing context only; never replace a populated Department or Equipment."""
	if doc.docstatus == 2:
		return
	rows = doc.get(SUPPORTED_CHILD_TABLES.get(doc.doctype, "")) or []
	for row in rows:
		if not (doc.is_new() or row.is_new() or _equipment_is_new_or_changed(doc, row)):
			continue
		if doc.doctype == "Material Request":
			if not row.get("equipment") and doc.get("equipment"):
				row.equipment = doc.equipment
			if not row.get("department") and doc.get("department"):
				row.department = doc.department
		set_department_from_equipment(doc, row)
	if doc.is_new() or _equipment_is_new_or_changed(doc, doc):
		set_department_from_equipment(doc)


def set_department_from_equipment(doc, row=None):
	"""Reconcile a new Equipment default while preserving a deliberate Department override."""
	if doc.docstatus == 2:
		return
	row = row or doc
	if not row.get("equipment"):
		return

	previous = _get_row_before_save(doc, row)
	previous_equipment = previous.get("equipment") if previous else None
	previous_department = _get_equipment_department(previous_equipment)
	if not row.get("department") or (
		previous_department and row.get("department") == previous_department
	):
		row.department = _get_equipment_department(row.equipment)


def _get_equipment_department(equipment):
	if not equipment:
		return None
	return frappe.get_cached_value("Equipment Registry", equipment, "department")


def _get_row_before_save(doc, row):
	if doc.is_new():
		return None
	before = doc.get_doc_before_save()
	if not before:
		return None
	if row is doc:
		return before
	return next((item for item in before.get(row.parentfield) or [] if item.name == row.name), None)


def validate_department_company(department, company, context=None, require_eligible=False):
	"""Validate Department ownership and, for new use, enabled leaf eligibility."""
	if not department:
		return
	values = frappe.get_cached_value(
		"Department", department, ["company", "disabled", "is_group"], as_dict=True
	)
	if not values:
		frappe.throw(_("Department {0} does not exist.").format(frappe.bold(department)))
	if not values.company:
		frappe.throw(_("Department {0} must belong to a Company.").format(frappe.bold(department)))
	if values.company != company:
		frappe.throw(
			_("{0}: Department {1} does not belong to Company {2}.").format(
				context or _("Document"), frappe.bold(department), frappe.bold(company)
			)
		)
	if require_eligible and values.disabled:
		frappe.throw(
			_("{0}: Department {1} is disabled and cannot be used.").format(
				context or _("Document"), frappe.bold(department)
			)
		)
	if require_eligible and values.is_group:
		frappe.throw(
			_("{0}: Department {1} is a group and cannot be used.").format(
				context or _("Document"), frappe.bold(department)
			)
		)
	return values


def validate_equipment(equipment, company, require_active=False, context=None):
	if not equipment:
		return
	values = frappe.get_cached_value(
		"Equipment Registry", equipment, ["company", "status", "department"], as_dict=True
	)
	if not values:
		frappe.throw(_("Equipment {0} does not exist.").format(frappe.bold(equipment)))
	if values.company != company:
		frappe.throw(
			_("{0}: Equipment {1} does not belong to Company {2}.").format(
				context or _("Document"), frappe.bold(equipment), frappe.bold(company)
			)
		)
	if require_active and values.status != "Active":
		frappe.throw(_("Equipment {0} must be Active for new usage.").format(frappe.bold(equipment)))
	if require_active:
		validate_department_company(
			values.department,
			company,
			context or _("Equipment {0}").format(frappe.bold(equipment)),
			require_eligible=True,
		)


def validate_equipment_costing(doc, method=None):
	"""Validate header context and accounting-dimension item rows."""
	if doc.docstatus == 2 or method == "on_cancel":
		return

	force_new_use = method == "before_submit"
	_validate_row(doc, doc, force_new_use=force_new_use)
	for row in doc.get(SUPPORTED_CHILD_TABLES.get(doc.doctype, "")) or []:
		_validate_row(doc, row, force_new_use=force_new_use)


def _validate_row(doc, row, force_new_use=False):
	equipment = row.get("equipment")
	department = row.get("department")
	context = _("{0} row {1}").format(doc.doctype, row.idx) if row is not doc else doc.doctype
	validate_equipment(
		equipment,
		doc.company,
		require_active=force_new_use or _equipment_is_new_or_changed(doc, row),
		context=context,
	)
	validate_department_company(
		department,
		doc.company,
		context,
		require_eligible=force_new_use or doc.docstatus == 0 or _department_is_new_or_changed(doc, row),
	)


def _equipment_is_new_or_changed(doc, row):
	if doc.is_new():
		return bool(row.get("equipment"))
	before = doc.get_doc_before_save()
	if not before:
		return bool(row.get("equipment"))
	if row is doc:
		return before.get("equipment") != row.get("equipment")
	old_rows = {item.name: item for item in before.get(row.parentfield) or []}
	old_row = old_rows.get(row.name)
	return not old_row or old_row.get("equipment") != row.get("equipment")


def _department_is_new_or_changed(doc, row):
	if doc.is_new():
		return bool(row.get("department"))
	before = doc.get_doc_before_save()
	if not before:
		return bool(row.get("department"))
	if before.company != doc.company:
		return bool(row.get("department"))
	if row is doc:
		return before.get("department") != row.get("department")
	old_rows = {item.name: item for item in before.get(row.parentfield) or []}
	old_row = old_rows.get(row.name)
	return not old_row or old_row.get("department") != row.get("department")

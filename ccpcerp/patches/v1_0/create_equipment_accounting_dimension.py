import frappe
from frappe import _

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
	get_doctypes_with_dimensions,
	make_dimension_in_accounting_doctypes,
)


DIMENSION_NAME = "Equipment"
DIMENSION_FIELDNAME = "equipment"
DIMENSION_DOCUMENT_TYPE = "Equipment Registry"


def execute():
	dimensions = frappe.get_all(
		"Accounting Dimension",
		fields=["name", "label", "fieldname", "document_type", "disabled"],
	)
	candidates = [dimension for dimension in dimensions if _matches_equipment_identity(dimension)]
	dimension = _validate_dimension_candidates(candidates)

	doctypes = get_doctypes_with_dimensions()
	missing_doctypes = _validate_generated_fields(doctypes)

	if dimension:
		_validate_dimension_defaults(dimension.name)
		if not missing_doctypes:
			return
		dimension_doc = frappe.get_doc("Accounting Dimension", dimension.name)
	else:
		dimension_doc = frappe.get_doc(
			{
				"doctype": "Accounting Dimension",
				"label": DIMENSION_NAME,
				"fieldname": DIMENSION_FIELDNAME,
				"document_type": DIMENSION_DOCUMENT_TYPE,
				"disabled": 0,
				"dimension_defaults": [],
			}
		)
		dimension_doc.insert(ignore_permissions=True)

	make_dimension_in_accounting_doctypes(doc=dimension_doc, doclist=doctypes)
	remaining = _validate_generated_fields(doctypes)
	if remaining:
		_fail(
			_("Equipment Accounting Dimension did not create fields on: {0}.").format(
				", ".join(remaining)
			)
		)


def _matches_equipment_identity(dimension):
	return any(
		(
			dimension.name == DIMENSION_NAME,
			dimension.label == DIMENSION_NAME,
			dimension.fieldname == DIMENSION_FIELDNAME,
			dimension.document_type == DIMENSION_DOCUMENT_TYPE,
		)
	)


def _validate_dimension_candidates(candidates):
	if not candidates:
		return None

	if len(candidates) != 1:
		_fail(_("Conflicting Accounting Dimensions exist for Equipment."))

	dimension = candidates[0]
	expected = {
		"name": DIMENSION_NAME,
		"label": DIMENSION_NAME,
		"fieldname": DIMENSION_FIELDNAME,
		"document_type": DIMENSION_DOCUMENT_TYPE,
		"disabled": 0,
	}
	conflicts = [field for field, value in expected.items() if dimension.get(field) != value]
	if conflicts:
		_fail(
			_("Accounting Dimension {0} conflicts with the required Equipment configuration: {1}.").format(
				dimension.name, ", ".join(conflicts)
			)
		)

	return dimension


def _validate_dimension_defaults(dimension_name):
	dimension = frappe.get_doc("Accounting Dimension", dimension_name)
	if dimension.get("dimension_defaults"):
		_fail(_("Equipment Accounting Dimension must not have Dimension Defaults or mandatory configuration."))


def _validate_generated_fields(doctypes):
	custom_fields = frappe.get_all(
		"Custom Field",
		filters={"fieldname": DIMENSION_FIELDNAME},
		fields=["name", "dt", "fieldname", "fieldtype", "options"],
	)
	by_doctype = {}
	for custom_field in custom_fields:
		by_doctype.setdefault(custom_field.dt, []).append(custom_field)

	missing = []
	for doctype in doctypes:
		fields = by_doctype.get(doctype, [])
		if len(fields) > 1:
			_fail(_("More than one equipment Custom Field exists on {0}.").format(doctype))
		if fields and (
			fields[0].fieldtype != "Link" or fields[0].options != DIMENSION_DOCUMENT_TYPE
		):
			_fail(
				_("Custom Field {0} conflicts with the Equipment Accounting Dimension.").format(
					fields[0].name
				)
			)

		field = frappe.get_meta(doctype, cached=False).get_field(DIMENSION_FIELDNAME)
		if not field:
			missing.append(doctype)
			continue
		if field.fieldtype != "Link" or field.options != DIMENSION_DOCUMENT_TYPE:
			_fail(
				_("Field {0}.{1} conflicts with the Equipment Accounting Dimension.").format(
					doctype, DIMENSION_FIELDNAME
				)
			)

	return missing


def _fail(message):
	frappe.throw(message, frappe.ValidationError)

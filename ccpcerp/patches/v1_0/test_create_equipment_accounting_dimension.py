from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ccpcerp.patches.v1_0 import create_equipment_accounting_dimension as dimension_patch


def dimension(**overrides):
	values = {
		"name": "Equipment",
		"label": "Equipment",
		"fieldname": "equipment",
		"document_type": "Equipment Registry",
		"disabled": 0,
	}
	values.update(overrides)
	return frappe._dict(values)


def link_field():
	return SimpleNamespace(fieldtype="Link", options="Equipment Registry")


def custom_field(doctype):
	return frappe._dict(
		name=f"{doctype}-equipment",
		dt=doctype,
		fieldname="equipment",
		fieldtype="Link",
		options="Equipment Registry",
	)


def raise_frappe_exception(message, exc=frappe.ValidationError):
	raise exc(message)


class Meta:
	def __init__(self, fields, doctype):
		self.fields = fields
		self.doctype = doctype

	def get_field(self, fieldname):
		return self.fields.get(self.doctype)


class TestEquipmentAccountingDimensionPatch(TestCase):
	def setUp(self):
		translation = patch.object(dimension_patch, "_", side_effect=lambda value: value)
		translation.start()
		self.addCleanup(translation.stop)
		throw = patch.object(dimension_patch.frappe, "throw", side_effect=raise_frappe_exception)
		throw.start()
		self.addCleanup(throw.stop)
		self.doctypes = ["GL Entry", "Material Request"]
		self.fields = {}
		self.custom_fields = []
		self.created_doc = Mock()
		self.created_doc.name = "Equipment"
		self.created_doc.insert.return_value = self.created_doc
		self.existing_doc = Mock()
		self.existing_doc.name = "Equipment"
		self.existing_doc.get.side_effect = lambda fieldname: self.defaults if fieldname == "dimension_defaults" else None

	def get_all(self, doctype, **kwargs):
		if doctype == "Accounting Dimension":
			return self.dimensions
		if doctype == "Custom Field":
			return self.custom_fields
		raise AssertionError(f"Unexpected get_all for {doctype}")

	def get_doc(self, doctype, name=None):
		if isinstance(doctype, dict):
			self.created_values = doctype
			return self.created_doc
		if doctype == "Accounting Dimension" and name == "Equipment":
			return self.existing_doc
		raise AssertionError(f"Unexpected get_doc for {doctype} {name}")

	def generate(self, doc, doclist):
		for doctype in doclist:
			self.fields.setdefault(doctype, link_field())
			if not any(row.dt == doctype for row in self.custom_fields):
				self.custom_fields.append(custom_field(doctype))

	def run_execute(self):
		with (
			patch.object(dimension_patch.frappe, "get_all", side_effect=self.get_all),
			patch.object(dimension_patch.frappe, "get_doc", side_effect=self.get_doc) as get_doc,
			patch.object(
				dimension_patch.frappe,
				"get_meta",
				side_effect=lambda doctype, cached=False: Meta(self.fields, doctype),
			),
			patch.object(dimension_patch, "get_doctypes_with_dimensions", return_value=self.doctypes),
			patch.object(
				dimension_patch,
				"make_dimension_in_accounting_doctypes",
				side_effect=self.generate,
			) as generate,
		):
			dimension_patch.execute()
			return get_doc, generate

	def test_absent_dimension_is_created_with_expected_configuration(self):
		self.dimensions = []
		self.defaults = []

		get_doc, generate = self.run_execute()

		self.assertEqual(
			self.created_values,
			{
				"doctype": "Accounting Dimension",
				"label": "Equipment",
				"fieldname": "equipment",
				"document_type": "Equipment Registry",
				"disabled": 0,
				"dimension_defaults": [],
			},
		)
		self.created_doc.insert.assert_called_once_with(ignore_permissions=True)
		generate.assert_called_once_with(doc=self.created_doc, doclist=self.doctypes)
		self.assertEqual(get_doc.call_count, 1)

	def test_complete_correct_dimension_is_true_noop(self):
		self.dimensions = [dimension()]
		self.defaults = []
		self.fields = {doctype: link_field() for doctype in self.doctypes}
		self.custom_fields = [custom_field(doctype) for doctype in self.doctypes]

		get_doc, generate = self.run_execute()

		get_doc.assert_called_once_with("Accounting Dimension", "Equipment")
		generate.assert_not_called()
		self.created_doc.insert.assert_not_called()
		self.existing_doc.save.assert_not_called()
		self.existing_doc.insert.assert_not_called()
		self.existing_doc.delete.assert_not_called()

	def test_compatible_material_request_fixture_is_reused(self):
		self.dimensions = []
		self.defaults = []
		fixture = link_field()
		self.fields = {"Material Request": fixture}
		self.custom_fields = [custom_field("Material Request")]

		self.run_execute()

		self.assertIs(self.fields["Material Request"], fixture)
		self.assertEqual(
			[(row.dt, row.name) for row in self.custom_fields].count(
				("Material Request", "Material Request-equipment")
			),
			1,
		)
		for field in self.fields.values():
			self.assertEqual((field.fieldtype, field.options), ("Link", "Equipment Registry"))

	def test_patch_and_generator_are_idempotent(self):
		self.dimensions = []
		self.defaults = []
		self.run_execute()

		self.dimensions = [dimension()]
		first_fields = dict(self.fields)
		first_custom_fields = list(self.custom_fields)
		_, generate = self.run_execute()

		generate.assert_not_called()
		self.assertEqual(self.fields, first_fields)
		self.assertEqual(self.custom_fields, first_custom_fields)
		self.assertEqual(len({row.dt for row in self.custom_fields}), len(self.custom_fields))

		self.generate(self.created_doc, self.doctypes)
		self.generate(self.created_doc, self.doctypes)
		self.assertEqual(len({row.dt for row in self.custom_fields}), len(self.custom_fields))

	def test_same_name_or_label_with_wrong_doctype_fails(self):
		for candidate in (
			dimension(document_type="Asset"),
			dimension(name="Other", document_type="Asset"),
		):
			with self.subTest(candidate=candidate), self.assertRaises(frappe.ValidationError):
				dimension_patch._validate_dimension_candidates([candidate])

	def test_same_fieldname_on_another_dimension_fails(self):
		candidate = dimension(name="Machine", label="Machine", document_type="Asset")
		with self.assertRaises(frappe.ValidationError):
			dimension_patch._validate_dimension_candidates([candidate])

	def test_equipment_registry_on_another_dimension_fails(self):
		candidate = dimension(name="Machine", label="Machine", fieldname="machine")
		with self.assertRaises(frappe.ValidationError):
			dimension_patch._validate_dimension_candidates([candidate])

	def test_multiple_matching_dimensions_fail(self):
		with self.assertRaises(frappe.ValidationError):
			dimension_patch._validate_dimension_candidates(
				[dimension(), dimension(name="Machine", label="Machine", document_type="Asset")]
			)

	def test_disabled_dimension_fails(self):
		with self.assertRaises(frappe.ValidationError):
			dimension_patch._validate_dimension_candidates([dimension(disabled=1)])

	def test_dimension_defaults_or_mandatory_configuration_fails(self):
		configured_default = frappe._dict(
			company="CCPC", default_dimension=None, mandatory_for_bs=0, mandatory_for_pl=1
		)
		with (
			patch.object(
				dimension_patch.frappe,
				"get_doc",
				return_value=frappe._dict(dimension_defaults=[configured_default]),
			),
			self.assertRaises(frappe.ValidationError),
		):
			dimension_patch._validate_dimension_defaults("Equipment")

	def test_incompatible_generated_field_fails(self):
		bad_field = SimpleNamespace(fieldtype="Data", options=None)
		with (
			patch.object(
				dimension_patch.frappe,
				"get_all",
				return_value=[
					frappe._dict(
						name="GL Entry-equipment",
						dt="GL Entry",
						fieldtype="Data",
						options=None,
					)
				],
			),
			patch.object(
				dimension_patch.frappe,
				"get_meta",
				return_value=SimpleNamespace(get_field=Mock(return_value=bad_field)),
			),
			self.assertRaises(frappe.ValidationError),
		):
			dimension_patch._validate_generated_fields(["GL Entry"])

	def test_duplicate_generated_fields_fail(self):
		duplicates = [
			custom_field("GL Entry"),
			frappe._dict(custom_field("GL Entry"), name="GL Entry-equipment-2"),
		]
		with (
			patch.object(dimension_patch.frappe, "get_all", return_value=duplicates),
			self.assertRaises(frappe.ValidationError),
		):
			dimension_patch._validate_generated_fields(["GL Entry"])

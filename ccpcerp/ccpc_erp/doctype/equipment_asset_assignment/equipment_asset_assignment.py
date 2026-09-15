# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


INELIGIBLE_ASSET_STATUSES = frozenset({"Sold", "Scrapped", "Capitalized"})
IMMUTABLE_FIELDS = ("equipment", "asset", "company", "installed_on")
ER_CHECKREAD = 1020


def get_wrapped_database_errno(error):
	"""Return the errno from a database exception wrapped by Frappe."""
	original = error.__cause__
	if original is None and error.args and isinstance(error.args[0], BaseException):
		original = error.args[0]
	return original.args[0] if original and original.args else None


class EquipmentAssetAssignment(Document):
	def before_validate(self):
		if self.is_new() and self.equipment:
			self.company = frappe.db.get_value("Equipment Registry", self.equipment, "company")

	def validate(self):
		self.validate_dates()
		self.validate_immutability()
		if self.is_new():
			self.lock_assignment_resources()
			self.validate_equipment()
			self.validate_asset()
		self.validate_overlap(for_update=self.is_new())

	def lock_assignment_resources(self):
		"""Serialize assignment creation for the referenced Equipment and Asset."""
		# Keep this order deterministic across transactions to reduce deadlock risk.
		frappe.db.get_value("Equipment Registry", self.equipment, "name", for_update=True)
		frappe.db.get_value("Asset", self.asset, "name", for_update=True)

	def validate_dates(self):
		installed_on = get_datetime(self.installed_on)
		if installed_on and installed_on > now_datetime():
			frappe.throw(_("Installed On cannot be in the future."))
		if self.removed_on:
			if get_datetime(self.removed_on) <= installed_on:
				frappe.throw(_("Removed On must be later than Installed On."))
			if not self.removal_reason:
				frappe.throw(_("Removal Reason is required when an assignment is closed."))

	def validate_equipment(self):
		equipment = frappe.db.get_value(
			"Equipment Registry", self.equipment, ["company", "status"], as_dict=True
		)
		if not equipment:
			frappe.throw(_("Equipment Registry {0} does not exist.").format(frappe.bold(self.equipment)))
		if equipment.status != "Active":
			frappe.throw(_("Equipment Registry must be Active for a new Asset assignment."))
		if self.company != equipment.company:
			frappe.throw(_("Company must be derived from the selected Equipment Registry."))

	def validate_asset(self):
		asset = frappe.db.get_value("Asset", self.asset, ["company", "docstatus", "status"], as_dict=True)
		if not asset:
			frappe.throw(_("Asset {0} does not exist.").format(frappe.bold(self.asset)))
		if asset.docstatus != 1:
			frappe.throw(_("Asset must be submitted for a new Equipment assignment."))
		if asset.company != self.company:
			frappe.throw(_("Asset and Equipment Registry must belong to the same Company."))
		if asset.status in INELIGIBLE_ASSET_STATUSES:
			frappe.throw(_("Asset status {0} is not eligible for assignment.").format(frappe.bold(asset.status)))

	def validate_immutability(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		for fieldname in IMMUTABLE_FIELDS:
			if self.get(fieldname) != before.get(fieldname):
				frappe.throw(_("{0} cannot be changed after the assignment is saved.").format(
					frappe.bold(self.meta.get_label(fieldname))
				))
		if before.removed_on and self.removed_on != before.removed_on:
			frappe.throw(_("A closed assignment cannot be changed or reopened."))

	def validate_overlap(self, *, for_update=False):
		for fieldname, label in (("equipment", _("Equipment")), ("asset", _("Asset"))):
			filters = {fieldname: self.get(fieldname), "name": ["!=", self.name or ""]}
			if self.removed_on:
				filters["installed_on"] = ["<", self.removed_on]
			try:
				assignments = frappe.db.get_values(
					"Equipment Asset Assignment",
					filters=filters,
					fieldname=["name", "installed_on", "removed_on"],
					as_dict=True,
					for_update=for_update,
				)
			except frappe.QueryDeadlockError as error:
				if for_update and get_wrapped_database_errno(error) == ER_CHECKREAD:
					frappe.throw(_(
						"Another Equipment Asset Assignment changed concurrently. "
						"Your assignment was not saved. Please retry."
					))
				raise
			for assignment in assignments:
				if not assignment.removed_on or get_datetime(assignment.removed_on) > get_datetime(self.installed_on):
					frappe.throw(_("{0} assignment overlaps with {1}.").format(
						label, frappe.bold(assignment.name)
					))

	def on_trash(self):
		if self.removed_on:
			frappe.throw(_("A closed historical assignment cannot be deleted."))
		for doctype in ("Work Request", "Job Order"):
			if frappe.db.exists(doctype, {"equipment_asset_assignment": self.name}):
				frappe.throw(_("Assignment cannot be deleted because it is referenced by {0}.").format(doctype))

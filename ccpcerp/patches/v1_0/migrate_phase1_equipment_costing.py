import frappe
from frappe import _

from ccpcerp.ccpc_erp.equipment_costing import validate_department_company, validate_equipment
from ccpcerp.ccpc_erp.work_request_job_order import STATUS_MAP


COMPANY = "Cagayan Corn Products Corporation"
OLD_DEPARTMENT = "Wet Mill Plant"
DEPARTMENT = "Wet Mill Plant - CCPC"
VALID_EQUIPMENT_STATUSES = {"Active", "Inactive", "Decommissioned"}
PROTOTYPES = {
	"Equipment Registry": {"WD-401", "WPS-401"},
	"Work Request": {"WR-01", "WR-02"},
	"Job Order": {"1", "3"},
}
PROTOTYPE_JO_WORK_REQUESTS = {"1": "WR-01", "3": "WR-02"}


def execute():
	_validate_masters()
	_migrate_equipment()
	_migrate_work_requests()
	_migrate_job_orders()
	_repair_prototype_work_requests()
	_validate_final_state()


def _validate_masters():
	if not frappe.db.exists("Company", COMPANY):
		frappe.throw(_("Required prototype Company {0} does not exist.").format(frappe.bold(COMPANY)))
	if not frappe.db.exists("Department", DEPARTMENT):
		frappe.throw(_("Required Department {0} does not exist.").format(frappe.bold(DEPARTMENT)))
	validate_department_company(DEPARTMENT, COMPANY, _("Phase 1 migration"), require_eligible=True)


def _migrate_equipment():
	for name in frappe.get_all("Equipment Registry", pluck="name"):
		doc = frappe.get_doc("Equipment Registry", name)
		changed = _repair_prototype_context(doc)
		if _is_prototype(doc) and not doc.status:
			doc.status = "Active"
			changed = True
		if changed:
			doc.save(ignore_permissions=True)


def _migrate_work_requests():
	for name in frappe.get_all("Work Request", pluck="name"):
		doc = frappe.get_doc("Work Request", name)
		if _repair_prototype_context(doc):
			doc.flags.syncing_job_order = True
			doc.save(ignore_permissions=True)


def _migrate_job_orders():
	for name in frappe.get_all("Job Order", pluck="name"):
		doc = frappe.get_doc("Job Order", name)
		changed = _repair_prototype_context(doc)
		if _is_prototype(doc):
			_validate_prototype_jo_relationship(doc)
		if doc.work_request:
			work_request = frappe.get_doc("Work Request", doc.work_request)
			if _is_prototype(doc):
				if not doc.company:
					doc.company = work_request.company
					changed = True
				elif doc.company != work_request.company:
					frappe.throw(_("Prototype Job Order {0} has a nonblank conflicting Company.").format(doc.name))
				if doc.equipment != work_request.equipment:
					doc.equipment = work_request.equipment
					changed = True
			else:
				_validate_job_order_against_work_request(doc, work_request)
		if changed:
			doc.flags.syncing_work_request = True
			doc.save(ignore_permissions=True)


def _repair_prototype_work_requests():
	by_work_request = _get_job_orders_by_work_request()
	for name in sorted(PROTOTYPES["Work Request"]):
		if not frappe.db.exists("Work Request", name):
			continue
		doc = frappe.get_doc("Work Request", name)
		job_order = by_work_request.get(name)
		expected_job_order = _job_order_link_value(job_order)
		expected_status = STATUS_MAP.get(job_order.status, "Converted to Job Order") if job_order else "Open"
		if doc.job_order != expected_job_order or doc.status != expected_status:
			doc.flags.syncing_job_order = True
			doc.job_order = expected_job_order
			doc.status = expected_status
			doc.save(ignore_permissions=True)


def _job_order_link_value(job_order):
	return str(job_order.name) if job_order else None


def _is_prototype(doc):
	return str(doc.name) in PROTOTYPES[doc.doctype]


def _repair_prototype_context(doc):
	if not _is_prototype(doc):
		_validate_nonprototype_legacy_context(doc)
		return False

	changed = False
	if doc.department == OLD_DEPARTMENT:
		doc.department = DEPARTMENT
		changed = True
	if not doc.company:
		doc.company = COMPANY
		changed = True
	_validate_prototype_expected_context(doc)
	return changed


def _validate_prototype_expected_context(doc):
	if doc.company != COMPANY:
		frappe.throw(
			_("Prototype {0} {1} has unexpected Company {2}; migration will not overwrite it.").format(
				doc.doctype, doc.name, doc.company
			)
		)
	if doc.department != DEPARTMENT:
		frappe.throw(
			_("Prototype {0} {1} has unexpected Department {2}; migration will not overwrite it.").format(
				doc.doctype, doc.name, doc.department
			)
		)


def _validate_nonprototype_legacy_context(doc):
	if not doc.company:
		frappe.throw(
			_("{0} {1} is not an approved prototype and has blank Company; migration will not guess it.").format(
				doc.doctype, doc.name
			)
		)
	if doc.department == OLD_DEPARTMENT:
		frappe.throw(
			_("{0} {1} is not an approved prototype and retains legacy Department {2}.").format(
				doc.doctype, doc.name, OLD_DEPARTMENT
			)
		)


def _validate_prototype_jo_relationship(doc):
	expected = PROTOTYPE_JO_WORK_REQUESTS[str(doc.name)]
	if doc.work_request != expected:
		frappe.throw(
			_("Prototype Job Order {0} must reference audited Work Request {1}, not {2}.").format(
				doc.name, expected, doc.work_request or _("blank")
			)
		)


def _validate_job_order_against_work_request(job_order, work_request):
	if job_order.company != work_request.company:
		frappe.throw(_("Job Order {0} Company conflicts with linked Work Request {1}.").format(job_order.name, work_request.name))
	if job_order.equipment != work_request.equipment:
		frappe.throw(_("Job Order {0} Equipment conflicts with linked Work Request {1}.").format(job_order.name, work_request.name))


def _get_job_orders_by_work_request():
	by_work_request = {}
	job_orders = frappe.get_all(
		"Job Order",
		fields=["name", "work_request", "status"],
		filters={"work_request": ["is", "set"]},
		order_by="name",
	)
	for job_order in job_orders:
		if job_order.work_request in by_work_request:
			frappe.throw(_("More than one Job Order references Work Request {0}.").format(job_order.work_request))
		by_work_request[job_order.work_request] = job_order
	return by_work_request


def _validate_final_state():
	by_work_request = _get_job_orders_by_work_request()
	_validate_equipment_registry_records()
	_validate_work_request_records(by_work_request)
	_validate_job_order_records()


def _validate_equipment_registry_records():
	for row in frappe.get_all("Equipment Registry", fields=["name", "company", "department", "status"]):
		_require(row.company, _("Equipment Registry {0} has blank Company.").format(row.name))
		_require(row.department, _("Equipment Registry {0} has blank Department.").format(row.name))
		_require(row.status, _("Equipment Registry {0} has blank status.").format(row.name))
		if row.status not in VALID_EQUIPMENT_STATUSES:
			frappe.throw(_("Equipment Registry {0} has invalid status {1}.").format(row.name, row.status))
		validate_department_company(
			row.department, row.company, f"Equipment Registry {row.name}", require_eligible=True
		)


def _validate_work_request_records(by_work_request):
	fields = ["name", "company", "department", "equipment", "request_type", "job_order", "status"]
	for row in frappe.get_all("Work Request", fields=fields):
		_require(row.company, _("Work Request {0} has blank Company.").format(row.name))
		_require(row.department, _("Work Request {0} has blank Department.").format(row.name))
		if row.request_type == "Equipment" and not row.equipment:
			frappe.throw(_("Equipment-type Work Request {0} has blank Equipment.").format(row.name))
		validate_department_company(
			row.department, row.company, f"Work Request {row.name}", require_eligible=True
		)
		validate_equipment(row.equipment, row.company, require_active=False, context=f"Work Request {row.name}")

		job_order = by_work_request.get(row.name)
		expected_job_order = _job_order_link_value(job_order)
		if row.job_order != expected_job_order:
			frappe.throw(_("Work Request {0} has an inconsistent derived Job Order link.").format(row.name))
		if job_order:
			expected_status = STATUS_MAP.get(job_order.status, "Converted to Job Order")
			if row.status != expected_status:
				frappe.throw(_("Work Request {0} status is inconsistent with Job Order {1}.").format(row.name, job_order.name))


def _validate_job_order_records():
	fields = ["name", "company", "department", "equipment", "work_request"]
	for row in frappe.get_all("Job Order", fields=fields):
		_require(row.company, _("Job Order {0} has blank Company.").format(row.name))
		_require(row.department, _("Job Order {0} has blank Department.").format(row.name))
		validate_department_company(
			row.department, row.company, f"Job Order {row.name}", require_eligible=True
		)
		validate_equipment(row.equipment, row.company, require_active=False, context=f"Job Order {row.name}")
		if row.work_request:
			work_request = frappe.get_doc("Work Request", row.work_request)
			_validate_job_order_against_work_request(row, work_request)


def _require(value, message):
	if not value:
		frappe.throw(message)

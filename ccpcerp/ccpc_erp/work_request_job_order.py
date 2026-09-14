import frappe
from frappe import _


JOB_ORDER_CREATION_STATUS = "Open"


STATUS_MAP = {
	None: "Converted to Job Order",
	"": "Converted to Job Order",
	"Open": "Converted to Job Order",
	"Assigned": "Converted to Job Order",
	"In Progress": "In Progress",
	"Completed": "Closed",
	"Closed": "Closed",
	"Cancelled": "Cancelled",
}


def validate_new_job_order_relationship(work_request, job_order_name=None):
	"""Validate the authoritative relationship only while a new Job Order is established."""
	if work_request.status != JOB_ORDER_CREATION_STATUS:
		frappe.throw(
			_("Job Order can only be created from an Open Work Request. Work Request {0} is {1}.").format(
				frappe.bold(work_request.name), frappe.bold(work_request.status or _("blank"))
			)
		)
	if work_request.job_order:
		frappe.throw(
			_("Work Request {0} has an existing or inconsistent Job Order backlink {1}.").format(
				frappe.bold(work_request.name), frappe.bold(work_request.job_order)
			)
		)

	filters = {"work_request": work_request.name}
	if job_order_name:
		filters["name"] = ["!=", job_order_name]
	existing = frappe.db.exists("Job Order", filters)
	if existing:
		frappe.throw(
			_("A Job Order ({0}) already exists for Work Request {1}.").format(
				frappe.bold(existing), frappe.bold(work_request.name)
			)
		)


def sync_work_request(job_order):
	if not job_order.work_request or getattr(job_order.flags, "syncing_work_request", False):
		return
	work_request = frappe.get_doc("Work Request", job_order.work_request)
	work_request.flags.syncing_job_order = True
	work_request.job_order = job_order.name
	work_request.status = STATUS_MAP.get(job_order.status, "Converted to Job Order")
	work_request.save(ignore_permissions=True)


def clear_work_request(job_order):
	if not job_order.work_request:
		return
	work_request = frappe.get_doc("Work Request", job_order.work_request)
	if work_request.job_order and str(work_request.job_order) != str(job_order.name):
		return
	work_request.flags.syncing_job_order = True
	work_request.job_order = None
	work_request.status = "Open"
	work_request.save(ignore_permissions=True)

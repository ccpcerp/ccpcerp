import frappe


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

from collections import defaultdict

import frappe
from frappe import _


def execute():
	validate_relationship_rows(frappe.get_all("Job Order", fields=["name", "work_request"]))


def validate_relationship_rows(rows):
	"""Check values exactly as returned by Frappe: None and empty string are distinct."""
	work_request_job_orders = defaultdict(list)
	for row in rows:
		if row.work_request is None:
			continue
		work_request_job_orders[row.work_request].append(str(row.name))

	conflicts = {
		work_request: sorted(names)
		for work_request, names in work_request_job_orders.items()
		if len(names) > 1
	}
	if not conflicts:
		return

	details = []
	for work_request in sorted(conflicts):
		label = _("empty-string work_request") if work_request == "" else _("Work Request {0}").format(work_request)
		details.append(_("{0}: Job Orders {1}").format(label, ", ".join(conflicts[work_request])))
	frappe.throw(
		_("Cannot add the Job Order unique index. Resolve these conflicting relationships: {0}").format(
			"; ".join(details)
		)
	)

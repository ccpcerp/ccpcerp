# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class JobOrder(Document):

    def validate(self):
        self.validate_duplicate_work_request()

    def after_insert(self):
        self.sync_work_request_status()

    def on_update(self):
        self.sync_work_request_status()

    def validate_duplicate_work_request(self):
        if self.work_request:
            existing = frappe.db.exists(
                "Job Order",
                {
                    "work_request": self.work_request,
                    "name": ["!=", self.name]
                }
            )

            if existing:
                frappe.throw(
                    f"A Job Order ({existing}) already exists for Work Request {self.work_request}."
                )

    def sync_work_request_status(self):
        if not self.work_request:
            return

        status_map = {
            "Open": "Converted to Job Order",
            "Assigned": "Converted to Job Order",
            "In Progress": "In Progress",
            "Completed": "Closed",
            "Closed": "Closed",
            "Cancelled": "Cancelled",
        }

        work_request_status = status_map.get(self.status)

        if work_request_status:
            frappe.db.set_value(
                "Work Request",
                self.work_request,
                "status",
                work_request_status
            )
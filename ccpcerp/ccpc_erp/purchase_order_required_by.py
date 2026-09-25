# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt


class PurchaseOrderRequiredByMixin:
	"""Allow overdue Required By dates while retaining ERPNext's other schedule-date behavior."""

	def validate_schedule_date(self):
		# ERPNext v16 only uses transaction_date in this method to reject a
		# Required By date earlier than the Purchase Order Date. Hide that
		# value only while the standard validator runs, so its required-date,
		# earliest-date, and blank-row propagation behavior remains intact.
		transaction_date = self.transaction_date
		try:
			self.transaction_date = None
			return super().validate_schedule_date()
		finally:
			self.transaction_date = transaction_date

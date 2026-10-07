from unittest import TestCase

from ccpcerp import setup


class TestPurchaseOrderApprovalWorkflow(TestCase):
	def test_purchase_order_workflow_separates_preparation_and_finance_approval(self):
		states = {
			state: {"docstatus": docstatus, "allow_edit": allow_edit}
			for state, docstatus, allow_edit in setup.PURCHASE_ORDER_WORKFLOW_STATES
		}
		self.assertEqual(states["Draft"]["allow_edit"], setup.PURCHASING_PERSONNEL_ROLE)
		self.assertEqual(
			states["Returned for Correction"]["allow_edit"],
			setup.PURCHASING_PERSONNEL_ROLE,
		)
		self.assertEqual(states["Pending Finance Approval"]["allow_edit"], "System Manager")
		self.assertEqual(states["Submitted"]["docstatus"], "1")

	def test_purchase_order_can_be_returned_and_resubmitted_before_submission(self):
		transitions = {
			(transition["state"], transition["action"]): transition
			for transition in setup.PURCHASE_ORDER_WORKFLOW_TRANSITIONS
		}
		self.assertEqual(
			transitions[("Draft", "Send for Finance Approval")]["next_state"],
			"Pending Finance Approval",
		)
		self.assertEqual(
			transitions[("Draft", "Send for Finance Approval")]["allowed"],
			setup.PURCHASING_PERSONNEL_ROLE,
		)
		self.assertEqual(
			transitions[("Pending Finance Approval", "Return for Correction")]["next_state"],
			"Returned for Correction",
		)
		self.assertEqual(
			transitions[("Pending Finance Approval", "Return for Correction")]["allowed"],
			setup.PURCHASE_ORDER_APPROVER_ROLE,
		)
		self.assertEqual(
			transitions[("Returned for Correction", "Resubmit for Finance Approval")]["next_state"],
			"Pending Finance Approval",
		)

	def test_only_finance_approver_transition_submits_purchase_order(self):
		approve = next(
			transition
			for transition in setup.PURCHASE_ORDER_WORKFLOW_TRANSITIONS
			if transition["action"] == "Approve & Submit"
		)
		self.assertEqual(approve["state"], "Pending Finance Approval")
		self.assertEqual(approve["next_state"], "Submitted")
		self.assertEqual(approve["allowed"], setup.PURCHASE_ORDER_APPROVER_ROLE)

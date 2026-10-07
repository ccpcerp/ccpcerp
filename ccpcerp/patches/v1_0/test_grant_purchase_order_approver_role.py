from unittest import TestCase
from unittest.mock import MagicMock, patch

from ccpcerp.patches.v1_0.grant_purchase_order_approver_role import execute
from ccpcerp.setup import (
	PURCHASE_ORDER_APPROVER_PERMISSIONS,
	PURCHASE_ORDER_APPROVER_ROLE,
	PURCHASE_ORDER_APPROVER_USERS,
)


class TestGrantPurchaseOrderApproverRolePatch(TestCase):
	def test_purchase_order_approver_is_narrowly_scoped(self):
		po_rights = PURCHASE_ORDER_APPROVER_PERMISSIONS["Purchase Order"]
		self.assertTrue({"read", "write", "submit", "print", "report"} <= po_rights)
		self.assertNotIn("create", po_rights)
		self.assertNotIn("cancel", po_rights)
		self.assertNotIn("amend", po_rights)
		self.assertNotIn("Purchase Invoice", PURCHASE_ORDER_APPROVER_PERMISSIONS)
		self.assertNotIn("Payment Entry", PURCHASE_ORDER_APPROVER_PERMISSIONS)

	@patch("ccpcerp.patches.v1_0.grant_purchase_order_approver_role._ensure_role")
	@patch("ccpcerp.patches.v1_0.grant_purchase_order_approver_role.frappe")
	def test_active_finance_controller_gets_purchase_order_approver_role(self, frappe, ensure_role):
		user = PURCHASE_ORDER_APPROVER_USERS[0]
		frappe.db.exists.return_value = True
		user_doc = MagicMock()
		user_doc.enabled = 1
		user_doc.roles = []
		frappe.get_doc.return_value = user_doc

		execute()

		ensure_role.assert_called_once_with(PURCHASE_ORDER_APPROVER_ROLE)
		user_doc.append_roles.assert_called_once_with(PURCHASE_ORDER_APPROVER_ROLE)
		user_doc.save.assert_called_once_with(ignore_permissions=True)
		frappe.clear_cache.assert_called_once_with(user=user)

	@patch("ccpcerp.patches.v1_0.grant_purchase_order_approver_role._ensure_role")
	@patch("ccpcerp.patches.v1_0.grant_purchase_order_approver_role.frappe")
	def test_missing_disabled_or_existing_user_is_not_rewritten(self, frappe, ensure_role):
		users = ("missing@example.com", "disabled@example.com", "existing@example.com")
		with patch(
			"ccpcerp.patches.v1_0.grant_purchase_order_approver_role.PURCHASE_ORDER_APPROVER_USERS",
			users,
		):
			disabled = MagicMock()
			disabled.enabled = 0
			disabled.roles = []
			existing = MagicMock()
			existing.enabled = 1
			existing.roles = [MagicMock(role=PURCHASE_ORDER_APPROVER_ROLE)]
			frappe.db.exists.side_effect = [False, True, True]
			frappe.get_doc.side_effect = [disabled, existing]

			execute()

		disabled.append_roles.assert_not_called()
		disabled.save.assert_not_called()
		existing.append_roles.assert_not_called()
		existing.save.assert_not_called()
		frappe.clear_cache.assert_not_called()

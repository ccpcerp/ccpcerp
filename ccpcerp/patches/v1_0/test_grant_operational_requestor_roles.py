from unittest import TestCase
from unittest.mock import MagicMock, patch

from ccpcerp.patches.v1_0.grant_operational_requestor_roles import execute
from ccpcerp.setup import MRS_REQUESTOR_ROLE, WITHDRAWAL_SLIP_REQUESTOR_ROLE


class TestGrantOperationalRequestorRolesPatch(TestCase):
	@patch("ccpcerp.patches.v1_0.grant_operational_requestor_roles._ensure_role")
	@patch("ccpcerp.patches.v1_0.grant_operational_requestor_roles.frappe")
	def test_active_operational_user_gets_both_requestor_roles(self, frappe, ensure_role):
		frappe.get_all.return_value = ["operations@example.com"]
		user = MagicMock()
		user.enabled = 1
		user.roles = []
		frappe.get_doc.return_value = user

		execute()

		ensure_role.assert_any_call(MRS_REQUESTOR_ROLE)
		ensure_role.assert_any_call(WITHDRAWAL_SLIP_REQUESTOR_ROLE)
		user.append_roles.assert_called_once_with(
			MRS_REQUESTOR_ROLE,
			WITHDRAWAL_SLIP_REQUESTOR_ROLE,
		)
		user.save.assert_called_once_with(ignore_permissions=True)
		frappe.clear_cache.assert_called_once_with(user="operations@example.com")

	@patch("ccpcerp.patches.v1_0.grant_operational_requestor_roles._ensure_role")
	@patch("ccpcerp.patches.v1_0.grant_operational_requestor_roles.frappe")
	def test_existing_or_disabled_users_are_not_rewritten(self, frappe, ensure_role):
		frappe.get_all.return_value = ["disabled@example.com", "existing@example.com"]
		disabled = MagicMock()
		disabled.enabled = 0
		disabled.roles = []
		existing = MagicMock()
		existing.enabled = 1
		existing.roles = [
			MagicMock(role=MRS_REQUESTOR_ROLE),
			MagicMock(role=WITHDRAWAL_SLIP_REQUESTOR_ROLE),
		]
		frappe.get_doc.side_effect = [disabled, existing]

		execute()

		disabled.append_roles.assert_not_called()
		disabled.save.assert_not_called()
		existing.append_roles.assert_not_called()
		existing.save.assert_not_called()
		frappe.clear_cache.assert_not_called()

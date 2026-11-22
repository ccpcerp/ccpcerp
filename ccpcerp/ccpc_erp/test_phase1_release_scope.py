# Copyright (c) 2026, CCPC and contributors
"""Pure offline regression checks: never touch Frappe, stock, SQL or invoices."""
import unittest

from ccpcerp.ccpc_erp.phase1_release_scope import (
    Phase1ScopeError,
    require_billing_disabled,
    require_same_company_issue,
)


class TestPhase1ReleaseScope(unittest.TestCase):
    def test_same_company_is_allowed(self):
        self.assertEqual(require_same_company_issue("Ricor Mills Corporation", "Ricor Mills Corporation"), "Ricor Mills Corporation")

    def test_cross_company_is_denied_in_both_directions(self):
        for issuer, requester in (
            ("Cagayan Corn Products Corporation", "Ricor Mills Corporation"),
            ("Ricor Mills Corporation", "Cagayan Corn Products Corporation"),
        ):
            with self.subTest(issuer=issuer, requester=requester):
                with self.assertRaisesRegex(Phase1ScopeError, "issuing and requesting"):
                    require_same_company_issue(issuer, requester)

    def test_missing_company_fails_closed(self):
        for value in (None, "", " ", 5):
            with self.subTest(value=value):
                with self.assertRaises(Phase1ScopeError):
                    require_same_company_issue("Ricor Mills Corporation", value)
                with self.assertRaises(Phase1ScopeError):
                    require_same_company_issue(value, "Ricor Mills Corporation")

    def test_uncanonical_names_not_silently_trimmed(self):
        with self.assertRaises(Phase1ScopeError):
            require_same_company_issue("Ricor Mills Corporation ", "Ricor Mills Corporation")
        with self.assertRaises(Phase1ScopeError):
            require_same_company_issue("Ricor Mills Corporation", " Ricor Mills Corporation")

    def test_billing_api_is_disabled(self):
        with self.assertRaisesRegex(Phase1ScopeError, "disabled"):
            require_billing_disabled()


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import MagicMock, patch

import frappe

from ccpcerp.patches.v1_0 import create_intercompany_parties as patch_module


MODULE = "ccpcerp.patches.v1_0.create_intercompany_parties"


class FakeParty:
    def __init__(self, doctype="Customer", name="Test Party"):
        self.doctype = doctype
        self.name = name
        self.companies = []
        self.accounts = []

    def get(self, fieldname):
        return getattr(self, fieldname)

    def append(self, fieldname, value):
        row = frappe._dict(value)
        getattr(self, fieldname).append(row)
        return row


class TestCreateIntercompanyParties(unittest.TestCase):
    def test_company_configuration_contains_four_unique_companies(self):
        names = [company for company, _abbr in patch_module.COMPANIES]
        abbrs = [abbr for _company, abbr in patch_module.COMPANIES]

        self.assertEqual(len(names), 4)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(abbrs), len(set(abbrs)))

    def test_other_companies_excludes_represented_company(self):
        others = patch_module._other_companies(
            "Cagayan Corn Products Corporation"
        )

        self.assertEqual(len(others), 3)
        self.assertNotIn("Cagayan Corn Products Corporation", others)
        self.assertIn("Ricor Mills Corporation", others)
        self.assertIn("GSL Realty Corporation", others)
        self.assertIn("Woodland Farms Corporation", others)

    def test_receivable_account_name_uses_company_abbreviation(self):
        self.assertEqual(
            patch_module._account_name(
                patch_module.RECEIVABLE_CODE,
                "Ricor Mills Corporation",
            ),
            "1312 - Intercompany Receivable - RMC",
        )

    def test_payable_account_name_uses_company_abbreviation(self):
        self.assertEqual(
            patch_module._account_name(
                patch_module.PAYABLE_CODE,
                "Cagayan Corn Products Corporation",
            ),
            "2121 - Intercompany Payable - CCPC",
        )

    def test_allowed_companies_adds_other_three(self):
        doc = FakeParty()

        patch_module._ensure_allowed_companies(
            doc,
            "Cagayan Corn Products Corporation",
        )

        self.assertEqual(
            {row.company for row in doc.companies},
            {
                "Ricor Mills Corporation",
                "GSL Realty Corporation",
                "Woodland Farms Corporation",
            },
        )

    def test_allowed_companies_is_idempotent(self):
        doc = FakeParty()

        patch_module._ensure_allowed_companies(
            doc,
            "Cagayan Corn Products Corporation",
        )
        patch_module._ensure_allowed_companies(
            doc,
            "Cagayan Corn Products Corporation",
        )

        self.assertEqual(len(doc.companies), 3)

    def test_self_company_in_allowed_companies_is_rejected(self):
        doc = FakeParty()
        doc.companies.append(
            frappe._dict(company="Cagayan Corn Products Corporation")
        )

        with self.assertRaises(frappe.ValidationError):
            patch_module._ensure_allowed_companies(
                doc,
                "Cagayan Corn Products Corporation",
            )

    def test_party_accounts_add_expected_accounts(self):
        doc = FakeParty()

        with patch(f"{MODULE}._validate_account") as validate:
            patch_module._ensure_party_accounts(
                doc,
                "Cagayan Corn Products Corporation",
                patch_module.RECEIVABLE_CODE,
                "Receivable",
            )

        self.assertEqual(len(doc.accounts), 3)

        accounts = {
            row.company: row.account
            for row in doc.accounts
        }

        self.assertEqual(
            accounts["Ricor Mills Corporation"],
            "1312 - Intercompany Receivable - RMC",
        )
        self.assertEqual(
            accounts["GSL Realty Corporation"],
            "1312 - Intercompany Receivable - GSL",
        )
        self.assertEqual(
            accounts["Woodland Farms Corporation"],
            "1312 - Intercompany Receivable - WFC",
        )
        self.assertEqual(validate.call_count, 3)

    def test_party_accounts_is_idempotent(self):
        doc = FakeParty()

        with patch(f"{MODULE}._validate_account"):
            patch_module._ensure_party_accounts(
                doc,
                "Ricor Mills Corporation",
                patch_module.PAYABLE_CODE,
                "Payable",
            )
            patch_module._ensure_party_accounts(
                doc,
                "Ricor Mills Corporation",
                patch_module.PAYABLE_CODE,
                "Payable",
            )

        self.assertEqual(len(doc.accounts), 3)

    def test_existing_party_account_is_corrected(self):
        doc = FakeParty()
        doc.accounts.append(
            frappe._dict(
                company="Ricor Mills Corporation",
                account="Old Account",
            )
        )

        with patch(f"{MODULE}._validate_account"):
            patch_module._ensure_party_accounts(
                doc,
                "Cagayan Corn Products Corporation",
                patch_module.PAYABLE_CODE,
                "Payable",
            )

        rmc = next(
            row
            for row in doc.accounts
            if row.company == "Ricor Mills Corporation"
        )
        self.assertEqual(
            rmc.account,
            "2121 - Intercompany Payable - RMC",
        )

    def test_duplicate_party_account_company_is_rejected(self):
        doc = FakeParty()
        doc.accounts.extend(
            [
                frappe._dict(
                    company="Ricor Mills Corporation",
                    account="Account A",
                ),
                frappe._dict(
                    company="Ricor Mills Corporation",
                    account="Account B",
                ),
            ]
        )

        with self.assertRaises(frappe.ValidationError):
            patch_module._ensure_party_accounts(
                doc,
                "Cagayan Corn Products Corporation",
                patch_module.PAYABLE_CODE,
                "Payable",
            )

    def test_validate_account_rejects_missing_account(self):
        with patch(
            f"{MODULE}.frappe.db.get_value",
            return_value=None,
        ):
            with self.assertRaises(frappe.ValidationError):
                patch_module._validate_account(
                    "Missing Account",
                    "Ricor Mills Corporation",
                    "Receivable",
                )

    def test_validate_account_rejects_wrong_company(self):
        account = frappe._dict(
            company="Cagayan Corn Products Corporation",
            account_type="Receivable",
            is_group=0,
        )

        with patch(
            f"{MODULE}.frappe.db.get_value",
            return_value=account,
        ):
            with self.assertRaises(frappe.ValidationError):
                patch_module._validate_account(
                    "1312 - Intercompany Receivable - RMC",
                    "Ricor Mills Corporation",
                    "Receivable",
                )

    def test_validate_account_rejects_wrong_account_type(self):
        account = frappe._dict(
            company="Ricor Mills Corporation",
            account_type="",
            is_group=0,
        )

        with patch(
            f"{MODULE}.frappe.db.get_value",
            return_value=account,
        ):
            with self.assertRaises(frappe.ValidationError):
                patch_module._validate_account(
                    "1312 - Intercompany Receivable - RMC",
                    "Ricor Mills Corporation",
                    "Receivable",
                )

    def test_name_collision_is_not_converted_automatically(self):
        same_name = frappe._dict(
            name="Ricor Mills Corporation",
            customer_name="Ricor Mills Corporation",
            is_internal_customer=0,
            represents_company=None,
            disabled=0,
        )

        with patch(
            f"{MODULE}.frappe.db.get_value",
            side_effect=[None, same_name],
        ):
            with self.assertRaises(frappe.ValidationError):
                patch_module._resolve_existing_party(
                    "Customer",
                    "customer_name",
                    "is_internal_customer",
                    "Ricor Mills Corporation",
                )


if __name__ == "__main__":
    unittest.main()

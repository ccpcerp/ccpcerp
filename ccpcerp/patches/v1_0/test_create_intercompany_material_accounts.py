from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from ccpcerp.patches.v1_0 import create_intercompany_material_accounts as account_patch


def raise_frappe_exception(message, exc=frappe.ValidationError):
    raise exc(message)


def account_row(company, abbr, spec, **overrides):
    values = {
        "name": f"{spec['account_number']} - {spec['account_name']} - {abbr}",
        "account_name": spec["account_name"],
        "account_number": spec["account_number"],
        "company": company,
        "parent_account": spec["parent_account"],
        "root_type": spec["root_type"],
        "account_type": spec["account_type"],
        "is_group": spec["is_group"],
        "disabled": 0,
    }
    values.update(overrides)
    return frappe._dict(values)


class TestIntercompanyMaterialAccountsPatch(TestCase):
    def setUp(self):
        translation = patch.object(account_patch, "_", side_effect=lambda value: value)
        translation.start()
        self.addCleanup(translation.stop)

        throw = patch.object(
            account_patch.frappe,
            "throw",
            side_effect=raise_frappe_exception,
        )
        throw.start()
        self.addCleanup(throw.stop)

    def test_account_specs_are_standardized(self):
        specs = account_patch._account_specs("CCPC")

        self.assertEqual(
            [row["account_number"] for row in specs],
            ["1312", "2120", "2121", "5150", "6050"],
        )
        self.assertEqual(specs[0]["account_type"], "Receivable")
        self.assertEqual(specs[1]["is_group"], 1)
        self.assertEqual(specs[2]["account_type"], "Payable")
        self.assertEqual(specs[3]["root_type"], "Expense")
        self.assertEqual(specs[4]["root_type"], "Income")

    def test_execute_processes_all_accounts_for_all_companies(self):
        with (
            patch.object(account_patch.frappe.db, "exists", return_value=True),
            patch.object(account_patch, "_ensure_account") as ensure_account,
        ):
            account_patch.execute()

        self.assertEqual(
            ensure_account.call_count,
            len(account_patch.COMPANIES) * 5,
        )

        seen_companies = {
            call.args[0]
            for call in ensure_account.call_args_list
        }
        self.assertEqual(seen_companies, set(account_patch.COMPANIES))

    def test_missing_required_company_fails(self):
        def exists(doctype, name):
            if doctype == "Company":
                return name != "Ricor Mills Corporation"
            return False

        with (
            patch.object(account_patch.frappe.db, "exists", side_effect=exists),
            patch.object(account_patch, "_ensure_account"),
            self.assertRaises(frappe.ValidationError),
        ):
            account_patch.execute()

    def test_existing_correct_account_is_reused(self):
        company = "Cagayan Corn Products Corporation"
        abbr = "CCPC"
        spec = account_patch._account_specs(abbr)[4]
        existing = account_row(company, abbr, spec)

        with (
            patch.object(account_patch.frappe, "get_all", return_value=[existing]),
            patch.object(account_patch.frappe, "get_doc") as get_doc,
        ):
            account_patch._ensure_account(company, abbr, spec)

        get_doc.assert_not_called()

    def test_existing_incompatible_account_fails(self):
        company = "Cagayan Corn Products Corporation"
        abbr = "CCPC"
        spec = account_patch._account_specs(abbr)[0]
        existing = account_row(
            company,
            abbr,
            spec,
            parent_account="1001 - Current Assets - CCPC",
        )

        with (
            patch.object(account_patch.frappe, "get_all", return_value=[existing]),
            self.assertRaises(frappe.ValidationError),
        ):
            account_patch._ensure_account(company, abbr, spec)

    def test_duplicate_account_number_fails(self):
        company = "Ricor Mills Corporation"
        abbr = "RMC"
        spec = account_patch._account_specs(abbr)[0]

        matches = [
            account_row(company, abbr, spec),
            account_row(
                company,
                abbr,
                spec,
                name="1312 - Another Account - RMC",
                account_name="Another Account",
            ),
        ]

        with (
            patch.object(account_patch.frappe, "get_all", return_value=matches),
            self.assertRaises(frappe.ValidationError),
        ):
            account_patch._ensure_account(company, abbr, spec)

    def test_missing_parent_fails(self):
        company = "GSL Realty Corporation"
        abbr = "GSL"
        spec = account_patch._account_specs(abbr)[0]

        def get_value(doctype, name, fields, as_dict=False):
            self.assertEqual(doctype, "Account")
            self.assertEqual(name, spec["parent_account"])
            return None

        with (
            patch.object(account_patch.frappe, "get_all", return_value=[]),
            patch.object(account_patch.frappe.db, "exists", return_value=False),
            patch.object(account_patch.frappe.db, "get_value", side_effect=get_value),
            self.assertRaises(frappe.ValidationError),
        ):
            account_patch._ensure_account(company, abbr, spec)

    def test_incompatible_parent_fails(self):
        company = "Woodland Farms Corporation"
        abbr = "WFC"
        spec = account_patch._account_specs(abbr)[2]

        parent = frappe._dict(
            name=spec["parent_account"],
            company=company,
            is_group=0,
            disabled=0,
        )

        with (
            patch.object(account_patch.frappe, "get_all", return_value=[]),
            patch.object(account_patch.frappe.db, "exists", return_value=False),
            patch.object(account_patch.frappe.db, "get_value", return_value=parent),
            self.assertRaises(frappe.ValidationError),
        ):
            account_patch._ensure_account(company, abbr, spec)

    def test_missing_account_is_created_and_validated(self):
        company = "Ricor Mills Corporation"
        abbr = "RMC"
        spec = account_patch._account_specs(abbr)[0]
        expected = account_row(company, abbr, spec)

        parent = frappe._dict(
            name=spec["parent_account"],
            company=company,
            is_group=1,
            disabled=0,
        )

        created_doc = Mock()
        created_doc.name = expected.name

        get_value_results = [parent, expected]

        with (
            patch.object(account_patch.frappe, "get_all", return_value=[]),
            patch.object(account_patch.frappe.db, "exists", return_value=False),
            patch.object(
                account_patch.frappe.db,
                "get_value",
                side_effect=get_value_results,
            ),
            patch.object(
                account_patch.frappe,
                "get_doc",
                return_value=created_doc,
            ) as get_doc,
        ):
            account_patch._ensure_account(company, abbr, spec)

        get_doc.assert_called_once_with(
            {
                "doctype": "Account",
                "company": company,
                "account_number": "1312",
                "account_name": "Intercompany Receivable",
                "parent_account": "1310 - Accounts Receivable - Affiliates - RMC",
                "account_type": "Receivable",
                "is_group": 0,
            }
        )
        created_doc.insert.assert_called_once_with(ignore_permissions=True)

    def test_name_collision_fails(self):
        company = "Cagayan Corn Products Corporation"
        abbr = "CCPC"
        spec = account_patch._account_specs(abbr)[3]

        with (
            patch.object(account_patch.frappe, "get_all", return_value=[]),
            patch.object(account_patch.frappe.db, "exists", return_value=True),
            self.assertRaises(frappe.ValidationError),
        ):
            account_patch._ensure_account(company, abbr, spec)

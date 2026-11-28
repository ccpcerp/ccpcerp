"""Frappe-free regression of CCPC/RMC Company Cost Center default policy.

Use: python3 -B -m unittest ccpcerp.ccpc_erp.test_company_default_cost_centers_pure -v
These tests do NOT modify any ERPNext site or prove live stock/GL posting.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from ccpcerp.ccpc_erp.bc_issue_allocation import (
    COMPANY_DEFAULT_COST_CENTERS,
    CenterEvidence,
    CompanyCenterDefaultEvidence,
    ExpenseEvidence,
    IssueLine,
    resolve_original_issue_allocation,
)
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError


NOW = datetime(2026, 11, 29, 4, 0, tzinfo=timezone.utc)
CCPC = "Cagayan Corn Products Corporation"
RMC = "Ricor Mill Corporation"


def expense(company):
    return ExpenseEvidence(
        revision="signed-expense-1", company=company, item_group="Bearings",
        expense_account="Expense Account", account_company=company,
        account_root_type="Expense", account_is_group=False,
        account_disabled=False, item_group_is_group=False,
        approval_state="Approved", prepared_by="finance-maker@example.test",
        approved_by="finance-checker@example.test",
        approved_at=NOW - timedelta(days=1),
        effective_from=NOW - timedelta(days=1),
        effective_until=None,
    )


def company_default(company, center):
    return CompanyCenterDefaultEvidence(
        company=company, cost_center=center, center_company=company,
        center_is_group=False, center_disabled=False,
    )


def allocate(company, beneficiary, default, *, signed=(), expense_evidence=None,
             chosen_revision=None):
    return resolve_original_issue_allocation(
        company=company, beneficiary_department=beneficiary, posting_at=NOW,
        lines=[IssueLine(source_row="row1", item_code="ITEM-00010", item_group="Bearings")],
        expense_evidence=[expense(company)] if expense_evidence is None else expense_evidence,
        center_evidence=list(signed), selected_center_revision=chosen_revision,
        company_center_default=default,
    )


class CompanyDefaultCenterPolicyTests(unittest.TestCase):
    def test_exact_company_defaults_and_missing_revision_link(self):
        expected = {
            CCPC: ("Main - CCPC", "Wet Mill Plant - CCPC"),
            RMC: ("Main - RMC", "RMC Production Department"),
        }
        self.assertEqual(COMPANY_DEFAULT_COST_CENTERS,
                         {name: center for name, (center, _) in expected.items()})
        for company, (center, beneficiary) in expected.items():
            with self.subTest(company=company):
                result = allocate(company, beneficiary, company_default(company, center))
                self.assertEqual(len(result), 1)
                row = result[0]
                self.assertEqual(row.company, company)
                self.assertEqual(row.beneficiary_department, beneficiary)
                self.assertEqual(row.cost_center, center)
                self.assertIsNone(row.center_revision)
                self.assertEqual(row.center_source, "Company Default")
                self.assertEqual(row.expense_revision, "signed-expense-1")

    def test_reject_wrong_company_or_unlisted_company(self):
        with self.assertRaises(MappingPolicyError):
            allocate(CCPC, "Wet Mill Plant - CCPC", company_default(RMC, "Main - RMC"))
        with self.assertRaises(MappingPolicyError):
            allocate("Unknown Company", "Unknown Dept",
                     company_default("Unknown Company", "Main - UNKNOWN"))

    def test_reject_wrong_center_group_disabled_or_cross_company_master(self):
        good = company_default(CCPC, "Main - CCPC")
        for bad in (
            replace(good, cost_center="Main - RMC"),
            replace(good, center_company=RMC),
            replace(good, center_is_group=True),
            replace(good, center_disabled=True),
        ):
            with self.subTest(default=bad), self.assertRaises(MappingPolicyError):
                allocate(CCPC, "Wet Mill Plant - CCPC", bad)

    def test_no_implicit_default_and_no_forged_selector(self):
        with self.assertRaises(MappingPolicyError):
            allocate(CCPC, "Wet Mill Plant - CCPC", None)
        with self.assertRaises(MappingPolicyError):
            allocate(CCPC, "Wet Mill Plant - CCPC",
                     company_default(CCPC, "Main - CCPC"),
                     chosen_revision="forged-center")

    def test_company_default_cannot_bypass_missing_signed_expense(self):
        with self.assertRaises(MappingPolicyError):
            allocate(CCPC, "Wet Mill Plant - CCPC",
                     company_default(CCPC, "Main - CCPC"),
                     expense_evidence=[])

    def test_preserve_existing_signed_department_revision(self):
        signed = CenterEvidence(
            revision="srq204k4v9", company=CCPC,
            beneficiary_department="Wet Mill Plant - CCPC",
            cost_center="Main - CCPC", center_company=CCPC,
            center_is_group=False, center_disabled=False,
            selector_mode="Department Only", approval_state="Approved",
            prepared_by="stockroom@example.test",
            approved_by="finance-controller@example.test",
            approved_at=NOW - timedelta(days=1),
            effective_from=NOW - timedelta(days=1), effective_until=None,
        )
        result = allocate(CCPC, "Wet Mill Plant - CCPC", None, signed=[signed])
        self.assertEqual(result[0].center_revision, "srq204k4v9")
        self.assertEqual(result[0].center_source, "Signed Department Revision")
        with self.assertRaises(MappingPolicyError):
            allocate(CCPC, "Wet Mill Plant - CCPC",
                     company_default(CCPC, "Main - CCPC"), signed=[signed])

    def test_runtime_and_schema_require_provenance_field(self):
        root = Path(__file__).resolve().parent
        runtime = (root / "bc_issue_runtime.py").read_text(encoding="utf-8")
        setup = (root.parent / "setup.py").read_text(encoding="utf-8")
        for text in (
            '"SELECT name, cost_center FROM `tabCompany` WHERE name=%s FOR UPDATE"',
            'company_center_default=company_center_default',
            'row.ccpc_center_assignment_source = a.center_source',
            'row.get("ccpc_center_assignment_source") == expected.center_source',
            '"ccpc_center_assignment_source"',
        ):
            self.assertIn(text, runtime)
        self.assertIn('"fieldname": "ccpc_center_assignment_source"', setup)
        self.assertIn('"fieldtype": "Select"', setup)


if __name__ == "__main__":
    unittest.main()

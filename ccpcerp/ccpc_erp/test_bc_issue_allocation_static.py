"""Synthetic non-Frappe B/C original Material Issue allocation tests.

This is deliberately NOT proof of a live approved mapping, GL, or SLE posting.
"""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from ccpcerp.ccpc_erp.bc_issue_allocation import (
    CenterEvidence, ExpenseEvidence, IssueLine,
    assert_original_issue_rows_equal, resolve_original_issue_allocation,
)
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 5, tzinfo=UTC)


def expense(**overrides):
    values = dict(
        revision="EXP-1", company="_Test Company", item_group="_Test Materials",
        expense_account="_Test Consumables Expense", account_company="_Test Company",
        account_root_type="Expense", account_is_group=False, account_disabled=False,
        item_group_is_group=False, approval_state="Approved", prepared_by="preparer@test.invalid",
        approved_by="reviewer@test.invalid", approved_at=NOW-timedelta(days=2),
        effective_from=NOW-timedelta(days=1), effective_until=None,
    )
    values.update(overrides)
    return ExpenseEvidence(**values)


def center(**overrides):
    values = dict(
        revision="CTR-1", company="_Test Company", beneficiary_department="_Test Maintenance",
        cost_center="_Test Plant Center", center_company="_Test Company", center_is_group=False,
        center_disabled=False, selector_mode="Department Only", approval_state="Approved",
        prepared_by="preparer@test.invalid", approved_by="reviewer@test.invalid",
        approved_at=NOW-timedelta(days=2), effective_from=NOW-timedelta(days=1),
        effective_until=None,
    )
    values.update(overrides)
    return CenterEvidence(**values)


def issue(*, lines=None, expenses=None, centers=None, chosen=None, company="_Test Company", department="_Test Maintenance", posting=NOW):
    return resolve_original_issue_allocation(
        company=company, beneficiary_department=department, posting_at=posting,
        lines=lines if lines is not None else [IssueLine("WSI-1", "PART-1", "_Test Materials")],
        expense_evidence=expenses if expenses is not None else [expense()],
        center_evidence=centers if centers is not None else [center()],
        selected_center_revision=chosen,
    )


class IssueAllocationTests(unittest.TestCase):
    def test_approved_original_issue_yields_immutable_company_scoped_snapshot(self):
        result = issue()
        self.assertEqual(len(result), 1)
        r = result[0]
        self.assertEqual((r.expense_account, r.cost_center, r.expense_revision, r.center_revision),
                         ("_Test Consumables Expense", "_Test Plant Center", "EXP-1", "CTR-1"))
        with self.assertRaises((AttributeError, TypeError)):
            r.expense_account = "forged"

    def test_distinct_categories_different_accounts_same_beneficiary(self):
        allocations = issue(
            lines=[IssueLine("WSI-1", "PART-1", "_Test Materials"), IssueLine("WSI-2", "PART-2", "_Test Parts")],
            expenses=[expense(), expense(revision="EXP-2", item_group="_Test Parts", expense_account="_Test Repairs Expense")],
        )
        self.assertEqual([a.expense_account for a in allocations],
                         ["_Test Consumables Expense", "_Test Repairs Expense"])
        self.assertEqual({a.cost_center for a in allocations}, {"_Test Plant Center"})

    def test_missing_or_draft_or_superseded_expense_is_blocked(self):
        for rows in ([], [expense(approval_state="Draft")], [expense(approval_state="Revoked")]):
            with self.subTest(rows=rows), self.assertRaises(MappingPolicyError):
                issue(expenses=rows)

    def test_missing_or_draft_center_is_blocked(self):
        for rows in ([], [center(approval_state="Draft")]):
            with self.subTest(rows=rows), self.assertRaises(MappingPolicyError):
                issue(centers=rows)

    def test_wrong_company_and_beneficiary_do_not_match(self):
        with self.assertRaises(MappingPolicyError):
            issue(expenses=[expense(company="Other Company")])
        with self.assertRaises(MappingPolicyError):
            issue(centers=[center(company="Other Company")])
        with self.assertRaises(MappingPolicyError):
            issue(centers=[center(beneficiary_department="Wrong Department")])

    def test_parent_group_and_wrong_master_ownership_rejected(self):
        for evidence in [expense(item_group_is_group=True), expense(account_is_group=True),
                         expense(account_disabled=True), expense(account_company="Other Company"),
                         expense(account_root_type="Asset")]:
            with self.subTest(evidence=evidence), self.assertRaises(MappingPolicyError):
                issue(expenses=[evidence])
        for evidence in [center(center_is_group=True), center(center_disabled=True), center(center_company="Other Company")]:
            with self.subTest(evidence=evidence), self.assertRaises(MappingPolicyError):
                issue(centers=[evidence])

    def test_ambiguous_approved_expense_revisions_rejected(self):
        with self.assertRaises(MappingPolicyError):
            issue(expenses=[expense(), expense(revision="EXP-2")])

    def test_expired_not_yet_effective_or_unapproved_at_posting_rejected(self):
        for evidence in [expense(effective_until=NOW), expense(effective_from=NOW+timedelta(hours=1)),
                         expense(approved_at=NOW+timedelta(hours=1))]:
            with self.subTest(evidence=evidence), self.assertRaises(MappingPolicyError):
                issue(expenses=[evidence])
        with self.assertRaises(MappingPolicyError):
            issue(posting=datetime(2026, 10, 20, 5))

    def test_maker_checker_same_identity_rejected(self):
        with self.assertRaises(MappingPolicyError):
            issue(expenses=[expense(approved_by="preparer@test.invalid")])

    def test_multi_center_requires_explicit_revision(self):
        options=[center(revision="CTR-1",selector_mode="Explicit Selection"),
                 center(revision="CTR-2",selector_mode="Explicit Selection",cost_center="_Test Shop Center")]
        with self.assertRaises(MappingPolicyError):
            issue(centers=options)
        self.assertEqual(issue(centers=options,chosen="CTR-2")[0].cost_center,"_Test Shop Center")
        with self.assertRaises(MappingPolicyError):
            issue(centers=options,chosen="FORGED")
        with self.assertRaises(MappingPolicyError):
            issue(centers=[center(),options[1]],chosen="CTR-2")

    def test_duplicate_source_identity_and_missing_item_rejected(self):
        with self.assertRaises(MappingPolicyError):
            issue(lines=[IssueLine("WSI-1","PART-1","_Test Materials"),IssueLine("WSI-1","PART-2","_Test Materials")])
        with self.assertRaises(MappingPolicyError):
            issue(lines=[IssueLine("WSI-1","","_Test Materials")])

    def test_forged_row_expense_center_item_or_duplicate_rejected(self):
        expected=issue()
        row={"withdrawal_slip_item":"WSI-1","item_code":"PART-1","expense_account":"_Test Consumables Expense","cost_center":"_Test Plant Center"}
        self.assertTrue(assert_original_issue_rows_equal(expected,[row]))
        for field,new in (("expense_account","other"),("cost_center","other"),("item_code","another"),
                          ("withdrawal_slip_item","fake")):
            altered=dict(row,**{field:new})
            with self.subTest(field=field),self.assertRaises(MappingPolicyError):
                assert_original_issue_rows_equal(expected,[altered])
        with self.assertRaises(MappingPolicyError):
            assert_original_issue_rows_equal(expected,[row,row])
        with self.assertRaises(MappingPolicyError):
            assert_original_issue_rows_equal(expected,[])


if __name__ == "__main__":
    unittest.main()

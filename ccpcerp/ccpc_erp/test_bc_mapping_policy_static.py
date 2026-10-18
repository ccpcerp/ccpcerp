"""Inert G1-G3 policy tests. No Frappe, ERPNext site, financial masters or GL."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).with_name("bc_mapping_policy.py")
spec = importlib.util.spec_from_file_location("bc_mapping_policy", SOURCE)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

UTC = timezone.utc


def dt(hour, minute=0):
    return datetime(2026, 10, 18, hour, minute, tzinfo=UTC)


class MakerCheckerTests(unittest.TestCase):
    def test_independent_named_users(self):
        self.assertEqual(
            policy.assert_independent_review(" maker@example.test ", "checker@example.test"),
            ("maker@example.test", "checker@example.test"),
        )

    def test_self_approval_rejected_case_insensitive(self):
        with self.assertRaises(policy.MappingPolicyError):
            policy.assert_independent_review("Maker@example.test", "maker@EXAMPLE.test")

    def test_guest_administrator_or_absent_identity_rejected(self):
        for maker, checker in (
            ("Guest", "checker"), ("maker", "Administrator"),
            ("", "checker"), ("maker", ""), (None, "checker"),
        ):
            with self.subTest(maker=maker, checker=checker):
                with self.assertRaises(policy.MappingPolicyError):
                    policy.assert_independent_review(maker, checker)


class RevisionIntervalTests(unittest.TestCase):
    def test_disjoint_and_touching_half_open_windows(self):
        self.assertFalse(policy.intervals_overlap(dt(8), dt(10), dt(10), dt(12)))
        self.assertFalse(policy.intervals_overlap(dt(10), dt(12), dt(8), dt(10)))
        self.assertTrue(policy.intervals_overlap(dt(8), dt(10), dt(9), dt(12)))
        self.assertTrue(policy.intervals_overlap(dt(9), dt(12), dt(8), dt(10)))

    def test_open_ended_windows(self):
        self.assertTrue(policy.intervals_overlap(dt(8), None, dt(10), dt(11)))
        self.assertTrue(policy.intervals_overlap(dt(8), None, dt(10), None))

    def test_invalid_and_naive_intervals_fail(self):
        for args in (
            (dt(10), dt(10), dt(11), None),
            (dt(11), dt(10), dt(8), dt(9)),
            (datetime(2026, 10, 18, 8), None, dt(9), None),
        ):
            with self.subTest(args=args):
                with self.assertRaises(policy.MappingPolicyError):
                    policy.intervals_overlap(*args)

    def test_utc_normalization(self):
        plus_eight = timezone(timedelta(hours=8))
        self.assertFalse(policy.intervals_overlap(
            datetime(2026, 10, 18, 16, tzinfo=plus_eight),
            datetime(2026, 10, 18, 18, tzinfo=plus_eight),
            dt(10), dt(11),
        ))

    def test_posting_before_approval_or_start_is_not_effective(self):
        self.assertFalse(policy.is_effective_after_approval(dt(8), dt(15), dt(11), dt(10)))
        self.assertFalse(policy.is_effective_after_approval(dt(10), dt(15), dt(9), dt(9)))

    def test_approval_at_posting_and_end_exclusive(self):
        self.assertTrue(policy.is_effective_after_approval(dt(8), dt(10), dt(8), dt(8)))
        self.assertFalse(policy.is_effective_after_approval(dt(8), dt(10), dt(8), dt(10)))

    def test_missing_approval_timestamp_fail_closed(self):
        with self.assertRaises(policy.MappingPolicyError):
            policy.is_effective_after_approval(dt(8), None, None, dt(10))


class CostCenterSelectorTests(unittest.TestCase):
    def test_one_department_only_revision_is_unambiguous(self):
        self.assertEqual(
            policy.select_center_revision([("REV-01", "Department Only")]), "REV-01"
        )

    def test_multiple_valid_choices_require_explicit_signed_revision(self):
        options = [("A", "Explicit Selection"), ("B", "Explicit Selection")]
        with self.assertRaises(policy.MappingPolicyError):
            policy.select_center_revision(options)
        self.assertEqual(policy.select_center_revision(options, "B"), "B")

    def test_unknown_or_forged_choice_rejected(self):
        with self.assertRaises(policy.MappingPolicyError):
            policy.select_center_revision([("A", "Explicit Selection")], "FORGED")

    def test_department_only_and_explicit_choices_cannot_compete(self):
        values = [("DEPT", "Department Only"), ("OTHER", "Explicit Selection")]
        with self.assertRaises(policy.MappingPolicyError):
            policy.select_center_revision(values, "OTHER")

    def test_duplicate_and_missing_signed_candidates_rejected(self):
        for options in (
            [], [("A", "Explicit Selection"), ("A", "Explicit Selection")],
            [("", "Explicit Selection")], [("A", "Section")],
            [("A", "Explicit Selection", "bad")],
        ):
            with self.subTest(options=options):
                with self.assertRaises(policy.MappingPolicyError):
                    policy.select_center_revision(options)


if __name__ == "__main__":
    unittest.main()

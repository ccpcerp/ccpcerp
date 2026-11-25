# Copyright (c) 2026, CCPC and contributors
"""Pure, offline regression checks for the Phase 1 maintenance expense gate."""
import unittest

from ccpcerp.ccpc_erp.phase1_maintenance_use import (
    MaintenanceUseError, PILOT_BENEFICIARY, PILOT_COMPANY,
    PILOT_ITEM_GROUP, ROUTINE_MAINTENANCE, require_maintenance_pilot_use,
)


class TestPhase1MaintenanceUse(unittest.TestCase):
    def _check(self, groups, use, *, company=PILOT_COMPANY,
               beneficiary=PILOT_BENEFICIARY):
        return require_maintenance_pilot_use(company, beneficiary, groups, use)

    def test_exact_maintenance_request_passes(self):
        self.assertTrue(self._check((PILOT_ITEM_GROUP,), ROUTINE_MAINTENANCE))
        self.assertTrue(self._check((PILOT_ITEM_GROUP, PILOT_ITEM_GROUP),
                                    ROUTINE_MAINTENANCE))

    def test_missing_or_wrong_classification_fails_closed(self):
        for value in (None, "", "Capital Project / Improvement",
                      "Other / Unclassified", "routine maintenance",
                      "Routine Maintenance ", "Routine Maintenance/Capital"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(MaintenanceUseError, "Routine Maintenance"):
                    self._check((PILOT_ITEM_GROUP,), value)

    def test_different_beneficiary_departments_are_supported(self):
        # The caller must have already verified the real active Company-scoped
        # Department and signed Center mapping against the locked DB masters.
        for value in (PILOT_BENEFICIARY, "Wet Mill Plant - CCPC",
                      "Cogen Plant Department - CCPC"):
            with self.subTest(beneficiary=value):
                self.assertTrue(self._check(
                    (PILOT_ITEM_GROUP,), ROUTINE_MAINTENANCE,
                    beneficiary=value,
                ))

    def test_missing_or_noncanonical_beneficiary_is_denied(self):
        for value in (None, "", " ", " Wet Mill Plant - CCPC",
                      "Wet Mill Plant - CCPC ", 17):
            with self.subTest(beneficiary=value):
                with self.assertRaisesRegex(MaintenanceUseError, "beneficiary Department"):
                    self._check((PILOT_ITEM_GROUP,), ROUTINE_MAINTENANCE,
                                beneficiary=value)

    def test_mixed_item_groups_rejected(self):
        for groups in (
            ("Bearings", "Raw Material"), ("Office Supplies", "Bearings"),
        ):
            with self.subTest(groups=groups):
                with self.assertRaisesRegex(MaintenanceUseError, "cannot mix"):
                    self._check(groups, ROUTINE_MAINTENANCE)

    def test_other_scopes_unchanged(self):
        self.assertFalse(self._check(("Office Supplies",), None))
        self.assertFalse(self._check(("Bearings",), None,
                                     company="Ricor Mills Corporation"))

    def test_invalid_server_source_fails_closed(self):
        for groups in (None, (), [], "Bearings", (None,), ("",)):
            with self.subTest(groups=groups):
                with self.assertRaises(MaintenanceUseError):
                    self._check(groups, ROUTINE_MAINTENANCE)


if __name__ == "__main__":
    unittest.main()

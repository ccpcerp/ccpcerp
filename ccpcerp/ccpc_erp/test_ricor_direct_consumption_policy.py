# Copyright (c) 2026, CCPC and contributors
"""Pure offline negative-path tests, not native ERPNext or MariaDB acceptance."""

import unittest

from ccpcerp.ccpc_erp.ricor_direct_consumption_policy import (
    DirectConsumptionBillingItemError,
    validate_direct_consumption_billing_item,
)


def eligible_item():
    return {
        "name": "CCPC-TEST-DIRECT-CONSUMPTION-NOS",
        "item_group": "CCPC TEST Intercompany Charges",
        "stock_uom": "Nos",
        "disabled": 0,
        "is_stock_item": 0,
        "is_purchase_item": 1,
        "is_fixed_asset": 0,
        "enable_deferred_expense": 0,
        "has_variants": 0,
        "variant_of": None,
    }


def check(candidate, *, source_item_code="ITEM-00010",
          frozen_source_uom="Nos", item_group_is_leaf=True):
    return validate_direct_consumption_billing_item(
        source_item_code=source_item_code,
        frozen_source_uom=frozen_source_uom,
        candidate=candidate,
        item_group_is_leaf=item_group_is_leaf,
    )


class TestDirectConsumptionBillingItemPolicy(unittest.TestCase):
    def test_eligible_distinct_nonstock_item(self):
        self.assertEqual(check(eligible_item()), "CCPC-TEST-DIRECT-CONSUMPTION-NOS")

    def test_actual_inventory_candidate_is_ineligible(self):
        candidate = eligible_item()
        candidate.update(name="ITEM-00043", item_group="All Item Groups",
                         is_fixed_asset=1)
        with self.assertRaises(DirectConsumptionBillingItemError):
            check(candidate)

    def test_reject_stock_disabled_asset_deferred_and_variant(self):
        for field, bad_value in (
            ("disabled", 1),
            ("is_stock_item", 1),
            ("is_purchase_item", 0),
            ("is_fixed_asset", 1),
            ("enable_deferred_expense", 1),
            ("has_variants", 1),
            ("variant_of", "VARIANT-TEMPLATE"),
        ):
            with self.subTest(field=field):
                candidate = eligible_item()
                candidate[field] = bad_value
                with self.assertRaises(DirectConsumptionBillingItemError):
                    check(candidate)

    def test_reject_missing_ambiguous_or_string_flags(self):
        for field in (
            "disabled", "is_stock_item", "is_purchase_item",
            "is_fixed_asset", "enable_deferred_expense", "has_variants",
        ):
            for bad in (None, "0", "1", 2):
                with self.subTest(field=field, bad=bad):
                    candidate = eligible_item()
                    candidate[field] = bad
                    with self.assertRaises(DirectConsumptionBillingItemError):
                        check(candidate)

    def test_reject_root_or_nonleaf_item_group(self):
        for group, leaf in (("All Item Groups", True), ("CCPC TEST Intercompany Charges", False),
                            ("CCPC TEST Intercompany Charges", None), ("", True)):
            with self.subTest(group=group, leaf=leaf):
                candidate = eligible_item()
                candidate["item_group"] = group
                with self.assertRaises(DirectConsumptionBillingItemError):
                    check(candidate, item_group_is_leaf=leaf)

    def test_reject_different_uom_or_missing_frozen_uom(self):
        for uom in ("Kg", "", None):
            with self.subTest(uom=uom):
                candidate = eligible_item()
                candidate["stock_uom"] = uom
                with self.assertRaises(DirectConsumptionBillingItemError):
                    check(candidate)
        with self.assertRaises(DirectConsumptionBillingItemError):
            check(eligible_item(), frozen_source_uom="")

    def test_reject_replacing_original_item(self):
        candidate = eligible_item()
        candidate["name"] = "ITEM-00010"
        with self.assertRaises(DirectConsumptionBillingItemError):
            check(candidate)

    def test_reject_noncanonical_identity_or_missing_payload(self):
        for name in ("", " ITEM-999 ", None):
            candidate = eligible_item()
            candidate["name"] = name
            with self.subTest(name=name), self.assertRaises(DirectConsumptionBillingItemError):
                check(candidate)
        with self.assertRaises(DirectConsumptionBillingItemError):
            check(None)
        with self.assertRaises(DirectConsumptionBillingItemError):
            check(eligible_item(), source_item_code="")

if __name__ == "__main__":
    unittest.main()

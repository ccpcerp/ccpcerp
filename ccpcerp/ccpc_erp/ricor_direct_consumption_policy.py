# Copyright (c) 2026, CCPC and contributors
"""Fail-closed eligibility for a distinct direct-consumption PI billing Item.

This module performs *only* a semantic Item preflight. It does not approve an
Item, derive the original Item Group or beneficiary, authorize a mapping, save
an invoice, or authorize historical posting. The caller must separately prove
source provenance, effective signed Ricor mappings and normal ERPNext behavior.
"""

from collections.abc import Mapping


class DirectConsumptionBillingItemError(ValueError):
    """The proposed purchase-side billing representation is not eligible."""


def _require(condition, message):
    if not condition:
        raise DirectConsumptionBillingItemError(message)


def _flag(fields, name):
    value = fields.get(name)
    _require(
        type(value) in (int, bool) and value in (0, 1),
        f"Billing Item {name} must be an explicit ERPNext boolean flag.",
    )
    return bool(value)


def validate_direct_consumption_billing_item(
    *, source_item_code, frozen_source_uom, candidate, item_group_is_leaf
):
    """Return a guarded PI billing Item code; no DB calls or invoice writes.

    The source item is the stock identity retained on the Material Issue and
    immutable intercompany billing source. The candidate is a *different*
    non-stock, non-asset, purchase-enabled charge representation in the same
    real UOM, not a substitute for historic material provenance.
    """
    _require(
        isinstance(source_item_code, str) and bool(source_item_code.strip())
        and source_item_code == source_item_code.strip(),
        "A frozen original stock Item code is required.",
    )
    _require(
        isinstance(frozen_source_uom, str) and bool(frozen_source_uom.strip())
        and frozen_source_uom == frozen_source_uom.strip(),
        "A frozen original stock UOM is required.",
    )
    _require(isinstance(candidate, Mapping), "Candidate Item fields are required.")
    code = candidate.get("name")
    group = candidate.get("item_group")
    uom = candidate.get("stock_uom")
    _require(
        isinstance(code, str) and bool(code.strip()) and code == code.strip(),
        "The candidate Item code must be canonical.",
    )
    _require(code != source_item_code, "The original stock Item cannot be reused as the billing Item.")
    _require(
        isinstance(group, str) and bool(group.strip()) and group == group.strip(),
        "A specific billing Item Group is required.",
    )
    _require(group != "All Item Groups" and item_group_is_leaf is True,
             "The billing Item Group must be an eligible leaf, not the root.")
    _require(uom == frozen_source_uom,
             "Billing Item UOM differs from the original frozen source UOM.")
    _require(not _flag(candidate, "disabled"), "Disabled billing Items are not eligible.")
    _require(not _flag(candidate, "is_stock_item"), "Billing Item must be non-stock.")
    _require(_flag(candidate, "is_purchase_item"), "Billing Item must be purchase-enabled.")
    _require(not _flag(candidate, "is_fixed_asset"), "Fixed-asset Items cannot represent consumed materials.")
    _require(not _flag(candidate, "enable_deferred_expense"),
             "Deferred-expense Items require a separate accounting design.")
    _require(not _flag(candidate, "has_variants"),
             "Variant templates cannot be used as the billing Item.")
    _require(candidate.get("variant_of") in (None, ""),
             "A variant is not an approved generic billing Item.")
    return code

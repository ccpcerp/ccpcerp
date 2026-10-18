# Copyright (c) 2026, CCPC and contributors
"""Deterministic scope and revision identities for inactive B/C mapping review.

No ERPNext posting, approval, permission, or financial-master resolution occurs here.
"""
import hashlib
import json


def mapping_key(*parts):
    """Hash a typed sequence; delimiters in ERPNext names cannot create collisions."""
    if any(value is None or str(value) == "" for value in parts):
        raise ValueError("Mapping identity components must all be supplied")
    payload = json.dumps([str(part) for part in parts], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

# Copyright (c) 2026, CCPC and contributors
"""Explicit internal Finance decision: authenticated, scope-bound and single-use."""
import hashlib
import json

import frappe
from frappe.utils import now_datetime

from ccpcerp.ccpc_erp.bc_issue_runtime import _canonical, _require, _real_authority, _utc
from ccpcerp.ccpc_erp.bc_mapping_lifecycle import _locate
from ccpcerp.ccpc_erp.bc_mapping_policy import assert_independent_review
from ccpcerp.ccpc_erp.bc_mapping_workflow import _master_validated

DECISION = "CCPC Accounting Mapping Decision"
USE = "CCPC Mapping Decision Use"


def digest(*facts):
    return hashlib.sha256(json.dumps(
        facts, ensure_ascii=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()


def event_insert(doctype, fields):
    prev = getattr(frappe.flags, "ccpc_finance_event_writer", False)
    frappe.flags.ccpc_finance_event_writer = True
    try:
        return frappe.get_doc(dict(doctype=doctype, **fields)).insert(ignore_permissions=True)
    finally:
        frappe.flags.ccpc_finance_event_writer = prev


def audit(doctype, name, event, facts):
    return frappe.get_doc({
        "doctype": "Version", "ref_doctype": doctype, "docname": name,
        "data": frappe.as_json({"changed": [],
                               "ccpc_finance_event": dict(facts, event=event)}),
    }).insert(ignore_permissions=True).name


def facts_for(kind, company, scope, row, value_field):
    key = "item_group" if kind == "Expense" else "beneficiary_department"
    return (kind, company, scope.name, scope[key], row.name,
            row[value_field], row.owner, str(row.modified))


def snapshot(kind, action, company, scope, row, value_field, request_name=""):
    return digest(*facts_for(kind, company, scope, row, value_field),
                  action, request_name or "")


def approved_hash(kind, company, scope, row, value_field):
    return digest(*facts_for(kind, company, scope, row, value_field)[:7],
                  row.approved_by, row.approved_at, row.approval_reference,
                  row.effective_from, row.effective_until)


def decision_row(name):
    name = _canonical(name, "Accounting decision ID")
    table = chr(96) + "tab" + DECISION + chr(96)
    rows = frappe.db.sql(
        "SELECT name,kind,action,revision,company,mapping_scope,source_value,"
        "financial_value,maker,reviewed_modified,snapshot_hash,signed_by,"
        "signed_at,request_name,status,decision_key FROM " + table + " WHERE name=%s FOR UPDATE",
        (name,), as_dict=True,
    )
    _require(len(rows) == 1, "Unknown internal signed Accounting decision.")
    return rows[0]


def verify_decision(name, kind, action, company, scope, row, checker,
                    field, request_name=""):
    doc = decision_row(name)
    data = facts_for(kind, company, scope, row, field)
    _require(doc.status == "Signed"
             and doc.decision_key == digest(kind, action, row.name, checker, doc.snapshot_hash)
             and doc.kind == kind and doc.action == action
             and doc.revision == row.name and doc.company == company
             and doc.mapping_scope == scope.name and doc.source_value == data[3]
             and doc.financial_value == row[field] and doc.maker == row.owner
             and doc.reviewed_modified == str(row.modified)
             and doc.snapshot_hash == snapshot(
                 kind, action, company, scope, row, field, request_name)
             and doc.signed_by == checker and bool(doc.signed_at)
             and (doc.request_name or "") == (request_name or ""),
             "Signed Accounting decision does not match the current reviewed facts.")
    _require(_utc(doc.signed_at) <= _utc(now_datetime()),
             "Accounting decision signature is in the future.")
    _require(not frappe.db.exists(USE, {"decision": doc.name}),
             "Accounting decision has already been consumed.")
    return doc


def consume_decision(decision, kind, action, revision, event_ref, checker):
    # Unique decision Link prevents one decision being consumed twice.
    return event_insert(USE, {
        "decision": decision.name, "kind": kind, "action": action,
        "revision": revision, "event_ref": event_ref,
        "used_by": checker, "used_at": now_datetime(),
    }).name


@frappe.whitelist(methods=["POST"])
def sign_mapping_decision(kind, revision, expected_modified, action, request_name=None):
    """Sign exact current review; signing itself never changes mapping eligibility."""
    action = _canonical(action, "Mapping decision action")
    _require(action in ("Approve", "Retire"), "Unknown Accounting decision action.")
    expected_modified = _canonical(expected_modified, "Reviewed version token")
    rev_dt, field, master, company, scope, row, checker = _locate(
        kind, revision, "CCPC Accounting Mapping Approver",
    )
    _require(str(row.modified) == expected_modified,
             "Reviewed mapping has changed since the Checker inspected it.")
    if action == "Approve":
        _require(row.approval_state == "Draft" and not row.supersedes
                 and not row.revocation_reason and not row.approved_at
                 and not row.approved_by and not request_name,
                 "Only an original untouched Draft may be approved.")
        assert_independent_review(row.owner, checker)
        _real_authority(row.owner, "CCPC Mapping Preparer", company)
        _master_validated(master, row[field], company)
    else:
        _require(row.approval_state == "Approved" and row.effective_until is None
                 and _utc(row.effective_from) <= _utc(now_datetime())
                 and row.approved_by and row.approved_at,
                 "Only currently effective Approved mappings may be retired.")
        request_name = _canonical(request_name, "Retirement request")
        req_table = chr(96) + "tabCCPC Mapping Retirement Request" + chr(96)
        requests = frappe.db.sql(
            "SELECT name,kind,revision,company,mapping_scope,financial_value,"
            "maker,target_hash,request_state FROM " + req_table +
            " WHERE name=%s FOR UPDATE", (request_name,), as_dict=True,
        )
        _require(len(requests) == 1, "Unknown retirement request.")
        req = requests[0]
        _require(req.kind == kind and req.revision == row.name
                 and req.company == company and req.mapping_scope == scope.name
                 and req.financial_value == row[field] and req.request_state == "Pending"
                 and req.target_hash == approved_hash(kind, company, scope, row, field),
                 "Retirement request changed or does not match this mapping.")
        assert_independent_review(req.maker, checker)
    snap = snapshot(kind, action, company, scope, row, field, request_name)
    key = digest(kind, action, row.name, checker, snap)
    existing = frappe.db.get_value(DECISION, {"decision_key": key}, "name")
    if existing:
        _require(not frappe.db.exists(USE, {"decision": existing}),
                 "Prior decision already consumed.")
        return {"name": existing, "state": "Signed", "reused": True}
    data = facts_for(kind, company, scope, row, field)
    saved = event_insert(DECISION, {
        "kind": kind, "action": action, "revision": row.name,
        "company": company, "mapping_scope": scope.name,
        "source_value": data[3], "financial_value": row[field],
        "maker": row.owner, "reviewed_modified": str(row.modified),
        "snapshot_hash": snap, "signed_by": checker, "signed_at": now_datetime(),
        "request_name": request_name or None, "decision_key": key, "status": "Signed",
    })
    audit(DECISION, saved.name, "signed_internal_accounting_decision",
          {"kind": kind, "action": action, "revision": row.name,
           "company": company, "checker": checker, "fingerprint": snap})
    return {"name": saved.name, "state": "Signed", "reused": False}

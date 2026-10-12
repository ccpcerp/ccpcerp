from unittest import TestCase
from unittest.mock import patch

import frappe

import ccpcerp.hooks as hooks
from ccpcerp.ccpc_erp.transaction_deletion_protection import (
    PROTECTED_BATCH_DOCTYPE,
    TransactionDeletionProtectionMixin,
)


MODULE = "ccpcerp.ccpc_erp.transaction_deletion_protection"


def raise_validation_error(message, exc=frappe.ValidationError, **kwargs):
    raise exc(message)


class BaseTransactionDeletionRecord:
    def validate_to_delete_list(self):
        self.base_validate_to_delete_list_calls += 1

    def validate_doc_status(self):
        self.base_validate_doc_status_calls += 1
        return "validated"

    def delete_docs_linked_with_specified_company(
        self, doctype, reference_doc_names
    ):
        self.base_delete_calls.append((doctype, reference_doc_names))
        return "deleted"


class TransactionDeletionRecordHarness(
    TransactionDeletionProtectionMixin, BaseTransactionDeletionRecord
):
    def __init__(self, *, company="CCPC", selections=None, execution=None):
        self.company = company
        self.doctypes_to_delete = selections or []
        self.doctypes = execution or []
        self.base_validate_to_delete_list_calls = 0
        self.base_validate_doc_status_calls = 0
        self.base_delete_calls = []

    def get(self, fieldname):
        return getattr(self, fieldname, None)


def selection(doctype_name, company_field=None, *, execution=False):
    values = {"doctype_name": doctype_name}
    values["docfield_name" if execution else "company_field"] = company_field
    return frappe._dict(values)


def affected(invoice_name="SINV-1", batch_name="ICMB-1", **values):
    result = frappe._dict(
        invoice_name=invoice_name,
        batch_name=batch_name,
        expected_company="CCPC",
        actual_company="CCPC",
        represents_company=None,
    )
    result.update(values)
    return result


class TestTransactionDeletionProtection(TestCase):
    def setUp(self):
        self.enterContext(
            patch(f"{MODULE}.frappe.throw", side_effect=raise_validation_error)
        )
        self.enterContext(patch(f"{MODULE}._", side_effect=lambda message: message))

    def test_hooks_preserve_existing_extension_and_register_protection(self):
        self.assertEqual(
            hooks.extend_doctype_class["Purchase Order"],
            "ccpcerp.ccpc_erp.purchase_order_required_by.PurchaseOrderRequiredByMixin",
        )
        self.assertEqual(
            hooks.extend_doctype_class["Transaction Deletion Record"],
            "ccpcerp.ccpc_erp.transaction_deletion_protection.TransactionDeletionProtectionMixin",
        )
        self.assertIn(PROTECTED_BATCH_DOCTYPE, hooks.company_data_to_be_ignored)

    def test_batch_is_rejected_in_initial_selection_after_standard_validation(self):
        doc = TransactionDeletionRecordHarness(
            selections=[selection(PROTECTED_BATCH_DOCTYPE, "issuing_company")]
        )

        with self.assertRaisesRegex(frappe.ValidationError, "cannot be deleted"):
            doc.validate_to_delete_list()

        self.assertEqual(doc.base_validate_to_delete_list_calls, 1)

    def test_batch_is_rejected_in_generated_execution_selection(self):
        doc = TransactionDeletionRecordHarness(
            execution=[selection(PROTECTED_BATCH_DOCTYPE, "issuing_company", execution=True)]
        )

        with self.assertRaises(frappe.ValidationError):
            doc.validate_doc_status()

        self.assertEqual(doc.base_validate_doc_status_calls, 0)

    def test_empty_and_unrelated_selections_delegate_normally(self):
        for selections in ([], [selection("ToDo", None)]):
            with self.subTest(selections=selections):
                doc = TransactionDeletionRecordHarness(selections=selections)
                doc.validate_to_delete_list()
                self.assertEqual(doc.base_validate_to_delete_list_calls, 1)

                result = doc.validate_doc_status()
                self.assertEqual(result, "validated")
                self.assertEqual(doc.base_validate_doc_status_calls, 1)

    def test_similarly_named_doctype_is_not_blocked(self):
        doc = TransactionDeletionRecordHarness(
            selections=[selection(f"{PROTECTED_BATCH_DOCTYPE} Archive", None)]
        )
        doc.validate_to_delete_list()
        self.assertEqual(doc.base_validate_to_delete_list_calls, 1)

    def test_manual_api_and_csv_style_batch_rows_are_rejected(self):
        for company_field in (None, "issuing_company", "requesting_company"):
            with self.subTest(company_field=company_field):
                doc = TransactionDeletionRecordHarness(
                    selections=[selection(PROTECTED_BATCH_DOCTYPE, company_field)]
                )
                with self.assertRaises(frappe.ValidationError):
                    doc.validate_to_delete_list()

    def test_invoice_company_selectors_detect_linked_invoices(self):
        cases = (
            ("Sales Invoice", "company", "sales_invoice", "issuing_company"),
            ("Sales Invoice", "represents_company", "sales_invoice", "issuing_company"),
            ("Purchase Invoice", "company", "purchase_invoice", "requesting_company"),
            ("Purchase Invoice", "represents_company", "purchase_invoice", "requesting_company"),
        )
        for doctype, company_field, reference_field, expected_company in cases:
            with self.subTest(doctype=doctype, company_field=company_field):
                doc = TransactionDeletionRecordHarness(
                    company="Selected Company",
                    selections=[selection(doctype, company_field)],
                )
                with (
                    patch(f"{MODULE}.frappe.db.sql", return_value=[affected()]) as sql,
                    self.assertRaises(frappe.ValidationError),
                ):
                    doc.validate_to_delete_list()

                query = " ".join(sql.call_args.args[0].split())
                self.assertIn(f"batch.`{reference_field}`", query)
                self.assertIn(f"batch.`{expected_company}`", query)
                self.assertIn(f"invoice.`{company_field}` = %(company)s", query)
                self.assertEqual(sql.call_args.args[1], {"company": "Selected Company"})
                self.assertTrue(sql.call_args.kwargs["as_dict"])

    def test_global_invoice_selection_detects_any_linked_invoice(self):
        doc = TransactionDeletionRecordHarness(
            selections=[selection("Sales Invoice", None)]
        )
        with (
            patch(f"{MODULE}.frappe.db.sql", return_value=[affected()]) as sql,
            self.assertRaises(frappe.ValidationError),
        ):
            doc.validate_to_delete_list()

        query = " ".join(sql.call_args.args[0].split())
        self.assertNotIn("invoice.`company` =", query)
        self.assertNotIn("invoice.`represents_company` =", query)
        self.assertEqual(sql.call_args.args[1], {})

    def test_unknown_invoice_company_selector_fails_closed(self):
        doc = TransactionDeletionRecordHarness(
            selections=[selection("Sales Invoice", "unexpected_company")]
        )
        with self.assertRaisesRegex(frappe.ValidationError, "Cannot safely verify"):
            doc.validate_to_delete_list()

    def test_unrelated_invoice_company_selection_delegates(self):
        doc = TransactionDeletionRecordHarness(
            selections=[selection("Purchase Invoice", "company")]
        )
        with patch(f"{MODULE}.frappe.db.sql", return_value=[]):
            doc.validate_to_delete_list()
        self.assertEqual(doc.base_validate_to_delete_list_calls, 1)

    def test_actual_invoice_company_mismatch_does_not_remove_protection(self):
        doc = TransactionDeletionRecordHarness(
            selections=[selection("Sales Invoice", "company")]
        )
        mismatch = affected(expected_company="Issuer A", actual_company="Legacy B")
        with (
            patch(f"{MODULE}.frappe.db.sql", return_value=[mismatch]),
            self.assertRaises(frappe.ValidationError),
        ):
            doc.validate_to_delete_list()

    def test_missing_invoice_target_does_not_block_unrelated_deletion(self):
        doc = TransactionDeletionRecordHarness(
            selections=[selection("Sales Invoice", "company")]
        )
        with patch(f"{MODULE}.frappe.db.sql", return_value=[]):
            doc.validate_to_delete_list()
        self.assertEqual(doc.base_validate_to_delete_list_calls, 1)

    def test_runtime_revalidates_original_and_generated_invoice_selections(self):
        for fieldname, rows in (
            ("doctypes_to_delete", [selection("Sales Invoice", "company")]),
            ("doctypes", [selection("Purchase Invoice", "company", execution=True)]),
        ):
            with self.subTest(fieldname=fieldname):
                kwargs = {"selections" if fieldname == "doctypes_to_delete" else "execution": rows}
                doc = TransactionDeletionRecordHarness(**kwargs)
                with (
                    patch(f"{MODULE}.frappe.db.sql", return_value=[affected()]),
                    self.assertRaises(frappe.ValidationError),
                ):
                    doc.validate_doc_status()
                self.assertEqual(doc.base_validate_doc_status_calls, 0)

    def test_database_failure_during_selection_check_propagates(self):
        doc = TransactionDeletionRecordHarness(
            selections=[selection("Sales Invoice", "company")]
        )
        error = RuntimeError("database unavailable")
        with patch(f"{MODULE}.frappe.db.sql", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                doc.validate_to_delete_list()
        self.assertIs(raised.exception, error)

    def test_exact_name_guard_rejects_linked_sales_and_purchase_invoices(self):
        for doctype, reference_field, invoice_name in (
            ("Sales Invoice", "sales_invoice", "SINV-1"),
            ("Purchase Invoice", "purchase_invoice", "PINV-1"),
        ):
            with self.subTest(doctype=doctype):
                doc = TransactionDeletionRecordHarness()
                linked = frappe._dict(name="ICMB-1", **{reference_field: invoice_name})
                with (
                    patch(f"{MODULE}.frappe.db.get_value", return_value=linked) as get_value,
                    self.assertRaises(frappe.ValidationError),
                ):
                    doc.delete_docs_linked_with_specified_company(
                        doctype, [invoice_name, "UNRELATED"]
                    )

                get_value.assert_called_once_with(
                    PROTECTED_BATCH_DOCTYPE,
                    {reference_field: ["in", (invoice_name, "UNRELATED")]},
                    ["name", reference_field],
                    as_dict=True,
                    order_by=None,
                    for_update=True,
                )
                self.assertEqual(doc.base_delete_calls, [])

    def test_exact_name_guard_allows_unlinked_and_empty_invoice_names(self):
        for names in ([], ["SINV-UNRELATED"]):
            with self.subTest(names=names):
                doc = TransactionDeletionRecordHarness()
                with patch(f"{MODULE}.frappe.db.get_value", return_value=None) as get_value:
                    result = doc.delete_docs_linked_with_specified_company(
                        "Sales Invoice", names
                    )
                self.assertEqual(result, "deleted")
                self.assertEqual(doc.base_delete_calls, [("Sales Invoice", names)])
                if names:
                    get_value.assert_called_once()
                else:
                    get_value.assert_not_called()

    def test_exact_name_guard_rejects_direct_batch_deletion(self):
        doc = TransactionDeletionRecordHarness()
        with self.assertRaises(frappe.ValidationError):
            doc.delete_docs_linked_with_specified_company(
                PROTECTED_BATCH_DOCTYPE, ["ICMB-1"]
            )
        self.assertEqual(doc.base_delete_calls, [])

    def test_exact_name_database_failure_propagates_without_delegating(self):
        doc = TransactionDeletionRecordHarness()
        error = RuntimeError("locking read failed")
        with patch(f"{MODULE}.frappe.db.get_value", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                doc.delete_docs_linked_with_specified_company(
                    "Purchase Invoice", ["PINV-1"]
                )
        self.assertIs(raised.exception, error)
        self.assertEqual(doc.base_delete_calls, [])

    def test_no_company_lock_is_requested(self):
        doc = TransactionDeletionRecordHarness(
            selections=[selection("Sales Invoice", "company")]
        )
        with (
            patch(f"{MODULE}.frappe.db.sql", return_value=[]),
            patch.object(doc, "_acquire_billing_scope_lock", create=True) as company_lock,
        ):
            doc.validate_to_delete_list()
        company_lock.assert_not_called()

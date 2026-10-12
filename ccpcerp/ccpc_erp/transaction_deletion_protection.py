import frappe
from frappe import _


PROTECTED_BATCH_DOCTYPE = "Intercompany Material Billing Batch"
INVOICE_SPECS = {
    "Sales Invoice": {
        "batch_reference": "sales_invoice",
        "expected_batch_company": "issuing_company",
        "table": "tabSales Invoice",
    },
    "Purchase Invoice": {
        "batch_reference": "purchase_invoice",
        "expected_batch_company": "requesting_company",
        "table": "tabPurchase Invoice",
    },
}
INVOICE_COMPANY_COLUMNS = {
    "company": "company",
    "represents_company": "represents_company",
}


class TransactionDeletionProtectionMixin:
    def validate_to_delete_list(self):
        super().validate_to_delete_list()
        self._validate_ccpc_deletion_selections(self.get("doctypes_to_delete") or [])

    def validate_doc_status(self):
        self._validate_ccpc_deletion_selections(self.get("doctypes_to_delete") or [])
        self._validate_ccpc_deletion_selections(self.get("doctypes") or [])
        return super().validate_doc_status()

    def delete_docs_linked_with_specified_company(
        self, doctype, reference_doc_names
    ):
        if doctype == PROTECTED_BATCH_DOCTYPE:
            self._throw_protected_batch_deletion()

        spec = INVOICE_SPECS.get(doctype)
        if spec and reference_doc_names:
            linked_batch = frappe.db.get_value(
                PROTECTED_BATCH_DOCTYPE,
                {spec["batch_reference"]: ["in", tuple(reference_doc_names)]},
                ["name", spec["batch_reference"]],
                as_dict=True,
                order_by=None,
                for_update=True,
            )
            if linked_batch:
                self._throw_protected_invoice_deletion(
                    doctype,
                    linked_batch.get(spec["batch_reference"]),
                    linked_batch.name,
                )

        return super().delete_docs_linked_with_specified_company(
            doctype, reference_doc_names
        )

    def _validate_ccpc_deletion_selections(self, selections):
        for selection in selections:
            doctype = selection.get("doctype_name")
            if doctype == PROTECTED_BATCH_DOCTYPE:
                self._throw_protected_batch_deletion()

            spec = INVOICE_SPECS.get(doctype)
            if not spec:
                continue

            company_field = selection.get("company_field") or selection.get(
                "docfield_name"
            )
            affected = self._get_affected_linked_invoice(
                doctype,
                spec,
                company_field,
            )
            if affected:
                self._throw_protected_invoice_deletion(
                    doctype,
                    affected.invoice_name,
                    affected.batch_name,
                )

    def _get_affected_linked_invoice(self, doctype, spec, company_field):
        if company_field:
            company_column = INVOICE_COMPANY_COLUMNS.get(company_field)
            if not company_column:
                frappe.throw(
                    _(
                        "Cannot safely verify Transaction Deletion Record company "
                        "field {0} for {1}."
                    ).format(frappe.bold(company_field), frappe.bold(doctype))
                )
            company_condition = f"AND invoice.`{company_column}` = %(company)s"
            parameters = {"company": self.company}
        else:
            company_condition = ""
            parameters = {}

        rows = frappe.db.sql(
            f"""
            SELECT
                batch.name AS batch_name,
                invoice.name AS invoice_name,
                batch.`{spec['expected_batch_company']}` AS expected_company,
                invoice.company AS actual_company,
                invoice.represents_company AS represents_company
            FROM `tabIntercompany Material Billing Batch` batch
            INNER JOIN `{spec['table']}` invoice
                ON invoice.name = batch.`{spec['batch_reference']}`
            WHERE batch.`{spec['batch_reference']}` IS NOT NULL
              AND batch.`{spec['batch_reference']}` != ''
              {company_condition}
            LIMIT 1
            """,
            parameters,
            as_dict=True,
        )
        return rows[0] if rows else None

    def _throw_protected_batch_deletion(self):
        frappe.throw(
            _(
                "Intercompany Material Billing Batch cannot be deleted through "
                "Transaction Deletion Record."
            )
        )

    def _throw_protected_invoice_deletion(self, doctype, invoice_name, batch_name):
        frappe.throw(
            _(
                "{0} {1} is linked to Intercompany Material Billing Batch {2} "
                "and cannot be deleted through Transaction Deletion Record."
            ).format(
                frappe.bold(doctype),
                frappe.bold(invoice_name),
                frappe.bold(batch_name),
            )
        )

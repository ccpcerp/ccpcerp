function can_prepare_intercompany_billing() {
    return frappe.session.user !== "Administrator" &&
        frappe.user_roles.includes("CCPC Billing Preparer");
}

function can_approve_intercompany_billing() {
    return frappe.session.user !== "Administrator" &&
        frappe.user_roles.includes("Finance Controller");
}

function can_manage_intercompany_billing() {
    return can_prepare_intercompany_billing() ||
        can_approve_intercompany_billing();
}

async function refresh_preview(frm) {
    if (frm.is_dirty()) {
        await frm.save();
    }

    const response = await frm.call("refresh_preview");
    const result = response.message || {};

    frappe.show_alert({
        message: __(
            "Preview refreshed: {0} line(s), total {1}.",
            [
                result.line_count || 0,
                format_currency(result.total_amount || 0),
            ]
        ),
        indicator: "blue",
    });

    await frm.reload_doc();
}

async function finalize_month(frm) {
    if (frm.is_dirty()) {
        await frm.save();
    }

    frappe.confirm(
        __(
            "Final Refresh will rescan the complete billing month and move this batch to Under Review. Continue?"
        ),
        async () => {
            const response = await frm.call("finalize_month");
            const result = response.message || {};

            frappe.show_alert({
                message: __(
                    "Billing month finalized: {0} line(s), total {1}.",
                    [
                        result.line_count || 0,
                        format_currency(result.total_amount || 0),
                    ]
                ),
                indicator: "green",
            });

            await frm.reload_doc();
        }
    );
}

async function approve_and_create_invoices(frm) {
    if (frm.is_dirty()) {
        await frm.save();
    }

    frappe.confirm(
        __(
            "Approval will create matching Draft Sales Invoice and Purchase Invoice using the finalized month-end billing values. Continue?"
        ),
        async () => {
            const response = await frm.call("approve_and_create_invoices");
            const result = response.message || {};

            frappe.show_alert({
                message: __(
                    "Billing approved. Draft Sales Invoice {0} and Purchase Invoice {1} created.",
                    [
                        result.sales_invoice || "",
                        result.purchase_invoice || "",
                    ]
                ),
                indicator: "green",
            });

            await frm.reload_doc();
        }
    );
}

async function request_cancelled_invoice_correction(frm) {
    frappe.prompt(
        [{
            fieldname: "reason", label: __("Reason for Manual Correction"),
            fieldtype: "Small Text", reqd: 1,
        }],
        async ({ reason }) => {
            const response = await frm.call(
                "request_cancelled_invoice_correction", { reason }
            );
            frappe.show_alert({
                message: __("Correction review requested: {0}. Original invoices remain linked.",
                    [(response.message || {}).case || ""]),
                indicator: "orange",
            });
            await frm.reload_doc();
        },
        __("Request Cancelled Invoice Correction"),
        __("Request Review")
    );
}

async function review_cancelled_invoice_correction(frm) {
    frappe.prompt(
        [
            { fieldname: "action", label: __("Finance Decision"),
                fieldtype: "Select", options: "Approve\\nDecline", reqd: 1 },
            { fieldname: "note", label: __("Decision Note"),
                fieldtype: "Small Text", reqd: 1 },
        ],
        async ({ action, note }) => {
            const response = await frm.call(
                "review_cancelled_invoice_correction", { action, note }
            );
            frappe.show_alert({
                message: __("Correction review {0}. No replacement invoice was created.",
                    [(response.message || {}).status || ""]),
                indicator: "blue",
            });
            await frm.reload_doc();
        },
        __("Review Cancelled Invoice Correction"),
        __("Record Decision")
    );
}

frappe.ui.form.on("Intercompany Material Billing Batch", {
    refresh(frm) {
        if (frm.is_new()) {
            return;
        }
        if (frm.doc.status === "Draft" && can_prepare_intercompany_billing()) {
            frm.add_custom_button(__("Refresh Preview"),
                () => refresh_preview(frm), __("Actions"));
            frm.add_custom_button(__("Finalize Month"),
                () => finalize_month(frm), __("Actions"));
        }
        if (frm.doc.status === "Under Review" &&
            can_approve_intercompany_billing() &&
            frm.doc.finalized_by &&
            frm.doc.finalized_by.toLowerCase() !== frappe.session.user.toLowerCase()) {
            frm.add_custom_button(__("Approve & Create Invoices"),
                () => approve_and_create_invoices(frm), __("Actions"));
        }
        if (frm.doc.status === "Approved" && frm.doc.sales_invoice &&
            frm.doc.purchase_invoice) {
            if (can_prepare_intercompany_billing()) {
                frm.add_custom_button(__("Request Correction Review"),
                    () => request_cancelled_invoice_correction(frm), __("Actions"));
            }
            if (can_approve_intercompany_billing() &&
                frm.doc.finalized_by !== frappe.session.user) {
                frm.add_custom_button(__("Record Correction Decision"),
                    () => review_cancelled_invoice_correction(frm), __("Actions"));
            }
        }
    },
});

if (typeof module !== "undefined") {
    module.exports = {
        can_manage_intercompany_billing,
        can_prepare_intercompany_billing,
        can_approve_intercompany_billing,
        refresh_preview,
        finalize_month,
        approve_and_create_invoices,
        request_cancelled_invoice_correction,
        review_cancelled_invoice_correction,
    };
}

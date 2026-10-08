function can_manage_intercompany_billing() {
    return (
        frappe.session.user === "Administrator" ||
        frappe.user_roles.includes("System Manager") ||
        frappe.user_roles.includes("Finance Controller")
    );
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

frappe.ui.form.on("Intercompany Material Billing Batch", {
    refresh(frm) {
        if (!can_manage_intercompany_billing()) {
            return;
        }

        if (frm.is_new()) {
            return;
        }

        if (frm.doc.status === "Draft") {
            frm.add_custom_button(
                __("Refresh Preview"),
                () => refresh_preview(frm),
                __("Actions")
            );

            frm.add_custom_button(
                __("Finalize Month"),
                () => finalize_month(frm),
                __("Actions")
            );

            return;
        }

        if (frm.doc.status === "Under Review") {
            frm.add_custom_button(
                __("Approve & Create Invoices"),
                () => approve_and_create_invoices(frm),
                __("Actions")
            );
        }
    },
});

if (typeof module !== "undefined") {
    module.exports = {
        can_manage_intercompany_billing,
        refresh_preview,
        finalize_month,
        approve_and_create_invoices,
    };
}

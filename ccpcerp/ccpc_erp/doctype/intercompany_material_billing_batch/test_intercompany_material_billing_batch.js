const assert = require("node:assert/strict");

let handlers;
let alerts = [];
let confirmMessage = null;

global.__ = (value, args) => {
    if (!args) return value;
    return value.replace(/\{(\d+)\}/g, (_, i) => args[Number(i)]);
};

global.format_currency = (value) => `CUR ${value}`;

global.frappe = {
    session: {
        user: "finance@example.com",
    },
    user_roles: ["Finance Controller"],
    ui: {
        form: {
            on(doctype, value) {
                if (doctype === "Intercompany Material Billing Batch") {
                    handlers = value;
                }
            },
        },
    },
    show_alert(value) {
        alerts.push(value);
    },
    confirm(message, yes) {
        confirmMessage = message;
        return yes();
    },
    prompt(fields, submit) {
        if (fields.some((field) => field.fieldname === "reason")) {
            return submit({reason: "Cancelled linked invoice pair needs Finance review."});
        }
        return submit({
            action: "Approve",
            note: "Independent checker recorded manual correction approval.",
        });
    },
};

const {
    can_manage_intercompany_billing,
    refresh_preview,
    finalize_month,
    approve_and_create_invoices,
} = require("./intercompany_material_billing_batch.js");


function form({
    isNew = false,
    status = "Draft",
    dirty = false,
    callResults = {},
    finalizedBy = "billing-preparer@example.com",
} = {}) {
    return {
        doc: {
            name: "ICMB-2026-00001",
            status,
            finalized_by: finalizedBy,
            sales_invoice: status === "Approved" ? "SINV-TEST" : null,
            purchase_invoice: status === "Approved" ? "PINV-TEST" : null,
        },
        is_new: () => isNew,
        is_dirty: () => dirty,
        buttons: [],
        saved: 0,
        reloaded: 0,
        calls: [],
        add_custom_button(label, action, group) {
            this.buttons.push({ label, action, group });
        },
        async save() {
            this.saved += 1;
        },
        async reload_doc() {
            this.reloaded += 1;
        },
        async call(method) {
            this.calls.push(method);
            return {
                message: callResults[method] || {},
            };
        },
    };
}


async function run() {
    // Finance Checker can approve only, a separate Billing Preparer prepares.
    frappe.session.user = "finance@example.com";
    frappe.user_roles = ["Finance Controller"];
    assert.equal(can_manage_intercompany_billing(), true);

    // System Manager and Administrator are not normal workflow exceptions.
    frappe.user_roles = ["System Manager"];
    assert.equal(can_manage_intercompany_billing(), false);
    frappe.session.user = "Administrator";
    frappe.user_roles = [];
    assert.equal(can_manage_intercompany_billing(), false);

    frappe.session.user = "ordinary@example.com";
    frappe.user_roles = [];
    assert.equal(can_manage_intercompany_billing(), false);

    // Draft saved batch shows both preparation actions only to the preparer.
    frappe.session.user = "billing-preparer@example.com";
    frappe.user_roles = ["CCPC Billing Preparer"];
    const draft = form();
    handlers.refresh(draft);

    assert.deepEqual(
        draft.buttons.map((button) => [button.label, button.group]),
        [
            ["Refresh Preview", "Actions"],
            ["Finalize Month", "Actions"],
        ]
    );

    frappe.session.user = "finance@example.com";
    frappe.user_roles = ["Finance Controller"];
    const checkerDraft = form();
    handlers.refresh(checkerDraft);
    assert.equal(checkerDraft.buttons.length, 0);

    // New batches should not show refresh/finalize actions.
    // New batches should not show refresh/finalize actions.
    const newBatch = form({ isNew: true });
    handlers.refresh(newBatch);
    assert.equal(newBatch.buttons.length, 0);

    // Under Review: Finance Checker sees approval, preparer does not.
    frappe.session.user = "finance@example.com";
    frappe.user_roles = ["Finance Controller"];
    const underReview = form({ status: "Under Review" });
    handlers.refresh(underReview);

    assert.deepEqual(
        underReview.buttons.map((button) => [button.label, button.group]),
        [
            ["Approve & Create Invoices", "Actions"],
        ]
    );

    // Approved batches offer Finance's controlled correction decision.
    const approved = form({ status: "Approved" });
    handlers.refresh(approved);
    assert.deepEqual(approved.buttons.map((b) => b.label),
        ["Record Correction Decision"]);

    // The finalizing user cannot approve their own batch.
    const selfApproval = form({
        status: "Under Review", finalizedBy: "finance@example.com",
    });
    handlers.refresh(selfApproval);
    assert.equal(selfApproval.buttons.length, 0);

    // Billing preparer may request correction but not decide it.
    frappe.session.user = "billing-preparer@example.com";
    frappe.user_roles = ["CCPC Billing Preparer"];
    const approvedForPrep = form({status: "Approved"});
    handlers.refresh(approvedForPrep);
    assert.deepEqual(approvedForPrep.buttons.map((b) => b.label),
        ["Request Correction Review"]);

    // Controlled correction buttons call the guarded backend methods.
    const requestForm = form({
        status: "Approved",
        callResults: {
            request_cancelled_invoice_correction: {
                case: "ICCR-CI-1", status: "Requested",
            },
        },
    });
    approvedForPrep.buttons[0].action = () =>
        require("./intercompany_material_billing_batch.js")
            .request_cancelled_invoice_correction(requestForm);
    await approvedForPrep.buttons[0].action();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(requestForm.calls,
        ["request_cancelled_invoice_correction"]);
    assert.equal(requestForm.reloaded, 1);

    frappe.session.user = "finance@example.com";
    frappe.user_roles = ["Finance Controller"];
    const reviewForm = form({
        status: "Approved",
        callResults: {
            review_cancelled_invoice_correction: {
                status: "Approved for Manual Correction",
            },
        },
    });
    handlers.refresh(reviewForm);
    assert.deepEqual(reviewForm.buttons.map((b) => b.label),
        ["Record Correction Decision"]);
    await reviewForm.buttons[0].action();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(reviewForm.calls,
        ["review_cancelled_invoice_correction"]);
    assert.equal(reviewForm.reloaded, 1);

    // Non-finance users should not see actions.
    frappe.user_roles = [];
    const unauthorized = form();
    handlers.refresh(unauthorized);
    assert.equal(unauthorized.buttons.length, 0);

    // Preview refresh saves dirty form, calls backend, then reloads.
    frappe.user_roles = ["CCPC Billing Preparer"];
    alerts = [];

    const preview = form({
        dirty: true,
        callResults: {
            refresh_preview: {
                line_count: 3,
                total_amount: 12500,
            },
        },
    });

    await refresh_preview(preview);

    assert.equal(preview.saved, 1);
    assert.deepEqual(preview.calls, ["refresh_preview"]);
    assert.equal(preview.reloaded, 1);
    assert.equal(alerts.length, 1);
    assert.match(alerts[0].message, /3 line\(s\)/);
    assert.match(alerts[0].message, /12500/);

    // Finalize Month requires confirmation, calls backend, then reloads.
    alerts = [];
    confirmMessage = null;

    const finalBatch = form({
        callResults: {
            finalize_month: {
                status: "Under Review",
                line_count: 4,
                total_amount: 20000,
            },
        },
    });

    await finalize_month(finalBatch);

    assert.match(confirmMessage, /Final Refresh/);

    // frappe.confirm callback is async; allow it to complete.
    await new Promise((resolve) => setImmediate(resolve));

    assert.deepEqual(finalBatch.calls, ["finalize_month"]);
    assert.equal(finalBatch.reloaded, 1);
    assert.equal(alerts.length, 1);
    assert.match(alerts[0].message, /4 line\(s\)/);
    assert.match(alerts[0].message, /20000/);

    // Approval requires confirmation, creates both Draft invoices, then reloads.
    alerts = [];
    confirmMessage = null;

    const approvalBatch = form({
        status: "Under Review",
        dirty: true,
        callResults: {
            approve_and_create_invoices: {
                status: "Approved",
                sales_invoice: "SINV-2026-00001",
                purchase_invoice: "PINV-2026-00001",
                posting_date: "2026-10-31",
                total_amount: 20000,
            },
        },
    });

    await approve_and_create_invoices(approvalBatch);

    assert.equal(approvalBatch.saved, 1);
    assert.match(confirmMessage, /matching Draft Sales Invoice and Purchase Invoice/);

    // frappe.confirm callback is async; allow it to complete.
    await new Promise((resolve) => setImmediate(resolve));

    assert.deepEqual(
        approvalBatch.calls,
        ["approve_and_create_invoices"]
    );
    assert.equal(approvalBatch.reloaded, 1);
    assert.equal(alerts.length, 1);
    assert.match(alerts[0].message, /SINV-2026-00001/);
    assert.match(alerts[0].message, /PINV-2026-00001/);
}

run().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});

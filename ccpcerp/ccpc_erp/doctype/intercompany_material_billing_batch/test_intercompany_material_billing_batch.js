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
};

const {
    can_manage_intercompany_billing,
    refresh_preview,
    finalize_month,
} = require("./intercompany_material_billing_batch.js");


function form({
    isNew = false,
    status = "Draft",
    dirty = false,
    callResults = {},
} = {}) {
    return {
        doc: {
            name: "ICMB-2026-00001",
            status,
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
    // Finance Controller can manage billing.
    frappe.session.user = "finance@example.com";
    frappe.user_roles = ["Finance Controller"];
    assert.equal(can_manage_intercompany_billing(), true);

    // System Manager can manage billing.
    frappe.user_roles = ["System Manager"];
    assert.equal(can_manage_intercompany_billing(), true);

    // Administrator can manage billing.
    frappe.session.user = "Administrator";
    frappe.user_roles = [];
    assert.equal(can_manage_intercompany_billing(), true);

    // Ordinary user cannot manage billing.
    frappe.session.user = "ordinary@example.com";
    frappe.user_roles = [];
    assert.equal(can_manage_intercompany_billing(), false);

    // Draft saved batch shows both actions for Finance Controller.
    frappe.session.user = "finance@example.com";
    frappe.user_roles = ["Finance Controller"];

    const draft = form();
    handlers.refresh(draft);

    assert.deepEqual(
        draft.buttons.map((button) => [button.label, button.group]),
        [
            ["Refresh Preview", "Actions"],
            ["Finalize Month", "Actions"],
        ]
    );

    // New batches should not show refresh/finalize actions.
    const newBatch = form({ isNew: true });
    handlers.refresh(newBatch);
    assert.equal(newBatch.buttons.length, 0);

    // Under Review batches are frozen from refresh actions.
    const underReview = form({ status: "Under Review" });
    handlers.refresh(underReview);
    assert.equal(underReview.buttons.length, 0);

    // Non-finance users should not see actions.
    frappe.user_roles = [];
    const unauthorized = form();
    handlers.refresh(unauthorized);
    assert.equal(unauthorized.buttons.length, 0);

    // Preview refresh saves dirty form, calls backend, then reloads.
    frappe.user_roles = ["Finance Controller"];
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
}

run().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});

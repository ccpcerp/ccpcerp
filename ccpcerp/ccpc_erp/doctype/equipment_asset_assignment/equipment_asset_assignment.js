// Copyright (c) 2026, CCPC and contributors
// For license information, please see license.txt

frappe.ui.form.on("Equipment Asset Assignment", {
	setup(frm) {
		frm.set_query("equipment", () => ({ filters: { status: "Active" } }));
		frm.set_query("asset", () => ({
			filters: {
				company: frm.doc.company,
				docstatus: 1,
				status: ["not in", ["Sold", "Scrapped", "Capitalized"]],
			},
		}));
	},
});

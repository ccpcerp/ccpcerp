// Copyright (c) 2026, CCPC and contributors
// For license information, please see license.txt

frappe.ui.form.on("Equipment Registry", {
	setup(frm) {
		frm.set_query("department", () => ({
			filters: { company: frm.doc.company, disabled: 0, is_group: 0 },
		}));
		frm.set_query("section", () => ({
			filters: { company: frm.doc.company, department: frm.doc.department, disabled: 0 },
		}));
	},
	company(frm) {
		frm.set_value("department", null);
		frm.set_value("section", null);
	},
	department(frm) {
		frm.set_value("section", null);
	},
});

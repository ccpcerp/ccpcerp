// Copyright (c) 2026, CCPC and contributors
// For license information, please see license.txt

frappe.ui.form.on("CCPC Section", {
	department(frm) {
		if (!frm.doc.department) {
			frm.set_value("company", "");
			return;
		}

		frappe.db.get_value("Department", frm.doc.department, "company").then((r) => {
			frm.set_value("company", r.message?.company || "");
		});
	},
});

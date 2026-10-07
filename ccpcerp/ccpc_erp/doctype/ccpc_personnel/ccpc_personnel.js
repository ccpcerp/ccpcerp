frappe.ui.form.on("CCPC Personnel", {
	setup(frm) {
		frm.set_query("section", () => ({
			filters: { department: frm.doc.department, disabled: 0 },
		}));
	},
	department(frm) {
		if (frm.doc.section) frm.set_value("section", null);
	},
});

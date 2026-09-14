// Copyright (c) 2026, CCPC and contributors
// For license information, please see license.txt

frappe.ui.form.on("Job Order", {
	refresh(frm) {
		ccpcerp.equipment_department.initialize_row(frm, frm.doctype, frm.doc.name);
	},

	setup(frm) {
		frm.set_query("equipment", () => ({
			filters: { company: frm.doc.company, status: "Active" },
		}));
		frm.set_query("department", () => ({
			filters: { company: frm.doc.company, disabled: 0, is_group: 0 },
		}));
	},

	work_request(frm) {
		if (!frm.is_new() || !frm.doc.work_request) return;
		frappe.db.get_value(
			"Work Request",
			frm.doc.work_request,
			[
				"company",
				"equipment",
				"equipment_description",
				"asset_location",
				"department",
				"problem_description",
				"priority",
				"maintenance_section",
				"requested_by",
			],
			(r) => r && frm.set_value(r)
		);
	},

	equipment(frm) {
		if (frm.doc.work_request) return;
		if (!frm.doc.equipment) {
			ccpcerp.equipment_department.default_from_equipment(frm, frm.doctype, frm.doc.name);
			frm.set_value("equipment_description", "");
			return;
		}
		return ccpcerp.equipment_department.default_from_equipment(
			frm,
			frm.doctype,
			frm.doc.name,
			{
				on_context: (context) =>
					frm.set_value({
						equipment_description: context.equipment_description,
						company: context.company,
					}),
			}
		);
	},
});

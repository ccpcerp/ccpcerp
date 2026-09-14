const MATERIAL_REQUEST_ALLOWED_STATUSES = ["Open", "Converted to Job Order", "In Progress"];

function can_create_job_order(frm) {
	return !frm.is_new() && frm.doc.status === "Open" && !frm.doc.job_order;
}

function set_work_request_priority_requirement(frm) {
	if (frm.is_new() || frm.doc.priority) frm.__priority_required = true;
	if (frm.__priority_required === undefined) frm.__priority_required = false;
	frm.set_df_property("priority", "reqd", frm.__priority_required ? 1 : 0);
}

frappe.ui.form.on("Work Request", {
    refresh(frm) {
        toggle_fields(frm);
		set_work_request_priority_requirement(frm);
		ccpcerp.equipment_department.initialize_row(frm, frm.doctype, frm.doc.name);
		if (!frm.is_new() && MATERIAL_REQUEST_ALLOWED_STATUSES.includes(frm.doc.status)) {
			frm.add_custom_button(__("Material Request"), () => {
				frappe.model.open_mapped_doc({
					method: "ccpcerp.ccpc_erp.doctype.work_request.work_request.make_material_request",
					frm,
				});
			}, __("Create"));
		}
		if (can_create_job_order(frm)) {
			frm.add_custom_button(__("Job Order"), () => {
				frappe.model.open_mapped_doc({
					method: "ccpcerp.ccpc_erp.doctype.work_request.work_request.make_job_order",
					frm,
				});
			}, __("Create"));
		}
    },

	setup(frm) {
		frm.set_query("equipment", () => ({
			filters: { company: frm.doc.company, status: "Active" },
		}));
		frm.set_query("department", () => ({
			filters: { company: frm.doc.company, disabled: 0, is_group: 0 },
		}));
	},

    request_type(frm) {
        toggle_fields(frm);
    },
    equipment(frm) {
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
    }

});

if (typeof module !== "undefined") {
	module.exports = { can_create_job_order, set_work_request_priority_requirement };
}

function toggle_fields(frm) {
    if (frm.doc.request_type === "Equipment") {

        frm.set_df_property("equipment", "hidden", 0);
        frm.set_df_property("asset_location", "hidden", 1);

    } else {

        frm.set_df_property("equipment", "hidden", 1);
        frm.set_df_property("asset_location", "hidden", 0);

    }
}

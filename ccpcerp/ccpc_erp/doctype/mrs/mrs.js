frappe.ui.form.on("MRS", {
	refresh(frm) {
		const status = frm.doc.status || "Draft";
		const is_draft = status === "Draft";
		const is_plant_stage = status === "Pending Plant Manager";
		const is_coo_stage = status === "Pending COO";
		const user = frappe.session.user;
		const can_manage_qty =
			(is_plant_stage && (user === frm.doc.plant_manager || user === frm.doc.coo)) ||
			(is_coo_stage && user === frm.doc.coo);

		["use_department", "request_category", "equipment", "job_order", "work_request", "physical_location", "justification"].forEach(
			(fieldname) => frm.set_df_property(fieldname, "read_only", !is_draft)
		);

		frm.fields_dict.items.grid.update_docfield_property("item_code", "read_only", !is_draft);
		frm.fields_dict.items.grid.update_docfield_property("requested_qty", "read_only", !is_draft);
		frm.fields_dict.items.grid.update_docfield_property("date_needed", "read_only", !is_draft);
		frm.fields_dict.items.grid.update_docfield_property("remarks", "read_only", !is_draft);
		frm.fields_dict.items.grid.update_docfield_property("authorized_qty", "read_only", !can_manage_qty);

		frm.set_df_property(
			"bypass_other_management_approval",
			"read_only",
			!(is_plant_stage && (user === frm.doc.plant_manager || user === frm.doc.coo))
		);
		frm.set_df_property("bypass_reason", "read_only", !is_plant_stage);
		frm.set_df_property("owner_signed", "read_only", status !== "Pending Owner Signature");
		frm.set_df_property("owner_signed_date", "read_only", status !== "Pending Owner Signature");
	},
});

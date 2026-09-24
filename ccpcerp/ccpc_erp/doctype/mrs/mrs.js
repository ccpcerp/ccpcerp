async function set_requestor_context(frm) {
	if (!frm.is_new() || (frm.doc.prepared_by_name && frm.doc.requesting_department)) {
		return;
	}

	const user = frappe.session.user;
	const { message } = await frappe.db.get_value(
		"CCPC Personnel",
		{ user, active: 1 },
		["personnel_name", "department"]
	);

	if (!message) {
		return;
	}

	await frm.set_value("prepared_by", user);
	await frm.set_value("prepared_by_name", message.personnel_name || "");
	await frm.set_value("requesting_department", message.department || "");
}

frappe.ui.form.on("MRS", {
	onload(frm) {
		set_requestor_context(frm);
	},

	refresh(frm) {
		set_requestor_context(frm);

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
		frm.set_df_property("owner_signed", "read_only", status !== "Pending General Manager Signature");
		frm.set_df_property("owner_signed_date", "read_only", status !== "Pending General Manager Signature");
	},
});

frappe.ui.form.on("MRS Item", {
	async item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) {
			await frappe.model.set_value(cdt, cdn, "item_name", "");
			await frappe.model.set_value(cdt, cdn, "description", "");
			await frappe.model.set_value(cdt, cdn, "uom", "");
			return;
		}

		const { message } = await frappe.db.get_value(
			"Item",
			row.item_code,
			["item_name", "description", "stock_uom"]
		);
		if (!message) {
			return;
		}

		await frappe.model.set_value(cdt, cdn, "item_name", message.item_name || "");
		await frappe.model.set_value(cdt, cdn, "description", message.description || "");
		await frappe.model.set_value(cdt, cdn, "uom", message.stock_uom || "");
	},

	requested_qty(frm, cdt, cdn) {
		if ((frm.doc.status || "Draft") !== "Draft") {
			return;
		}

		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "authorized_qty", row.requested_qty || 0);
	},
});

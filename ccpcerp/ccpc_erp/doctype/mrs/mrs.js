async function create_material_request(frm) {
	const response = await frappe.call({
		method: "ccpcerp.ccpc_erp.mrs_procurement.make_material_request",
		args: { source_name: frm.doc.name },
		freeze: true,
		freeze_message: __("Creating Material Request..."),
	});

	const material_request = response.message && response.message.material_request;
	if (!material_request) {
		return;
	}

	await frm.reload_doc();
	frappe.set_route("Form", "Material Request", material_request);
}

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
		const is_returned_for_correction = status === "Returned for Correction";
		const is_plant_stage = status === "Pending Plant Manager";
		const is_coo_stage = status === "Pending COO";
		const user = frappe.session.user;
		const can_edit_request =
			is_draft || (is_returned_for_correction && user === frm.doc.prepared_by);
		const can_create_material_request =
			user === "Administrator" ||
			["System Manager", "Purchasing Personnel", "Purchasing Supervisor"].some((role) =>
				(frappe.user_roles || []).includes(role)
			);
		const can_manage_qty =
			(is_plant_stage && (user === frm.doc.plant_manager || user === frm.doc.coo)) ||
			(is_coo_stage && user === frm.doc.coo);

		["use_department", "request_category", "equipment", "job_order", "work_request", "physical_location", "justification"].forEach(
			(fieldname) => frm.set_df_property(fieldname, "read_only", !can_edit_request)
		);

		frm.fields_dict.items.grid.update_docfield_property("item_code", "read_only", !can_edit_request);
		frm.fields_dict.items.grid.update_docfield_property("requested_qty", "read_only", !can_edit_request);
		frm.fields_dict.items.grid.update_docfield_property("date_needed", "read_only", !can_edit_request);
		frm.fields_dict.items.grid.update_docfield_property("remarks", "read_only", !can_edit_request);
		frm.fields_dict.items.grid.update_docfield_property("authorized_qty", "read_only", !can_manage_qty);

		frm.set_df_property(
			"bypass_other_management_approval",
			"read_only",
			!(is_plant_stage && (user === frm.doc.plant_manager || user === frm.doc.coo))
		);
		frm.set_df_property("bypass_reason", "read_only", !is_plant_stage);
		frm.set_df_property("owner_signed", "read_only", status !== "Pending General Manager Signature");
		frm.set_df_property("owner_signed_date", "read_only", status !== "Pending General Manager Signature");

		if (status === "Ready for Purchasing" && !frm.doc.material_request && can_create_material_request) {
			frm.add_custom_button(
				__("Create Material Request"),
				() => create_material_request(frm),
				__("Create")
			);
		}
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
		const status = frm.doc.status || "Draft";
		const can_edit_request =
			status === "Draft" ||
			(status === "Returned for Correction" && frappe.session.user === frm.doc.prepared_by);
		if (!can_edit_request) {
			return;
		}

		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "authorized_qty", row.requested_qty || 0);
	},
});

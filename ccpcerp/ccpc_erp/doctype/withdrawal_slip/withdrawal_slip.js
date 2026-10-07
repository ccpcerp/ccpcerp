// Copyright (c) 2026, CCPC and contributors
// For license information, please see license.txt

const withdrawal_equipment_defaults_key = "__withdrawal_equipment_defaults";

const request_intent_fields = [
	"requestor",
	"required_by",
	"job_order",
	"company",
	"source_warehouse",
	"requesting_company",
	"equipment",
	"department",
	"section",
	"work_for_department",
	"work_for_section",
	"physical_location",
	"purpose",
];

function can_edit_request_intent(frm) {
	const state = frm.doc.status || "Draft";
	if (state === "Draft") return true;
	if (state !== "Returned for Correction") return false;

	return (
		frappe.session.user === frm.doc.approving_supervisor ||
		frappe.session.user === "Administrator" ||
		frappe.user_roles.includes("System Manager")
	);
}

function can_edit_stockroom(frm) {
	if ((frm.doc.status || "Draft") !== "Stockroom Review") return false;
	return (
		frappe.session.user === "Administrator" ||
		frappe.user_roles.includes("System Manager") ||
		frappe.user_roles.includes("Stockroom Personnel")
	);
}

function apply_form_permissions(frm) {
	const request_editable = can_edit_request_intent(frm);
	const stockroom_editable = can_edit_stockroom(frm);

	for (const fieldname of request_intent_fields) {
		frm.set_df_property(fieldname, "read_only", request_editable ? 0 : 1);
	}

	frm.set_df_property("source_warehouse", "read_only", request_editable || stockroom_editable ? 0 : 1);
	frm.set_df_property("received_by", "read_only", stockroom_editable ? 0 : 1);
	for (const fieldname of ["issued_by_name", "issued_by", "issued_on"]) {
		frm.set_df_property(fieldname, "read_only", 1);
	}

	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) return;

	frm.set_df_property("items", "read_only", request_editable || stockroom_editable ? 0 : 1);
	grid.cannot_add_rows = !request_editable;
	grid.cannot_delete_rows = !request_editable;

	for (const fieldname of ["item_code", "requested_qty", "remarks"]) {
		grid.update_docfield_property(fieldname, "read_only", request_editable ? 0 : 1);
	}
	for (const fieldname of [
		"item_name",
		"uom",
		"available_qty",
		"issued_qty",
		"shortage_qty",
		"returned_qty",
		"net_used_qty",
	]) {
		grid.update_docfield_property(fieldname, "read_only", 1);
	}
	for (const fieldname of [
		"issue_qty",
		"physical_discrepancy",
		"discrepancy_remarks",
		"stockroom_remarks",
	]) {
		grid.update_docfield_property(fieldname, "read_only", stockroom_editable ? 0 : 1);
	}

	grid.refresh();
}

function response_message(response) {
	return response && response.message ? response.message : response;
}

function can_create_material_issue(frm) {
	if (frm.is_new()) return false;
	if (frm.doc.status !== "Stockroom Review") return false;
	return (
		frappe.session.user === "Administrator" ||
		frappe.user_roles.includes("System Manager") ||
		frappe.user_roles.includes("Stockroom Personnel")
	);
}

async function create_material_issue(frm) {
	if (!frm.doc.received_by) {
		frappe.msgprint(__("Enter Received By before creating a Material Issue."));
		return;
	}
	if (frm.is_dirty()) {
		await frm.save();
	}

	const response = await frappe.call({
		method: "ccpcerp.ccpc_erp.withdrawal_slip_stock.make_material_issue",
		args: { withdrawal_slip: frm.doc.name },
		freeze: true,
		freeze_message: __("Preparing Material Issue..."),
	});
	const result = response_message(response) || {};
	if (!result.name) return;

	if (result.existing) {
		frappe.show_alert({
			message: __("Opening the existing draft Material Issue."),
			indicator: "blue",
		});
	}
	frappe.set_route("Form", "Stock Entry", result.name);
}

async function initialize_form_defaults(frm) {
	if (!frm.is_new()) return;
	const response = await frappe.call({
		method: "ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.get_form_defaults",
	});
	const defaults = response_message(response);
	const values = {};
	if (!frm.doc.company && defaults.company) values.company = defaults.company;
	if (!frm.doc.requesting_company && defaults.requesting_company) {
		values.requesting_company = defaults.requesting_company;
	}
	if (!frm.doc.source_warehouse && defaults.source_warehouse) {
		values.source_warehouse = defaults.source_warehouse;
	}
	if (!frm.doc.required_by) values.required_by = frappe.datetime.get_today();
	if (Object.keys(values).length) await frm.set_value(values);
}

async function load_requestor_suggestions(frm) {
	const response = await frappe.call({
		method:
			"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.get_requestor_suggestions",
	});
	const names = response_message(response) || [];
	const field = frm.fields_dict.requestor;
	if (field && field.set_data) {
		field.df.ignore_validation = 1;
		field.set_data(names);
	}
}

async function load_location_suggestions(frm) {
	const response = await frappe.call({
		method:
			"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.get_physical_location_suggestions",
		args: { company: frm.doc.company || null },
	});
	const locations = response_message(response) || [];
	const field = frm.fields_dict.physical_location;
	if (field && field.set_data) {
		field.df.ignore_validation = 1;
		field.set_data(locations);
	}
}

async function get_equipment_context(equipment) {
	if (!equipment) return null;
	return response_message(
		await frappe.db.get_value("Equipment Registry", equipment, [
			"company",
			"department",
			"section",
			"equipment_description",
			"physical_location",
			"status",
		])
	);
}

async function initialize_equipment_defaults(frm) {
	if (!frm.doc.equipment) {
		delete frm[withdrawal_equipment_defaults_key];
		return;
	}
	const context = await get_equipment_context(frm.doc.equipment);
	if (!context) return;
	frm[withdrawal_equipment_defaults_key] = {
		equipment: frm.doc.equipment,
		department: context.department,
		section: context.section,
		physical_location: context.physical_location,
	};
}

async function default_from_equipment(frm) {
	const equipment = frm.doc.equipment;
	if (!equipment) {
		delete frm[withdrawal_equipment_defaults_key];
		await frm.set_value("equipment_description", "");
		return;
	}

	const previous = frm[withdrawal_equipment_defaults_key];
	const context = await get_equipment_context(equipment);
	if (!context || frm.doc.equipment !== equipment) return;
	if (context.status !== "Active") {
		frappe.msgprint(__("Equipment must be Active for a new Withdrawal Slip."));
		return;
	}
	if (frm.doc.requesting_company && context.company !== frm.doc.requesting_company) {
		frappe.msgprint(__("Equipment must belong to the Requesting Company."));
		return;
	}

	const values = {
		equipment_description: context.equipment_description || "",
	};
		if (!frm.doc.work_for_department && context.department) {
		values.work_for_department = context.department;
	}
	if (!frm.doc.work_for_section && context.section) {
		values.work_for_section = context.section;
	}
	if (
		context.physical_location &&
		(!frm.doc.physical_location ||
			(previous && frm.doc.physical_location === previous.physical_location))
	) {
		values.physical_location = context.physical_location;
	}
	await frm.set_value(values);
	frm[withdrawal_equipment_defaults_key] = {
		equipment,
		department: context.department,
		section: context.section,
		physical_location: context.physical_location,
	};
}

async function refresh_item_row(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	if (!row.item_code || !frm.doc.source_warehouse) return;
	const response = await frappe.call({
		method:
			"ccpcerp.ccpc_erp.doctype.withdrawal_slip.withdrawal_slip.get_item_availability",
		args: {
			item_code: row.item_code,
			warehouse: frm.doc.source_warehouse,
		},
	});
	const values = response_message(response) || {};
	await frappe.model.set_value(cdt, cdn, {
		item_name: values.item_name || "",
		uom: values.uom || "",
		available_qty: values.available_qty || 0,
	});
}

function validate_requested_qty(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	const requested = flt(row.requested_qty);
	if (!requested || !row.item_code) return;

	const total = (frm.doc.items || [])
		.filter((item) => item.item_code === row.item_code)
		.reduce((sum, item) => sum + flt(item.requested_qty), 0);
	if (total <= flt(row.available_qty)) return;

	frappe.msgprint(
		__(
			"Total requested quantity for {0} cannot exceed available stock ({1}) in {2}.",
			[row.item_code, row.available_qty, frm.doc.source_warehouse]
		)
	);
	frappe.model.set_value(cdt, cdn, "requested_qty", 0);
}

function validate_issue_qty(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	const requested = flt(row.requested_qty);
	const issue = flt(row.issue_qty);

	if (issue > 0 && issue <= requested) return;

	if (issue > requested) {
		frappe.msgprint(
			__("Issue Qty cannot exceed Requested Qty ({0}).", [requested])
		);
	} else {
		frappe.msgprint(__("Issue Qty must be greater than zero."));
	}
	frappe.model.set_value(cdt, cdn, "issue_qty", requested);
}

frappe.ui.form.on("Withdrawal Slip", {
	refresh(frm) {
		apply_form_permissions(frm);
		if (can_create_material_issue(frm)) {
			frm.add_custom_button(
				__("Create Material Issue"),
				() => create_material_issue(frm),
				__("Create")
			);
		}
	},

	async onload(frm) {
		await initialize_form_defaults(frm);
		await initialize_equipment_defaults(frm);
		await load_requestor_suggestions(frm);
		await load_location_suggestions(frm);
	},

	setup(frm) {
		frm.set_query("equipment", () => ({
			filters: { company: frm.doc.requesting_company, status: "Active" },
		}));
		frm.set_query("department", () => ({
			filters: { company: frm.doc.requesting_company, disabled: 0, is_group: 0 },
		}));
		frm.set_query("section", () => ({
			filters: { department: frm.doc.department, disabled: 0 },
		}));
		frm.set_query("work_for_department", () => ({
			filters: { disabled: 0, is_group: 0 },
		}));
		frm.set_query("work_for_section", () => ({
			filters: { department: frm.doc.work_for_department, disabled: 0 },
		}));
		frm.set_query("job_order", () => ({
			filters: { company: frm.doc.company },
		}));
		frm.set_query("item_code", "items", () => ({
			filters: { disabled: 0, is_stock_item: 1 },
		}));
		frm.set_query("source_warehouse", () => ({
			filters: { company: frm.doc.company, disabled: 0, is_group: 0 },
		}));
	},

	async company(frm) {
		await frm.set_value("source_warehouse", "");
		await load_location_suggestions(frm);
	},

	async requesting_company(frm) {
		if (frm.doc.department) await frm.set_value("department", "");
		if (frm.doc.section) await frm.set_value("section", "");
	},

	async department(frm) {
		if (frm.doc.section) await frm.set_value("section", "");
	},

	async work_for_department(frm) {
		if (frm.doc.work_for_section) await frm.set_value("work_for_section", "");
	},

	async job_order(frm) {
		if (!frm.doc.job_order) return;
		const context = response_message(
			await frappe.db.get_value("Job Order", frm.doc.job_order, [
				"company",
				"equipment",
				"department",
				"asset_location",
			])
		);
		if (!context) return;

		const values = {};
		if (context.company) values.company = context.company;
		values.equipment = context.equipment || "";
		if (!frm.doc.work_for_department && context.department) {
			values.work_for_department = context.department;
		}
		if (!context.equipment && !frm.doc.physical_location && context.asset_location) {
			values.physical_location = context.asset_location;
		}
		await frm.set_value(values);
		if (context.equipment) await default_from_equipment(frm);
	},

	async equipment(frm) {
		await default_from_equipment(frm);
	},

	status(frm) {
		apply_form_permissions(frm);
	},
});

frappe.ui.form.on("Withdrawal Slip Item", {
	async item_code(frm, cdt, cdn) {
		await refresh_item_row(frm, cdt, cdn);
	},

	requested_qty: validate_requested_qty,
	issue_qty: validate_issue_qty,
});

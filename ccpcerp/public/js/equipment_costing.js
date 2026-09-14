const equipment_costing_parent_tables = {
	"Material Request": ["Material Request Item", "items"],
	"Stock Entry": ["Stock Entry Detail", "items"],
	"Purchase Invoice": ["Purchase Invoice Item", "items"],
	"Purchase Receipt": ["Purchase Receipt Item", "items"],
	"Journal Entry": ["Journal Entry Account", "accounts"],
};

frappe.provide("ccpcerp.equipment_department");

const equipment_department_provenance = "__equipment_department_provenance";

function response_message(response) {
	return response && response.message ? response.message : response;
}

function row_is_persisted(row) {
	return Boolean(row.name && !row.__islocal && !String(row.name).startsWith("new-"));
}

async function get_equipment_context(equipment) {
	if (!equipment) return null;
	return response_message(
		await frappe.db.get_value(
			"Equipment Registry",
			equipment,
			["department", "company", "status", "equipment_description"]
		)
	);
}

async function get_previous_equipment(row) {
	if (row[equipment_department_provenance]) {
		return row[equipment_department_provenance];
	}
	if (!row_is_persisted(row)) return null;
	const persisted = response_message(await frappe.db.get_value(row.doctype, row.name, "equipment"));
	const equipment = persisted && persisted.equipment;
	const context = await get_equipment_context(equipment);
	return { equipment, department: context && context.department };
}

async function initialize_equipment_department(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	const equipment = row && row.equipment;
	if (!row || !equipment) {
		if (row) delete row[equipment_department_provenance];
		return;
	}
	const context = await get_equipment_context(equipment);
	if (row.equipment !== equipment || !context) return;
	row[equipment_department_provenance] = { equipment, department: context.department };
}

async function initialize_equipment_department_form(frm) {
	await initialize_equipment_department(frm, frm.doctype, frm.doc.name);
	const child = equipment_costing_parent_tables[frm.doctype];
	if (!child) return;
	await Promise.all(
		(frm.doc[child[1]] || []).map((row) =>
			initialize_equipment_department(frm, row.doctype, row.name)
		)
	);
}

async function default_department_from_equipment(frm, cdt, cdn, options = {}) {
	const row = frappe.get_doc(cdt, cdn);
	const equipment = row.equipment;
	if (!equipment) {
		delete row[equipment_department_provenance];
		return;
	}

	const previous = await get_previous_equipment(row);
	if (row.equipment !== equipment) return;
	const context = await get_equipment_context(equipment);
	if (row.equipment !== equipment || !context) return;
	if (context.status !== "Active" || (frm.doc.company && context.company !== frm.doc.company)) {
		frappe.msgprint(__("Equipment must be Active and belong to the transaction Company."));
		return;
	}

	if (!row.department || (previous && row.department === previous.department)) {
		await frappe.model.set_value(cdt, cdn, "department", context.department);
		if (row.equipment !== equipment) return;
	}
	row[equipment_department_provenance] = { equipment, department: context.department };
	if (options.on_context && row.equipment === equipment) {
		await options.on_context(context);
	}
}

ccpcerp.equipment_department = {
	default_from_equipment: default_department_from_equipment,
	initialize_form: initialize_equipment_department_form,
	initialize_row: initialize_equipment_department,
};

if (typeof module !== "undefined") {
	module.exports = ccpcerp.equipment_department;
}

function setup_equipment_costing_queries(frm) {
	const child = equipment_costing_parent_tables[frm.doctype];
	if (frappe.meta.has_field(frm.doctype, "equipment")) {
		frm.set_query("equipment", () => ({
			filters: { company: frm.doc.company, status: "Active" },
		}));
	}
	if (frappe.meta.has_field(frm.doctype, "department")) {
		frm.set_query("department", () => ({
			filters: { company: frm.doc.company, disabled: 0, is_group: 0 },
		}));
	}
	if (!child) return;
	if (frappe.meta.has_field(child[0], "equipment")) {
		frm.set_query("equipment", child[1], () => ({
			filters: { company: frm.doc.company, status: "Active" },
		}));
	}
	if (frappe.meta.has_field(child[0], "department")) {
		frm.set_query("department", child[1], () => ({
			filters: { company: frm.doc.company, disabled: 0, is_group: 0 },
		}));
	}
}

function default_material_request_row(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	const values = {};
	if (!row.equipment && frm.doc.equipment) values.equipment = frm.doc.equipment;
	if (!row.department && frm.doc.department) {
		values.department = frm.doc.department;
	}
	if (Object.keys(values).length) frappe.model.set_value(cdt, cdn, values);
}

Object.keys(equipment_costing_parent_tables).forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		setup: setup_equipment_costing_queries,
		refresh: initialize_equipment_department_form,
		equipment(frm) {
			return default_department_from_equipment(frm, frm.doctype, frm.doc.name);
		},
	});
});

frappe.ui.form.on("Material Request Item", {
	items_add: default_material_request_row,
	equipment: default_department_from_equipment,
});

[
	"Stock Entry Detail",
	"Purchase Invoice Item",
	"Purchase Receipt Item",
	"Journal Entry Account",
].forEach((doctype) => {
	frappe.ui.form.on(doctype, { equipment: default_department_from_equipment });
});

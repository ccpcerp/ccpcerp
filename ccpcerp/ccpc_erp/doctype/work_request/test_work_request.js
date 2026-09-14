const assert = require("node:assert/strict");

let handlers;
global.__ = (value) => value;
global.ccpcerp = {
	equipment_department: {
		initialize_row() {},
		default_from_equipment() {},
	},
};
global.frappe = {
	ui: { form: { on(doctype, value) { if (doctype === "Work Request") handlers = value; } } },
	model: { open_mapped_doc() {} },
};

const { can_create_job_order } = require("./work_request.js");

function form({ isNew = false, status = "Open", jobOrder = null } = {}) {
	return {
		doc: { name: "WR-04", status, job_order: jobOrder },
		is_new: () => isNew,
		buttons: [],
		set_df_property() {},
		add_custom_button(label, action, group) { this.buttons.push({ label, group }); },
	};
}

assert.equal(can_create_job_order(form()), true);
assert.equal(can_create_job_order(form({ isNew: true })), false);
assert.equal(can_create_job_order(form({ jobOrder: "6" })), false);
for (const status of ["Converted to Job Order", "In Progress", "Closed", "Cancelled", "", "Unexpected"]) {
	assert.equal(can_create_job_order(form({ status })), false, status);
}

const eligible = form();
handlers.refresh(eligible);
assert.deepEqual(
	eligible.buttons.map((button) => [button.label, button.group]),
	[["Material Request", "Create"], ["Job Order", "Create"]]
);

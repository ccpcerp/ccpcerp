const assert = require("node:assert/strict");

global.ccpcerp = { equipment_department: { initialize_row() {}, default_from_equipment() {} } };
global.frappe = {
	ui: { form: { on() {} } },
};

const { set_job_order_priority_requirement } = require("./job_order.js");

function form({ isNew = false, priority = null } = {}) {
	return {
		doc: { priority },
		is_new: () => isNew,
		properties: {},
		set_df_property(fieldname, property, value) {
			this.properties[`${fieldname}.${property}`] = value;
		},
	};
}

const newJobOrder = form({ isNew: true });
set_job_order_priority_requirement(newJobOrder);
assert.equal(newJobOrder.properties["priority.reqd"], 1);

const historicalBlank = form();
set_job_order_priority_requirement(historicalBlank);
assert.equal(historicalBlank.properties["priority.reqd"], 0);

const populated = form({ priority: "3" });
set_job_order_priority_requirement(populated);
populated.doc.priority = null;
set_job_order_priority_requirement(populated);
assert.equal(populated.properties["priority.reqd"], 1);

const assert = require("node:assert/strict");

const docs = new Map();
const equipment = {
	OLD: { department: "Old Department", company: "CCPC", status: "Active" },
	NEW: { department: "New Department", company: "CCPC", status: "Active" },
	THIRD: { department: "Third Department", company: "CCPC", status: "Active" },
};
let persisted = {};
let deferred;

global.__ = (value) => value;
global.ccpcerp = {};
global.frappe = {
	provide() {},
	get_doc(doctype, name) {
		return docs.get(`${doctype}:${name}`);
	},
	db: {
		async get_value(doctype, name) {
			if (doctype === "Equipment Registry") {
				if (name === "DEFERRED") return deferred.promise;
				return { message: equipment[name] };
			}
			return { message: { equipment: persisted[`${doctype}:${name}`] } };
		},
	},
	model: {
		async set_value(doctype, name, fieldname, value) {
			docs.get(`${doctype}:${name}`)[fieldname] = value;
		},
	},
	meta: { has_field: () => true },
	ui: { form: { on() {} } },
	msgprint() {},
};

const defaults = require("./equipment_costing.js");

function add_row(name, values = {}) {
	const row = { doctype: "Test Row", name, __islocal: true, equipment: null, department: null, ...values };
	docs.set(`Test Row:${name}`, row);
	return row;
}

function form(row) {
	return { doc: { company: "CCPC" } };
}

async function run() {
	let row = add_row("new-1", { equipment: "NEW" });
	await defaults.default_from_equipment(form(row), row.doctype, row.name);
	assert.equal(row.department, "New Department");

	row = add_row("saved-1", {
		__islocal: false,
		equipment: "NEW",
		department: "Old Department",
	});
	persisted["Test Row:saved-1"] = "OLD";
	await defaults.default_from_equipment(form(row), row.doctype, row.name);
	assert.equal(row.department, "New Department");

	row = add_row("saved-2", {
		__islocal: false,
		equipment: "NEW",
		department: "Manual Override",
	});
	persisted["Test Row:saved-2"] = "OLD";
	await defaults.default_from_equipment(form(row), row.doctype, row.name);
	assert.equal(row.department, "Manual Override");

	row = add_row("new-2", { equipment: "OLD", department: "Old Department" });
	await defaults.initialize_row(form(row), row.doctype, row.name);
	row.equipment = "NEW";
	await defaults.default_from_equipment(form(row), row.doctype, row.name);
	assert.equal(row.department, "New Department");
	row.equipment = "THIRD";
	await defaults.default_from_equipment(form(row), row.doctype, row.name);
	assert.equal(row.department, "Third Department");

	let resolve;
	deferred = { promise: new Promise((done) => (resolve = done)) };
	row = add_row("new-3", { equipment: "DEFERRED", department: null });
	const pending = defaults.default_from_equipment(form(row), row.doctype, row.name);
	row.equipment = "THIRD";
	resolve({ message: { department: "Stale Department", company: "CCPC", status: "Active" } });
	await pending;
	assert.equal(row.department, null);

	row = add_row("new-4", { equipment: "OLD", department: "Manual Override" });
	await defaults.initialize_row(form(row), row.doctype, row.name);
	row.equipment = null;
	await defaults.default_from_equipment(form(row), row.doctype, row.name);
	assert.equal(row.department, "Manual Override");
	assert.equal(row.__equipment_department_provenance, undefined);
}

run().catch((error) => {
	console.error(error);
	process.exitCode = 1;
});

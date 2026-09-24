// Copyright (c) 2026, CCPC and contributors
// For license information, please see license.txt

frappe.listview_settings["MRS"] = {
	onload(listview) {
		// The requestor-facing procurement labels are intentionally descriptive.
		// Reserve enough room so statuses such as "Material Request Submitted"
		// remain fully readable instead of being truncated in List View.
		listview.column_max_widths.procurement_status = 260;
	},
};

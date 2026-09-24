// Copyright (c) 2026, CCPC and contributors
// For license information, please see license.txt

frappe.listview_settings["MRS"] = {
	onload(listview) {
		// The requestor-facing procurement labels are intentionally descriptive.
		// Reserve enough room so statuses such as "Material Request Submitted"
		// remain fully readable instead of being truncated in List View.
		listview.column_max_widths.procurement_status = 260;

		// Frappe caps indicator pills in List View at 150px. Apply a scoped
		// override only while rendering the MRS list so the full procurement
		// status remains visible without affecting other doctypes.
		$(listview.page.wrapper).addClass("ccpc-mrs-list");
		if (!document.getElementById("ccpc-mrs-list-style")) {
			$("<style>", { id: "ccpc-mrs-list-style" })
				.text(
					".ccpc-mrs-list .list-row-col[data-fieldname='procurement_status'] .indicator-pill {" +
						"max-width: none !important;" +
						"}"
				)
				.appendTo("head");
		}
	},
};

frappe.query_reports["CCPC Item Stock Inquiry"] = {
	onload: (report) => {
		if (!frappe.user.has_role("System Manager")) {
			const blocked_actions = [
				__("Edit"),
				__("Add Column"),
				__("Setup Auto Email"),
				__("User Permissions"),
			];
			report.page.menu.find(".dropdown-item").each(function () {
				if (blocked_actions.includes($(this).text().trim())) {
					$(this).remove();
				}
			});
		}
	},

	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => ({
				filters: {
					company: frappe.query_report.get_filter_value("company"),
					is_group: 0,
				},
			}),
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
			get_query: () => ({
				filters: {
					is_stock_item: 1,
					disabled: 0,
				},
			}),
		},
		{
			fieldname: "search_item",
			label: __("Search Item"),
			fieldtype: "Data",
			description: __("Search Item Code, Item Name, or Description"),
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "below_maintaining_only",
			label: __("Below Maintaining Balance Only"),
			fieldtype: "Check",
			default: 0,
		},
	],
};

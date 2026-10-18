// Draft baseline: not a complete maintenance-cost or repair-vs-replace report.
frappe.query_reports["CCPC Equipment Maintenance Baseline"] = {
  filters: [
    {
      fieldname: "company",
      label: __("Company"),
      fieldtype: "Link",
      options: "Company",
      reqd: 1,
      default: frappe.defaults.get_user_default("Company"),
    },
    {
      fieldname: "equipment",
      label: __("Equipment"),
      fieldtype: "Link",
      options: "Equipment Registry",
      get_query: () => ({
        filters: { company: frappe.query_report.get_filter_value("company") },
      }),
    },
    { fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
    { fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
  ],
};

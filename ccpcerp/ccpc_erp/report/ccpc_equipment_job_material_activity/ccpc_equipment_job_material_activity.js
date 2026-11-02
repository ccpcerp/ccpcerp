// Gross original equipment issue value and UNPRICED Job Order activity are separate.
frappe.query_reports["CCPC Equipment Job Material Activity"] = {
  filters: [
    {
      fieldname: "company",
      label: __("Issuing Company"),
      fieldtype: "Link", options: "Company", reqd: 1,
      default: frappe.defaults.get_user_default("Company"),
    },
    {
      fieldname: "equipment",
      label: __("Equipment (optional; filtered view excludes other unattributed issues)"),
      fieldtype: "Link", options: "Equipment Registry",
      get_query: () => ({
        filters: { company: frappe.query_report.get_filter_value("company") },
      }),
    },
    {
      fieldname: "from_date", label: __("From Date"), fieldtype: "Date",
      reqd: 1, default: frappe.datetime.month_start(),
    },
    {
      fieldname: "to_date", label: __("To Date"), fieldtype: "Date",
      reqd: 1, default: frappe.datetime.month_end(),
    },
  ],
};

// Read-only source register: do not aggregate SLE issue value plus invoice billing.
frappe.query_reports["CCPC Intercompany Withdrawal Billing Status"] = {
  filters: [
    {
      fieldname: "issuing_company",
      label: __("Issuing Company"),
      fieldtype: "Link",
      options: "Company",
      reqd: 1,
      default: frappe.defaults.get_user_default("Company"),
    },
    {
      fieldname: "requesting_company",
      label: __("Requesting Company"),
      fieldtype: "Link",
      options: "Company",
      reqd: 1,
    },
    {
      fieldname: "from_date",
      label: __("From Date"),
      fieldtype: "Date",
      reqd: 1,
      default: frappe.datetime.month_start(),
    },
    {
      fieldname: "to_date",
      label: __("To Date"),
      fieldtype: "Date",
      reqd: 1,
      default: frappe.datetime.month_end(),
    },
  ],
};

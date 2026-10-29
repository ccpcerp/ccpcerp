// Original issue-time beneficiary only. Requester/Section are NOT frozen on Stock Entry.
frappe.query_reports["CCPC Department Material Consumption"] = {
  filters: [
    {
      fieldname: "company",
      label: __("Issuing Company"),
      fieldtype: "Link",
      options: "Company",
      reqd: 1,
      default: frappe.defaults.get_user_default("Company"),
    },
    {
      fieldname: "department",
      label: __("Beneficiary Department (optional; includes conflicts)"),
      fieldtype: "Link",
      options: "Department",
      get_query: () => ({
        filters: { company: frappe.query_report.get_filter_value("company") },
      }),
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

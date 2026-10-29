// Two independent accounting perspectives; do not add gross stock source and posted GL.
frappe.query_reports["CCPC Company Stock PNL GL Reconciliation"] = {
  filters: [
    {
      fieldname: "company",
      label: __("Company"),
      fieldtype: "Link", options: "Company",
      reqd: 1, default: frappe.defaults.get_user_default("Company"),
    },
    {
      fieldname: "from_date", label: __("From Posting Date"),
      fieldtype: "Date", reqd: 1,
      default: frappe.datetime.month_start(),
    },
    {
      fieldname: "to_date", label: __("To Posting Date"),
      fieldtype: "Date", reqd: 1,
      default: frappe.datetime.month_end(),
    },
  ],
};

// This Page is NOT a generic SQL/report API. Server validates role and four Companies.
// Native ERPNext Report routes retain their original strict permissions.
frappe.pages["ccpc-group-executive-reporting"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper, title: __("CCPC Group Executive Reporting"), single_column: true,
  });
  const choices = [
    "Cagayan Corn Products Corporation",
    "Ricor Mills Corporation",
    "GSL Realty Corporation",
    "Woodland Farms Corporation",
  ];
  const add = (label, fieldname, fieldtype, options, value) =>
    page.add_field({ label: __(label), fieldname, fieldtype, options,
      default: value, reqd: 1, change: () => {} });
  const report = add("Report", "group_report", "Select",
    "department\\nequipment\\nintercompany\\nstock_gl", "department");
  const company = add("Issuing Company", "issuer", "Select", choices.join("\n"), choices[0]);
  const requester = add("Requesting Company (intercompany only)", "requester", "Select",
    choices.join("\n"), choices[1]);
  const from_date = add("From Date", "from_date", "Date", null, frappe.datetime.month_start());
  const to_date = add("To Date", "to_date", "Date", null, frappe.datetime.month_end());
  const result = $('<div class="ccpc-group-results" style="overflow-x:auto;margin-top:20px"></div>');
  $(page.body).append(result);

  page.set_primary_action(__("Run read-only report"), async function () {
    result.empty();
    const key = report.get_value();
    const filters = { from_date: from_date.get_value(), to_date: to_date.get_value() };
    if (key === "intercompany") {
      filters.issuing_company = company.get_value();
      filters.requesting_company = requester.get_value();
    } else {
      filters.company = company.get_value();
    }
    if (!key || !filters.from_date || !filters.to_date ||
        (key === "intercompany" && filters.issuing_company === filters.requesting_company)) {
      frappe.msgprint(__("Select a valid report, date range and distinct Companies."));
      return;
    }
    const response = await frappe.call({
      method: "ccpcerp.ccpc_erp.executive_reporting.run_group_report",
      type: "POST", args: { report_key: key, filters: JSON.stringify(filters) },
      freeze: true, freeze_message: __("Loading authorized report"),
    });
    const payload = response.message || {};
    const columns = payload.columns || [];
    const rows = payload.rows || [];
    const table = $('<table class="table table-bordered table-striped"></table>');
    const head = $("<thead></thead>");
    const hr = $("<tr></tr>");
    columns.forEach(c => $("<th></th>").text(c.label || c.fieldname).appendTo(hr));
    head.append(hr).appendTo(table);
    const body = $("<tbody></tbody>");
    rows.forEach(record => {
      const tr = $("<tr></tr>");
      columns.forEach(c => {
        const raw = record[c.fieldname];
        // DOM .text(), never HTML interpretation of invoice/customer/source data.
        $("<td></td>").text(raw === null || raw === undefined ? "" : String(raw)).appendTo(tr);
      });
      body.append(tr);
    });
    table.append(body);
    result.empty().append($("<p></p>").text(__("Rows: ") + rows.length)).append(table);
  });
};

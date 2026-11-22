frappe.pages["ccpc-phase1-finance"].on_page_load = async function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: __("Phase 1 Finance Mappings"),
    single_column: true,
  });
  const container = $("<div class='p-4'></div>").appendTo(page.body);
  const call = async (action, args = {}, freeze = true) => {
    const response = await frappe.call({
      method: `ccpcerp.ccpc_erp.phase1_finance_onboarding.${action}`,
      type: "POST", args, freeze,
    });
    return response.message;
  };
  const access = await call("get_pilot_context");
  if (!access.companies.length) {
    $("<p></p>").text(__("Ask your Finance administrator for an explicit Company permission.")).appendTo(container);
    return;
  }

  const filters = $("<div class='row'></div>").appendTo(container);
  const add_field = (id, label, input) => {
    const field = $("<div class='form-group col-md-3'></div>").appendTo(filters);
    const caption = $("<label></label>").attr("for", id).text(__(label)).appendTo(field);
    input.attr("id", id).addClass("form-control").appendTo(field);
    return caption;
  };
  const add_option = (select, value, label) =>
    $("<option></option>").val(value).text(label).appendTo(select);
  const company = $("<select></select>");
  add_field("ccpc-pilot-company", "Company", company);
  access.companies.forEach(name => add_option(company, name, name));
  const kind = $("<select></select>");
  add_field("ccpc-pilot-kind", "Mapping type", kind);
  add_option(kind, "Expense", __("Expense Account"));
  add_option(kind, "Center", __("Cost Center"));

  const result = $("<div class='mt-4'></div>").attr("aria-live", "polite");
  const scope = () => ({ company: company.val(), kind: kind.val(), source: source.val() });
  const suggestions = (field, label) => {
    const input = $("<input type='text' autocomplete='off' maxlength='140'>");
    const id = `ccpc-pilot-${field}`;
    const caption = add_field(id, label, input);
    const options = $("<datalist></datalist>").attr("id", `${id}-options`).appendTo(filters);
    input.attr("list", `${id}-options`);
    let request = 0;
    const search = frappe.utils.debounce(async () => {
      const current = ++request;
      const { company, kind } = scope();
      const rows = await call("search_pilot_master", { company, kind, field, text: input.val() }, false);
      if (current !== request) return;
      options.empty();
      rows.forEach(name => $("<option></option>").val(name).appendTo(options));
    }, 250);
    input.on("focus", search).on("input", () => {
      request += 1;
      result.empty();
      search();
    });
    return { input, caption, reset() { request += 1; input.val(""); options.empty(); } };
  };
  const source_field = suggestions("source", "Item Group");
  const source = source_field.input;
  const value_field = access.can_propose ? suggestions("value", "Expense Account") : null;
  company.add(kind).on("change", () => {
    source_field.reset();
    value_field?.reset();
    const expense = kind.val() === "Expense";
    source_field.caption.text(expense ? __("Item Group") : __("Beneficiary Department"));
    value_field?.caption.text(expense ? __("Expense Account") : __("Cost Center"));
    result.empty();
  });

  const facts = (parent, fields) => {
    const list = $("<dl></dl>").appendTo(parent);
    fields.forEach(([label, value]) => {
      $("<dt></dt>").text(__(label)).appendTo(list);
      $("<dd></dd>").text(value ?? "").appendTo(list);
    });
  };
  const button = (label, action, parent) =>
    $("<button type='button' class='btn btn-default mr-2'></button>").text(__(label)).on("click", action).appendTo(parent);

  const show_coverage = async () => {
    result.empty();
    const filters = scope();
    if (!filters.source) return frappe.msgprint(__("Select the exact mapping source."));
    const coverage = await call("get_pilot_coverage", filters);
    facts(result, [
      ["Active mapping", coverage.active?.value ?? __("No active mapping")],
      ["Active revision", coverage.active?.revision],
      ["Draft revision", coverage.draft],
    ].filter(([, value]) => value));

    if (!access.can_review || !coverage.draft) return;
    button("Review draft", async () => {
      const review = await call("review_pilot_mapping", { kind: filters.kind, revision: coverage.draft });
      result.empty();
      facts(result, [
        ["Company", review.company], ["Source", review.source], ["Proposed value", review.value],
        ["Revision", review.name], ["Prepared by", review.owner], ["Status", __(review.state)],
        ["Effective from", review.effective_from || __("At approval")], ["Effective until", review.effective_until],
      ].filter(([, value]) => value));
      if (review.change !== "New" || review.state !== "Draft" || review.owner === frappe.session.user) return;

      button("Sign and approve", () => frappe.confirm(
        __("Sign and approve this exact reviewed mapping? It can be used for Withdrawal posting once effective."),
        async () => {
          await call("sign_and_approve_initial_mapping", {
            kind: filters.kind, revision: review.name, expected_modified: review.modified,
          });
          await show_coverage();
        },
      ), result);
    }, result);
  };

  button("Check coverage", show_coverage, container);
  if (value_field) {
    button("Propose mapping", async () => {
      const args = { ...scope(), value: value_field.input.val() };
      if (!args.source || !args.value) return frappe.msgprint(__("Select a source and a proposed mapping value."));
      await call("propose_pilot_mapping", args);
      await show_coverage();
    }, container);
  }
  result.appendTo(container);
};

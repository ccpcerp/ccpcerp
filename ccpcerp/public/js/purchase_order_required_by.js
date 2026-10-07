function ccpc_allow_past_required_by(frm) {
	const schedule_date = frm.fields_dict?.schedule_date;
	if (!schedule_date) return;

	// ERPNext normally limits the PO Required By picker to the PO Date or later.
	// CCPC preserves the original requirement date even when procurement is delayed.
	schedule_date.df.min_date = null;
	schedule_date.datepicker?.update({ minDate: null });
}

function ccpc_show_overdue_required_by(frm) {
	if (!frm.doc.transaction_date || !frm.doc.items?.length) return;

	const has_overdue_required_by = frm.doc.items.some(
		(row) => row.schedule_date && row.schedule_date < frm.doc.transaction_date
	);

	if (has_overdue_required_by) {
		frm.set_intro(
			__(
				"One or more Required By dates are earlier than the Purchase Order Date. This is allowed so the original requirement date remains visible."
			),
			"orange"
		);
	}
}

function ccpc_refresh_required_by_behavior(frm) {
	// Run after ERPNext's standard handlers, which apply the normal minDate.
	setTimeout(() => {
		ccpc_allow_past_required_by(frm);
		ccpc_show_overdue_required_by(frm);
	}, 0);
}

frappe.ui.form.on("Purchase Order", {
	refresh(frm) {
		ccpc_refresh_required_by_behavior(frm);
	},

	transaction_date(frm) {
		ccpc_refresh_required_by_behavior(frm);
	},
});

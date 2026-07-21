frappe.ui.form.on("Work Request", {
    refresh(frm) {
        toggle_fields(frm);
    },

    request_type(frm) {
        toggle_fields(frm);
    },
    equipment(frm) {
        if (frm.doc.equipment) {
            frappe.db.get_value(
                "Equipment Registry",
                frm.doc.equipment,
                "equipment_description",
                (r) => {
                    if (r) {
                        frm.set_value(
                            "equipment_description",
                            r.equipment_description
                        );
                    }
                }
            );
        } else {
            frm.set_value("equipment_description", "");
        }
    }

});

function toggle_fields(frm) {
    if (frm.doc.request_type === "Equipment") {

        frm.set_df_property("equipment", "hidden", 0);
        frm.set_df_property("asset_location", "hidden", 1);

    } else {

        frm.set_df_property("equipment", "hidden", 1);
        frm.set_df_property("asset_location", "hidden", 0);

    }
}
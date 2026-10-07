import frappe
from frappe import _


COMPANIES = {
    "Cagayan Corn Products Corporation": "CCPC",
    "Ricor Mills Corporation": "RMC",
    "GSL Realty Corporation": "GSL",
    "Woodland Farms Corporation": "WFC",
}


def _account_specs(abbr):
    return (
        {
            "account_number": "1312",
            "account_name": "Intercompany Receivable",
            "parent_account": f"1310 - Accounts Receivable - Affiliates - {abbr}",
            "root_type": "Asset",
            "account_type": "Receivable",
            "is_group": 0,
        },
        {
            "account_number": "2120",
            "account_name": "Accounts Payable - Affiliates",
            "parent_account": f"2001 - Current Liabilities - {abbr}",
            "root_type": "Liability",
            "account_type": "",
            "is_group": 1,
        },
        {
            "account_number": "2121",
            "account_name": "Intercompany Payable",
            "parent_account": f"2120 - Accounts Payable - Affiliates - {abbr}",
            "root_type": "Liability",
            "account_type": "Payable",
            "is_group": 0,
        },
        {
            "account_number": "5150",
            "account_name": "Intercompany Materials Expense",
            "parent_account": f"5100 - Operating Expenses - {abbr}",
            "root_type": "Expense",
            "account_type": "",
            "is_group": 0,
        },
        {
            "account_number": "6050",
            "account_name": "Intercompany Material Charges",
            "parent_account": f"6000 - Other Income - {abbr}",
            "root_type": "Income",
            "account_type": "",
            "is_group": 0,
        },
    )


def execute():
    for company, abbr in COMPANIES.items():
        if not frappe.db.exists("Company", company):
            _fail(_("Required company {0} does not exist.").format(company))

        for spec in _account_specs(abbr):
            _ensure_account(company, abbr, spec)


def _ensure_account(company, abbr, spec):
    expected_name = f"{spec['account_number']} - {spec['account_name']} - {abbr}"

    matches = frappe.get_all(
        "Account",
        filters={
            "company": company,
            "account_number": spec["account_number"],
        },
        fields=[
            "name",
            "account_name",
            "account_number",
            "company",
            "parent_account",
            "root_type",
            "account_type",
            "is_group",
            "disabled",
        ],
    )

    if len(matches) > 1:
        _fail(
            _("Multiple accounts use number {0} for company {1}.").format(
                spec["account_number"], company
            )
        )

    if matches:
        _validate_account(matches[0], company, expected_name, spec)
        return

    name_collision = frappe.db.exists("Account", expected_name)
    if name_collision:
        _fail(
            _("Account {0} already exists but is not the expected account for company {1}.").format(
                expected_name, company
            )
        )

    parent = frappe.db.get_value(
        "Account",
        spec["parent_account"],
        ["name", "company", "is_group", "disabled"],
        as_dict=True,
    )
    if not parent:
        _fail(
            _("Required parent account {0} does not exist.").format(
                spec["parent_account"]
            )
        )

    if parent.company != company or not parent.is_group or parent.disabled:
        _fail(
            _("Parent account {0} is incompatible with the required intercompany structure.").format(
                spec["parent_account"]
            )
        )

    doc = frappe.get_doc(
        {
            "doctype": "Account",
            "company": company,
            "account_number": spec["account_number"],
            "account_name": spec["account_name"],
            "parent_account": spec["parent_account"],
            "account_type": spec["account_type"],
            "is_group": spec["is_group"],
        }
    )
    doc.insert(ignore_permissions=True)

    created = frappe.db.get_value(
        "Account",
        doc.name,
        [
            "name",
            "account_name",
            "account_number",
            "company",
            "parent_account",
            "root_type",
            "account_type",
            "is_group",
            "disabled",
        ],
        as_dict=True,
    )
    _validate_account(created, company, expected_name, spec)


def _validate_account(account, company, expected_name, spec):
    expected = {
        "name": expected_name,
        "account_name": spec["account_name"],
        "account_number": spec["account_number"],
        "company": company,
        "parent_account": spec["parent_account"],
        "root_type": spec["root_type"],
        "account_type": spec["account_type"],
        "is_group": spec["is_group"],
        "disabled": 0,
    }

    conflicts = [
        field
        for field, expected_value in expected.items()
        if account.get(field) != expected_value
    ]

    if conflicts:
        _fail(
            _("Account {0} conflicts with the required intercompany configuration: {1}.").format(
                account.name, ", ".join(conflicts)
            )
        )


def _fail(message):
    frappe.throw(message, frappe.ValidationError)

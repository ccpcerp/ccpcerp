import frappe
from frappe import _
from frappe.utils import cint


COMPANIES = (
    ("Cagayan Corn Products Corporation", "CCPC"),
    ("Ricor Mills Corporation", "RMC"),
    ("GSL Realty Corporation", "GSL"),
    ("Woodland Farms Corporation", "WFC"),
)

CUSTOMER_GROUP = "Intercompany"
SUPPLIER_GROUP = "Intercompany"
TERRITORY = "Philippines"

RECEIVABLE_CODE = "1312"
PAYABLE_CODE = "2121"


def execute():
    _validate_company_configuration()
    _ensure_party_group(
        "Customer Group",
        "customer_group_name",
        CUSTOMER_GROUP,
        "parent_customer_group",
        "All Customer Groups",
    )
    _ensure_party_group(
        "Supplier Group",
        "supplier_group_name",
        SUPPLIER_GROUP,
        "parent_supplier_group",
        "All Supplier Groups",
    )

    if not frappe.db.exists("Territory", TERRITORY):
        frappe.throw(
            _("Required Territory {0} does not exist.").format(
                frappe.bold(TERRITORY)
            )
        )

    for company, _abbr in COMPANIES:
        _ensure_customer(company)
        _ensure_supplier(company)


def _validate_company_configuration():
    seen_names = set()
    seen_abbrs = set()

    for company, expected_abbr in COMPANIES:
        if company in seen_names or expected_abbr in seen_abbrs:
            frappe.throw(_("Duplicate company configuration in intercompany party patch."))

        seen_names.add(company)
        seen_abbrs.add(expected_abbr)

        actual_abbr = frappe.db.get_value("Company", company, "abbr")
        if not actual_abbr:
            frappe.throw(
                _("Required Company {0} does not exist.").format(
                    frappe.bold(company)
                )
            )

        if actual_abbr != expected_abbr:
            frappe.throw(
                _("Company {0} must use abbreviation {1}, found {2}.").format(
                    frappe.bold(company),
                    frappe.bold(expected_abbr),
                    frappe.bold(actual_abbr),
                )
            )


def _ensure_party_group(
    doctype,
    name_field,
    group_name,
    parent_field,
    parent_name,
):
    if frappe.db.exists(doctype, group_name):
        is_group = frappe.db.get_value(doctype, group_name, "is_group")
        if cint(is_group):
            frappe.throw(
                _("{0} {1} must be a leaf group, not a parent group.").format(
                    doctype,
                    frappe.bold(group_name),
                )
            )
        return

    frappe.get_doc(
        {
            "doctype": doctype,
            name_field: group_name,
            parent_field: parent_name,
            "is_group": 0,
        }
    ).insert(ignore_permissions=True)


def _company_abbr(company):
    for configured_company, abbr in COMPANIES:
        if configured_company == company:
            return abbr

    frappe.throw(
        _("Company {0} is not configured for intercompany billing.").format(
            frappe.bold(company)
        )
    )


def _other_companies(represented_company):
    return [
        company
        for company, _abbr in COMPANIES
        if company != represented_company
    ]


def _account_name(code, company):
    return f"{code} - {'Intercompany Receivable' if code == RECEIVABLE_CODE else 'Intercompany Payable'} - {_company_abbr(company)}"


def _validate_account(account, company, account_type):
    info = frappe.db.get_value(
        "Account",
        account,
        ["company", "account_type", "is_group"],
        as_dict=True,
    )

    if not info:
        frappe.throw(
            _("Required Account {0} does not exist.").format(
                frappe.bold(account)
            )
        )

    if info.company != company:
        frappe.throw(
            _("Account {0} does not belong to Company {1}.").format(
                frappe.bold(account),
                frappe.bold(company),
            )
        )

    if cint(info.is_group):
        frappe.throw(
            _("Account {0} must be a ledger account.").format(
                frappe.bold(account)
            )
        )

    if info.account_type != account_type:
        frappe.throw(
            _("Account {0} must have Account Type {1}.").format(
                frappe.bold(account),
                frappe.bold(account_type),
            )
        )


def _resolve_existing_party(
    doctype,
    name_field,
    internal_field,
    represented_company,
):
    fields = [
        "name",
        name_field,
        internal_field,
        "represents_company",
        "disabled",
    ]

    represented = frappe.db.get_value(
        doctype,
        {"represents_company": represented_company},
        fields,
        as_dict=True,
    )

    same_name = frappe.db.get_value(
        doctype,
        {name_field: represented_company},
        fields,
        as_dict=True,
    )

    if represented:
        if represented.get(name_field) != represented_company:
            frappe.throw(
                _(
                    "{0} {1} already represents Company {2}. "
                    "Review it manually before running this patch."
                ).format(
                    doctype,
                    frappe.bold(represented.name),
                    frappe.bold(represented_company),
                )
            )

        if not cint(represented.get(internal_field)):
            frappe.throw(
                _(
                    "{0} {1} represents Company {2} but is not marked internal."
                ).format(
                    doctype,
                    frappe.bold(represented.name),
                    frappe.bold(represented_company),
                )
            )

        if cint(represented.disabled):
            frappe.throw(
                _(
                    "{0} {1} represents Company {2} but is disabled. "
                    "Review it manually before running this patch."
                ).format(
                    doctype,
                    frappe.bold(represented.name),
                    frappe.bold(represented_company),
                )
            )

        if same_name and same_name.name != represented.name:
            frappe.throw(
                _(
                    "Conflicting {0} named {1} already exists while {2} "
                    "also represents that company."
                ).format(
                    doctype,
                    frappe.bold(represented_company),
                    frappe.bold(represented.name),
                )
            )

        return represented.name

    if same_name:
        frappe.throw(
            _(
                "{0} named {1} already exists but is not the configured "
                "internal party for that company. It will not be converted automatically."
            ).format(
                doctype,
                frappe.bold(represented_company),
            )
        )

    return None


def _ensure_allowed_companies(doc, represented_company):
    existing = {
        row.company
        for row in (doc.get("companies") or [])
        if row.company
    }

    if represented_company in existing:
        frappe.throw(
            _(
                "{0} {1} incorrectly allows its represented company {2} "
                "to transact with itself."
            ).format(
                doc.doctype,
                frappe.bold(doc.name or represented_company),
                frappe.bold(represented_company),
            )
        )

    for company in _other_companies(represented_company):
        if company not in existing:
            doc.append("companies", {"company": company})
            existing.add(company)


def _ensure_party_accounts(
    doc,
    represented_company,
    account_code,
    account_type,
):
    by_company = {}

    for row in doc.get("accounts") or []:
        if not row.company:
            continue

        if row.company in by_company:
            frappe.throw(
                _(
                    "{0} {1} contains duplicate Party Account rows for Company {2}."
                ).format(
                    doc.doctype,
                    frappe.bold(doc.name or represented_company),
                    frappe.bold(row.company),
                )
            )

        by_company[row.company] = row

    if represented_company in by_company:
        frappe.throw(
            _(
                "{0} {1} must not contain a Party Account for its own represented company {2}."
            ).format(
                doc.doctype,
                frappe.bold(doc.name or represented_company),
                frappe.bold(represented_company),
            )
        )

    for company in _other_companies(represented_company):
        account = _account_name(account_code, company)
        _validate_account(account, company, account_type)

        row = by_company.get(company)
        if row:
            row.account = account
        else:
            doc.append(
                "accounts",
                {
                    "company": company,
                    "account": account,
                },
            )


def _ensure_customer(company):
    existing_name = _resolve_existing_party(
        "Customer",
        "customer_name",
        "is_internal_customer",
        company,
    )

    if existing_name:
        doc = frappe.get_doc("Customer", existing_name)
    else:
        doc = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": company,
                "customer_type": "Company",
                "customer_group": CUSTOMER_GROUP,
                "territory": TERRITORY,
                "is_internal_customer": 1,
                "represents_company": company,
                "disabled": 0,
            }
        )

    doc.customer_type = "Company"
    doc.customer_group = CUSTOMER_GROUP
    doc.territory = TERRITORY
    doc.is_internal_customer = 1
    doc.represents_company = company

    _ensure_allowed_companies(doc, company)
    _ensure_party_accounts(
        doc,
        company,
        RECEIVABLE_CODE,
        "Receivable",
    )

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)


def _ensure_supplier(company):
    existing_name = _resolve_existing_party(
        "Supplier",
        "supplier_name",
        "is_internal_supplier",
        company,
    )

    if existing_name:
        doc = frappe.get_doc("Supplier", existing_name)
    else:
        doc = frappe.get_doc(
            {
                "doctype": "Supplier",
                "supplier_name": company,
                "supplier_type": "Company",
                "supplier_group": SUPPLIER_GROUP,
                "is_internal_supplier": 1,
                "represents_company": company,
                "disabled": 0,
            }
        )

    doc.supplier_type = "Company"
    doc.supplier_group = SUPPLIER_GROUP
    doc.is_internal_supplier = 1
    doc.represents_company = company

    _ensure_allowed_companies(doc, company)
    _ensure_party_accounts(
        doc,
        company,
        PAYABLE_CODE,
        "Payable",
    )

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)

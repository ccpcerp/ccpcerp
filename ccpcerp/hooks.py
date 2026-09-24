app_name = "ccpcerp"
app_title = "CCPC ERP"
app_publisher = "CCPC"
app_description = "CCPC Maintenance Management System"
app_email = "gani@ccpc.local"
app_license = "mit"

fixtures = [
	{
		"doctype": "Custom Field",
		"filters": [
			[
				"name",
				"in",
				[
					"Material Request-equipment",
					"Material Request-department",
					"Material Request-work_request",
					"Material Request-mrs",
				],
			]
		],
	}
]

doctype_js = {
	"Work Request": "public/js/equipment_costing.js",
	"Job Order": "public/js/equipment_costing.js",
	"Material Request": "public/js/equipment_costing.js",
	"Stock Entry": "public/js/equipment_costing.js",
	"Purchase Invoice": "public/js/equipment_costing.js",
	"Journal Entry": "public/js/equipment_costing.js",
	"Purchase Receipt": "public/js/equipment_costing.js",
}

doc_events = {
	"Material Request": {
		"before_validate": [
			"ccpcerp.ccpc_erp.equipment_costing.preserve_material_request_work_request",
			"ccpcerp.ccpc_erp.equipment_costing.set_equipment_defaults",
		],
		"validate": "ccpcerp.ccpc_erp.equipment_costing.validate_equipment_costing",
		"before_submit": "ccpcerp.ccpc_erp.equipment_costing.validate_equipment_costing",
		"on_update": "ccpcerp.ccpc_erp.mrs_procurement.sync_material_request_status",
		"on_submit": "ccpcerp.ccpc_erp.mrs_procurement.sync_material_request_status",
		"on_cancel": "ccpcerp.ccpc_erp.mrs_procurement.sync_material_request_status",
	},
	"Stock Entry": {
		"before_validate": "ccpcerp.ccpc_erp.equipment_costing.set_equipment_defaults",
		"validate": [
			"ccpcerp.ccpc_erp.equipment_costing.validate_equipment_costing",
			"ccpcerp.ccpc_erp.withdrawal_slip_stock.validate_withdrawal_slip_stock_entry",
		],
		"before_submit": [
			"ccpcerp.ccpc_erp.equipment_costing.validate_equipment_costing",
			"ccpcerp.ccpc_erp.withdrawal_slip_stock.validate_withdrawal_slip_stock_entry",
		],
		"on_submit": "ccpcerp.ccpc_erp.withdrawal_slip_stock.sync_withdrawal_slip_issue_status",
		"on_cancel": "ccpcerp.ccpc_erp.withdrawal_slip_stock.sync_withdrawal_slip_issue_status",
	},
	"Purchase Invoice": {
		"before_validate": "ccpcerp.ccpc_erp.equipment_costing.set_equipment_defaults",
		"validate": "ccpcerp.ccpc_erp.equipment_costing.validate_equipment_costing",
		"before_submit": "ccpcerp.ccpc_erp.equipment_costing.validate_equipment_costing",
	},
	"Journal Entry": {
		"before_validate": "ccpcerp.ccpc_erp.equipment_costing.set_equipment_defaults",
		"validate": "ccpcerp.ccpc_erp.equipment_costing.validate_equipment_costing",
		"before_submit": "ccpcerp.ccpc_erp.equipment_costing.validate_equipment_costing",
	},
	"Purchase Receipt": {
		"before_validate": "ccpcerp.ccpc_erp.equipment_costing.set_equipment_defaults",
		"validate": "ccpcerp.ccpc_erp.equipment_costing.validate_equipment_costing",
		"before_submit": "ccpcerp.ccpc_erp.equipment_costing.validate_equipment_costing",
	},
}

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "ccpcerp",
# 		"logo": "/assets/ccpcerp/logo.png",
# 		"title": "CCPC ERP",
# 		"route": "/ccpcerp",
# 		"has_permission": "ccpcerp.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/ccpcerp/css/ccpcerp.css"
# app_include_js = "/assets/ccpcerp/js/ccpcerp.js"

# include js, css files in header of web template
# web_include_css = "/assets/ccpcerp/css/ccpcerp.css"
# web_include_js = "/assets/ccpcerp/js/ccpcerp.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "ccpcerp/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "ccpcerp/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "ccpcerp.utils.jinja_methods",
# 	"filters": "ccpcerp.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "ccpcerp.install.before_install"
# after_install = "ccpcerp.install.after_install"

after_migrate = "ccpcerp.setup.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "ccpcerp.uninstall.before_uninstall"
# after_uninstall = "ccpcerp.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "ccpcerp.utils.before_app_install"
# after_app_install = "ccpcerp.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "ccpcerp.utils.before_app_uninstall"
# after_app_uninstall = "ccpcerp.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "ccpcerp.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "ccpcerp.notifications.get_notification_config"

permission_query_conditions = {
	"MRS": "ccpcerp.ccpc_erp.mrs_permissions.get_mrs_permission_query_conditions",
}

has_permission = {
	"MRS": "ccpcerp.ccpc_erp.mrs_permissions.has_mrs_permission",
}

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"ccpcerp.tasks.all"
# 	],
# 	"daily": [
# 		"ccpcerp.tasks.daily"
# 	],
# 	"hourly": [
# 		"ccpcerp.tasks.hourly"
# 	],
# 	"weekly": [
# 		"ccpcerp.tasks.weekly"
# 	],
# 	"monthly": [
# 		"ccpcerp.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "ccpcerp.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "ccpcerp.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "ccpcerp.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "ccpcerp.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["ccpcerp.utils.before_request"]
# after_request = ["ccpcerp.utils.after_request"]

# Job Events
# ----------
# before_job = ["ccpcerp.utils.before_job"]
# after_job = ["ccpcerp.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"ccpcerp.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

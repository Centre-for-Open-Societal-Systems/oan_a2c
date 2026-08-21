app_name = "oan_a2c"
app_title = "OpenAgriNet Access to Credit"
app_publisher = "OpenAgriNet"
app_description = "Access to Credit platform as a DPG for the Open Agro Stack in Ethiopia"
app_email = "admin@openagrinet.org"
app_license = "mit"


# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "oan_a2c",
# 		"logo": "/assets/oan_a2c/logo.png",
# 		"title": "OpenAgriNet Access to Credit",
# 		"route": "/oan_a2c",
# 		"has_permission": "oan_a2c.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/oan_a2c/css/oan_a2c.css"
# app_include_js = "/assets/oan_a2c/js/oan_a2c.js"

# include js, css files in header of web template
# web_include_css = "/assets/oan_a2c/css/oan_a2c.css"
# web_include_js = "/assets/oan_a2c/js/oan_a2c.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "oan_a2c/public/scss/website"

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
# app_include_icons = "oan_a2c/public/icons.svg"

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
# 	"methods": "oan_a2c.utils.jinja_methods",
# 	"filters": "oan_a2c.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "oan_a2c.install.before_install"
# Install app-owned custom fields on standard doctypes. Runs on fresh install
# (patches are skipped there via set_all_patches_as_completed) and is idempotent.
after_install = "oan_a2c.setup.custom_fields.setup_custom_fields"

# Re-assert those custom fields on every migrate so already-provisioned sites
# stay self-healing when the definitions change.
after_migrate = "oan_a2c.setup.migrate.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "oan_a2c.uninstall.before_uninstall"
# after_uninstall = "oan_a2c.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "oan_a2c.utils.before_app_install"
# after_app_install = "oan_a2c.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "oan_a2c.utils.before_app_uninstall"
# after_app_uninstall = "oan_a2c.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "oan_a2c.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "oan_a2c.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

BANK_SCOPED = [
	"A2C Loan Product",
	"A2C Term Relationship",
	"A2C Loan Product Lookup",
	"A2C Loan Product Attribute Lookup",
	"A2C Loan Application",
	"A2C Loan Application Audit Event",
	"A2C Loan Status Stage",
]

permission_query_conditions = {d: "oan_a2c.a2c_marketplace.permissions.bank_scope_query" for d in BANK_SCOPED}
permission_query_conditions["A2C Loan Application"] = (
	"oan_a2c.a2c_marketplace.permissions.loan_application_scope_query"
)
# A2C Loan Product adds a catalog-visibility branch on top of bank scoping: a farmer
# browses across banks but only sees Active products. See permissions.py.
permission_query_conditions["A2C Loan Product"] = (
	"oan_a2c.a2c_marketplace.permissions.loan_product_scope_query"
)
# Neither of these is bank-scoped, so neither had a hook before farmers existed.
permission_query_conditions["A2C Farmer Profile"] = (
	"oan_a2c.a2c_marketplace.permissions.farmer_own_profile_query"
)
permission_query_conditions["A2C Consent Request"] = (
	"oan_a2c.a2c_marketplace.permissions.farmer_own_consent_query"
)
# Bookmarking is open to every catalog-browsing role, so these two hooks are the only
# thing scoping one user's saved list away from another's -- the query hook for lists,
# the has_permission twin below for reads/deletes by name.
permission_query_conditions["A2C Saved Product"] = (
	"oan_a2c.a2c_marketplace.permissions.saved_product_own_query"
)
has_permission = {d: "oan_a2c.a2c_marketplace.permissions.bank_scope_doc" for d in BANK_SCOPED}
has_permission["A2C Saved Product"] = "oan_a2c.a2c_marketplace.permissions.saved_product_own_doc"

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"A2C Loan Product": {
		"after_insert": "oan_a2c.a2c_marketplace.stats_cache.on_product_change",
		"on_update": [
			"oan_a2c.a2c_marketplace.lookups.refresh_product_lookups",
			"oan_a2c.a2c_marketplace.stats_cache.on_product_change",
		],
		"on_trash": [
			"oan_a2c.a2c_marketplace.lookups.delete_product_lookups",
			"oan_a2c.a2c_marketplace.stats_cache.on_product_change",
		],
	},
	"A2C Loan Application": {
		"after_insert": "oan_a2c.a2c_marketplace.stats_cache.on_application_change",
		"on_update": "oan_a2c.a2c_marketplace.stats_cache.on_application_change",
		"on_trash": "oan_a2c.a2c_marketplace.stats_cache.on_application_change",
	},
	"A2C Credit Information": {
		"after_insert": "oan_a2c.openagrinet_access_to_credit.doctype.a2c_credit_information.a2c_credit_information.sync_lead_loan_amount",
		"on_update": "oan_a2c.openagrinet_access_to_credit.doctype.a2c_credit_information.a2c_credit_information.sync_lead_loan_amount",
		"on_trash": "oan_a2c.openagrinet_access_to_credit.doctype.a2c_credit_information.a2c_credit_information.sync_lead_loan_amount",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"hourly": [
		"oan_a2c.a2c_marketplace.stats_cache.reconcile_all_banks",
	],
}

# Testing
# -------

before_tests = "oan_a2c.tests.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "oan_a2c.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "oan_a2c.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "oan_a2c.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["oan_a2c.utils.before_request"]
# after_request = ["oan_a2c.utils.after_request"]

# Job Events
# ----------
# before_job = ["oan_a2c.utils.before_job"]
# after_job = ["oan_a2c.utils.after_job"]

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

auth_hooks = ["oan_a2c.api.middleware.validate_jwt_request"]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

fixtures = [
	{
		"dt": "Role",
		"filters": [
			[
				"name",
				"in",
				[
					"A2C Administrator",
					"A2C Bank Admin",
					"A2C Bank Agent",
					"A2C Development Agent",
					"A2C Farmer",
				],
			]
		],
	}
]

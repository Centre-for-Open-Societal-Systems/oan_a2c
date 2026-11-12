import frappe
from frappe.installer import update_site_config

from oan_a2c.a2c_marketplace.roles import BANK_ADMIN_ROLE, FARMER_ROLE
from oan_a2c.setup.administrative_areas import seed_administrative_areas
from oan_a2c.setup.custom_fields import setup_custom_fields
from oan_a2c.setup.roles import setup_roles


def setup_self_registerable_roles():
	"""Ensure jwt_self_registerable_roles in site_config.json includes A2C Bank Admin and A2C Farmer."""
	current_roles = list(frappe.conf.get("jwt_self_registerable_roles") or [])
	required_roles = [BANK_ADMIN_ROLE, FARMER_ROLE]
	updated = False
	for role in required_roles:
		if role not in current_roles:
			current_roles.append(role)
			updated = True
	if updated:
		update_site_config("jwt_self_registerable_roles", current_roles)


def setup_desk_home_page():
	"""Open the desk on the workspace instead of the setup wizard.

	`bench new-site` stores `setup-wizard` as the desk home page, and only the wizard's last step
	resets it. A site created by a job never runs the wizard, and `bench migrate` marks Frappe's
	setup complete as soon as a non-admin user exists (the seeded demo users). The desk then boots
	into the wizard, which sends it back to /desk, and the page reloads in a loop. A home page an
	admin chose is left alone.
	"""
	if frappe.db.get_default("desktop:home_page") == "setup-wizard":
		frappe.db.set_default("desktop:home_page", "workspace")


def after_install():
	setup_roles()
	setup_custom_fields()
	setup_self_registerable_roles()
	setup_desk_home_page()
	seed_administrative_areas()


def after_migrate():
	setup_roles()
	setup_custom_fields()
	setup_self_registerable_roles()
	setup_desk_home_page()
	seed_administrative_areas()

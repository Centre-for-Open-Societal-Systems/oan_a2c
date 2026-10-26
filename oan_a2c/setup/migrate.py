import frappe
from frappe.installer import update_site_config

from oan_a2c.a2c_marketplace.roles import BANK_ADMIN_ROLE, FARMER_ROLE
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


def after_install():
	setup_roles()
	setup_custom_fields()
	setup_self_registerable_roles()


def after_migrate():
	setup_roles()
	setup_custom_fields()
	setup_self_registerable_roles()

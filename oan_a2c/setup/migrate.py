from oan_a2c.setup.custom_fields import setup_custom_fields
from oan_a2c.setup.roles import setup_roles


def after_install():
	setup_roles()
	setup_custom_fields()


def after_migrate():
	setup_roles()
	setup_custom_fields()

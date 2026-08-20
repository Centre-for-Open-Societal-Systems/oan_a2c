from oan_a2c.setup.custom_fields import setup_custom_fields
from oan_a2c.setup.workflow_properties import setup_workflow_property_setters


def after_migrate():
	setup_custom_fields()
	setup_workflow_property_setters()

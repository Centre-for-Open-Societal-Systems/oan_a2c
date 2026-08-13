import frappe

from oan_a2c.scripts.seed_locations import seed_all


def execute():
	# We just call the script logic.
	# We don't fail if the files are not found, we just print a message (handled by the script)
	seed_all()

import frappe


def execute():
	"""Copy `User.a2c_must_change_password` into oan_auth_service's `oan_must_change_password`.

	Login, refresh and the request middleware now read the oan_auth_service flag, so
	an agent holding an admin-issued temporary password must carry it over or they
	would be let straight in with it.

	Idempotent. Skips if either column is missing: the old one on a fresh install,
	the new one when oan_auth_service is not installed on this site yet (install it
	first, then migrate).
	"""
	if not (
		frappe.db.has_column("User", "a2c_must_change_password")
		and frappe.db.has_column("User", "oan_must_change_password")
	):
		return

	frappe.db.sql(
		"""
		UPDATE `tabUser`
		SET oan_must_change_password = 1
		WHERE a2c_must_change_password = 1
		"""
	)

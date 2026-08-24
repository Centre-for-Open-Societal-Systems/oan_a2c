import frappe


def execute():
	"""Re-key A2C Saved Product from A2C Farmer Profile to User.

	Bookmarking a product is a browsing convenience, not something that should
	require a farmer profile -- a profile only exists once consent has created
	one, so keying on it meant nobody could save a product until after they had
	already applied for one.

	Rows whose profile has no bound user cannot be migrated and are dropped: a
	bookmark that belongs to nobody is unreachable by any API and would block the
	unique index below.

	Idempotent: guarded on the presence of the old column, and the index swap
	tolerates either index already being in its target state.
	"""
	if not frappe.db.table_exists("A2C Saved Product"):
		return

	if not frappe.db.has_column("A2C Saved Product", "user"):
		return

	if frappe.db.has_column("A2C Saved Product", "farmer_profile"):
		frappe.db.sql(
			"""
			UPDATE `tabA2C Saved Product` sp
			INNER JOIN `tabA2C Farmer Profile` fp ON fp.name = sp.farmer_profile
			SET sp.`user` = fp.`user`
			WHERE ifnull(sp.`user`, '') = ''
			  AND ifnull(fp.`user`, '') != ''
			"""
		)
		# Unmigratable rows: no bound user, or a duplicate now that two profiles
		# belonging to one user collapse onto the same (user, loan_product).
		frappe.db.sql("DELETE FROM `tabA2C Saved Product` WHERE ifnull(`user`, '') = ''")
		# `name` tiebreaks the creation comparison. Two bookmarks of the same product
		# can share a creation timestamp (same second, or copied by an earlier
		# migration), and a strict `keep.creation < sp.creation` matches neither
		# direction for such a pair -- so both rows survive and the unique index added
		# below fails, aborting the migration. Comparing (creation, name) is a total
		# order, so exactly one row of every duplicate group is kept.
		frappe.db.sql(
			"""
			DELETE sp FROM `tabA2C Saved Product` sp
			INNER JOIN `tabA2C Saved Product` keep
				ON keep.`user` = sp.`user`
				AND keep.`loan_product` = sp.`loan_product`
				AND (keep.creation, keep.name) < (sp.creation, sp.name)
			"""
		)

	_drop_index("idx_unique_saved_product")
	_add_unique_index("idx_unique_saved_product_user", ["user", "loan_product"])


def _drop_index(name: str) -> None:
	exists = frappe.db.sql(
		"""SELECT 1 FROM information_schema.statistics
		WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s LIMIT 1""",
		("tabA2C Saved Product", name),
	)
	if exists:
		frappe.db.sql_ddl(f"ALTER TABLE `tabA2C Saved Product` DROP INDEX `{name}`")


def _add_unique_index(name: str, columns: list[str]) -> None:
	exists = frappe.db.sql(
		"""SELECT 1 FROM information_schema.statistics
		WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s LIMIT 1""",
		("tabA2C Saved Product", name),
	)
	if exists:
		return
	cols = ", ".join(f"`{c}`" for c in columns)
	frappe.db.sql_ddl(f"ALTER TABLE `tabA2C Saved Product` ADD UNIQUE INDEX `{name}` ({cols})")

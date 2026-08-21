import frappe


def before_tests():
	# Make frappe.db.commit a no-op during test runs to prevent data removal/mutation
	frappe.db.commit = lambda: None


def end_impersonation():
	"""Return to Administrator and drop the new-doc templates stamped while away.

	Any test that runs as a fixture user must call this before that user is
	deleted or rolled back, or it leaves a landmine for whatever runs next.

	Frappe caches one "new doc" template per doctype in `frappe.local`, built on
	first use and stamped with `owner = frappe.session.user` (see
	`frappe.model.create_new.make_new_doc`). The cache lives for the whole test
	process, so a template first built while impersonating `farmer-x@...` hands
	that owner to every later `frappe.new_doc()` of the same doctype. Once the
	fixture user is gone, the next insert of that doctype dies on link validation
	pointing at a user from a test class that finished long ago.

	That is exactly how inserting a User in one module started failing with
	"Could not find User: farmer-none-<hash>@example.com" from another module's
	fixtures -- User.after_insert creates a Notification Settings doc, which came
	out of the poisoned template. It only ever reproduced in a full-suite run,
	which is what made it look like a flake.
	"""
	frappe.set_user("Administrator")
	frappe.local.new_doc_templates.clear()

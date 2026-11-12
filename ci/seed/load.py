"""Load demo data exported from a Frappe site into a site that already has its apps.

Run from the bench's sites/ directory with the bench's python:

    load.py <site> <seed directory>

Only creates what is missing, so it is safe on every deploy: records edited or added
after the first run stay as they are, and a deleted demo record comes back. Emails stay
muted and document controllers do not run, so loading sends nothing.
"""

import json
import os
import shutil
import sys

import frappe
from frappe.utils.password import update_password

# These change the schema or cached metadata when saved, so they go through their controllers.
CONTROLLER_DOCTYPES = {
	"Role",
	"Workflow State",
	"Workflow Action Master",
	"Workflow",
	"Custom Field",
	"Property Setter",
	"Notification",
}
# PostgreSQL rejects '' in these columns; MariaDB exports contain it.
TYPED_FIELDS = {
	"Date",
	"Datetime",
	"Time",
	"Int",
	"Long Int",
	"Float",
	"Currency",
	"Percent",
	"Check",
	"Duration",
}


def normalize(row, doctype):
	row["doctype"] = doctype
	for field in frappe.get_meta(doctype).fields:
		value = row.get(field.fieldname)
		if field.fieldtype in TYPED_FIELDS and value == "":
			row[field.fieldname] = None
		elif field.fieldtype in ("Table", "Table MultiSelect") and value:
			for child in value:
				normalize(child, field.options)


def insert(row, doctype):
	normalize(row, doctype)
	doc = frappe.get_doc(row)
	if doctype in CONTROLLER_DOCTYPES:
		doc.insert(ignore_permissions=True, ignore_links=True, ignore_mandatory=True, set_name=row["name"])
		return
	doc.db_insert()
	for child in doc.get_all_children():
		child.db_insert()


def load_doctype(doctype, rows, password):
	created = failed = 0
	for row in rows:
		name = row["name"]
		if frappe.db.exists(doctype, name):
			continue
		frappe.db.savepoint("seed_row")
		try:
			insert(row, doctype)
			if doctype == "User" and password:
				update_password(name, password, logout_all_sessions=False)
			created += 1
		except Exception as e:
			frappe.db.rollback(save_point="seed_row")
			failed += 1
			print(f"FAILED {doctype} {name}: {e}")
	frappe.db.commit()  # nosemgrep
	print(f"{doctype}: created {created} of {len(rows)}, failed {failed}")
	return failed


def load_single(doctype, values):
	current = frappe.db.get_singles_dict(doctype)
	for key, value in values.items():
		if key in current or key.startswith("_") or not frappe.get_meta(doctype).has_field(key):
			continue
		frappe.db.set_single_value(doctype, key, value, update_modified=False)
	frappe.db.commit()  # nosemgrep
	print(f"{doctype}: single loaded")


def copy_files(seed):
	for kind in ("public", "private"):
		source = os.path.join(seed, "files", kind)
		target = frappe.get_site_path(kind, "files")
		for root, _dirs, names in os.walk(source):
			for name in names:
				path = os.path.join(target, os.path.relpath(os.path.join(root, name), source))
				if not os.path.exists(path):
					os.makedirs(os.path.dirname(path), exist_ok=True)
					shutil.copyfile(os.path.join(root, name), path)


def main(site, seed):
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")  # nosemgrep
	frappe.flags.in_import = True
	frappe.flags.mute_emails = True
	password = os.environ.get("SEED_USER_PASSWORD")
	with open(os.path.join(seed, "manifest.json")) as f:  # nosemgrep
		manifest = json.load(f)
	copy_files(seed)
	failed = 0
	for doctype in manifest["order"]:
		if not frappe.db.exists("DocType", doctype):
			print(f"{doctype}: not on this site, skipped")
			continue
		with open(os.path.join(seed, "data", manifest["files"][doctype])) as f:  # nosemgrep
			rows = json.load(f)
		if doctype in manifest["singles"]:
			load_single(doctype, rows[0])
		else:
			failed += load_doctype(doctype, rows, password)
	frappe.clear_cache()
	frappe.destroy()
	if failed:
		sys.exit(f"{failed} records failed")


if __name__ == "__main__":
	main(sys.argv[1], sys.argv[2])

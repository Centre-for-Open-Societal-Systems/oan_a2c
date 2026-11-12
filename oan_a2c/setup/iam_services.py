import json
import os

import frappe

IAM_SERVICE_DOCTYPE = "OAN IAM Service"
ENV_VAR = "OAN_IAM_SERVICES"


def sync_iam_services():
	"""Create or update the `OAN IAM Service` rows given as a JSON list in $OAN_IAM_SERVICES.

	Run with `bench execute` by the iam-services Job of the oan-a2c-service chart, which passes the
	chart's `iamServices`. Each entry is matched by `key` and uses the DocType's field names; the fields
	it names are set and the others are left as they are. `default_roles` is a list of role names.
	Rows that are not listed are kept.
	"""
	entries = json.loads(os.environ.get(ENV_VAR) or "[]")
	meta = frappe.get_meta(IAM_SERVICE_DOCTYPE)
	for entry in entries:
		key = entry.get("key")
		if not key:
			frappe.throw(f"Every entry of {ENV_VAR} needs a `key`.")
		doc = (
			frappe.get_doc(IAM_SERVICE_DOCTYPE, key)
			if frappe.db.exists(IAM_SERVICE_DOCTYPE, key)
			else frappe.new_doc(IAM_SERVICE_DOCTYPE)
		)
		for fieldname, value in entry.items():
			if fieldname == "default_roles":
				doc.set("default_roles", [{"role": role} for role in value or []])
			elif meta.has_field(fieldname):
				doc.set(fieldname, value)
			else:
				frappe.throw(f"{ENV_VAR}[{key}]: {IAM_SERVICE_DOCTYPE} has no field `{fieldname}`.")
		doc.save(ignore_permissions=True)
	print(f"{IAM_SERVICE_DOCTYPE}: {len(entries)} row(s) written")

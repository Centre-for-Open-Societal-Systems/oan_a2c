"""Settings the oan-a2c-service chart writes into the DB on every install and upgrade.

The chart's site-settings Job runs `bench execute oan_a2c.setup.site_settings.sync` with the values
in environment variables: lists and maps from the chart values as JSON, credentials from a Kubernetes
Secret. Nothing is written to site_config.json.
"""

import json
import os

import frappe

IAM_SERVICE_DOCTYPE = "OAN IAM Service"
OPENG2P_SETTINGS_DOCTYPE = "A2C OpenG2P Settings"
IAM_SERVICES_ENV = "OAN_IAM_SERVICES"
OPENG2P_SETTINGS_ENV = "OAN_OPENG2P_SETTINGS"
# Partner credentials, read from the Secret the chart's openg2pPartnerSecret names.
OPENG2P_PARTNER_ENV = {
	"partner_client_id": "OAN_OPENG2P_PARTNER_CLIENT_ID",
	"partner_client_secret": "OAN_OPENG2P_PARTNER_CLIENT_SECRET",
	"partner_username": "OAN_OPENG2P_PARTNER_USERNAME",
	"partner_password": "OAN_OPENG2P_PARTNER_PASSWORD",
}


def sync():
	sync_iam_services()
	sync_openg2p_settings()


def sync_iam_services():
	"""Create or update the `OAN IAM Service` rows given as a JSON list in $OAN_IAM_SERVICES.

	Each entry is matched by `key` and uses the DocType's field names; the fields it names are set and
	the others are left as they are. `default_roles` is a list of role names. Rows that are not listed
	are kept.
	"""
	entries = json.loads(os.environ.get(IAM_SERVICES_ENV) or "[]")
	meta = frappe.get_meta(IAM_SERVICE_DOCTYPE)
	for entry in entries:
		key = entry.get("key")
		if not key:
			frappe.throw(f"Every entry of {IAM_SERVICES_ENV} needs a `key`.")
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
				frappe.throw(f"{IAM_SERVICES_ENV}[{key}]: {IAM_SERVICE_DOCTYPE} has no field `{fieldname}`.")
		doc.save(ignore_permissions=True)
	print(f"{IAM_SERVICE_DOCTYPE}: {len(entries)} row(s) written")


def sync_openg2p_settings():
	"""Set the `A2C OpenG2P Settings` fields given in $OAN_OPENG2P_SETTINGS (a JSON map) and the partner
	credentials in $OAN_OPENG2P_PARTNER_*.

	Empty values are skipped, so a field left out or a credential missing from the Secret keeps what
	the DB has. Password fields are stored encrypted with the site's encryption_key.
	"""
	values = {
		fieldname: value
		for fieldname, value in json.loads(os.environ.get(OPENG2P_SETTINGS_ENV) or "{}").items()
		if value not in (None, "")
	}
	for fieldname, env in OPENG2P_PARTNER_ENV.items():
		if os.environ.get(env):
			values[fieldname] = os.environ[env]
	if not values:
		return
	meta = frappe.get_meta(OPENG2P_SETTINGS_DOCTYPE)
	unknown = sorted(fieldname for fieldname in values if not meta.has_field(fieldname))
	if unknown:
		frappe.throw(f"{OPENG2P_SETTINGS_DOCTYPE} has no field {', '.join(unknown)}.")
	doc = frappe.get_single(OPENG2P_SETTINGS_DOCTYPE)
	doc.update(values)
	doc.save(ignore_permissions=True)
	print(f"{OPENG2P_SETTINGS_DOCTYPE}: {', '.join(sorted(values))} written")

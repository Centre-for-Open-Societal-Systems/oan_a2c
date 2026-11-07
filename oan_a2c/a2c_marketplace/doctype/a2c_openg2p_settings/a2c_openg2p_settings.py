# Copyright (c) 2026, OAN and contributors
# For license information, please see license.txt

from frappe.model.document import Document

URL_FIELDS = ("keycloak_url", "consent_manager_url", "aggregation_url", "agent_api_url")


class A2COpenG2PSettings(Document):
	def validate(self):
		# Endpoints are joined with absolute paths, so a trailing slash would
		# produce `//consent/v1/...`.
		for field in URL_FIELDS:
			value = (self.get(field) or "").strip()
			self.set(field, value.rstrip("/"))

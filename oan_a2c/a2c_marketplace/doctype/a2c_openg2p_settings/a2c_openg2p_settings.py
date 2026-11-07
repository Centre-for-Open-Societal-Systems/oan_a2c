# Copyright (c) 2026, OAN and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class A2COpenG2PSettings(Document):
	def validate(self):
		# Endpoints are joined with absolute paths, so a trailing slash would
		# produce `//consent/v1/...`. Frappe checks the URL format (options: URL)
		# but does not trim, so strip it here for every URL field on the doctype.
		for df in self.meta.get("fields", {"fieldtype": "Data", "options": "URL"}):
			self.set(df.fieldname, (self.get(df.fieldname) or "").rstrip("/"))

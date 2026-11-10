# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

"""A2C Administrative Area: the Region > Zone > Woreda > Kebele tree.

Reference data behind the location dropdowns. The tree is seeded from
`setup/data/ethiopia_administrative_areas.json.gz`; queries over it live in
`a2c_marketplace/administrative_area.py`.
"""

import frappe
from frappe.utils.nestedset import NestedSet


class A2CAdministrativeArea(NestedSet):
	nsm_parent_field = "parent_administrative_area"

	def autoname(self):
		if getattr(self, "name", None):
			return
		self.set_tree_metadata()
		if self.path_code:
			self.name = self.path_code
		elif self.code and self.parent_administrative_area:
			self.name = f"{self.parent_administrative_area}.{self.code}"
		else:
			self.name = self.area_name

	def validate(self):
		self.set_tree_metadata()

	def set_tree_metadata(self):
		"""Compute depth, country, and materialized path_code."""
		if not self.parent_administrative_area:
			self.depth = 0
			if self.area_name == "World":
				self.is_group = 1
				self.path_code = self.code or "WORLD"
			else:
				self.path_code = self.code or self.area_name
			return

		parent = frappe.get_doc("A2C Administrative Area", self.parent_administrative_area)
		self.depth = (parent.depth or 0) + 1

		if not self.country:
			if parent.level_name == "Country" or parent.depth == 1:
				self.country = parent.area_name
			elif parent.country:
				self.country = parent.country

		segment = (self.code or self.area_name).replace(" ", "_").upper()
		if parent.path_code and parent.path_code != "WORLD":
			self.path_code = f"{parent.path_code}.{segment}"
		else:
			self.path_code = segment


def on_doctype_update():
	frappe.db.add_index("A2C Administrative Area", ["lft"])
	frappe.db.add_index("A2C Administrative Area", ["rgt"])
	frappe.db.add_index("A2C Administrative Area", ["path_code"])
	frappe.db.add_index("A2C Administrative Area", ["depth"])

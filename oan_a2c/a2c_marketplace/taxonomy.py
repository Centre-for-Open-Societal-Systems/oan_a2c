# Copyright (c) 2026, OAN and contributors
# For license information, please see license.txt

import frappe


def get_category_children(parent_category: str) -> list[str]:
	"""Get all descendant category term IDs for a given parent"""
	if not parent_category:
		return []

	children = frappe.get_all("A2C Term Category", filters={"parent_category": parent_category}, pluck="name")

	all_descendants = list(children)
	for child in children:
		all_descendants.extend(get_category_children(child))

	return all_descendants

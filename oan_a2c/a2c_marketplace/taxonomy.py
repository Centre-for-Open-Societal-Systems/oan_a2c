# Copyright (c) 2026, OAN and contributors
# For license information, please see license.txt

import re

import frappe


def clean_slug(text: str) -> str:
	"""Generate a URL-safe lowercase slug consisting only of [a-z0-9-].

	Everything outside [A-Za-z0-9] is dropped: parentheses, commas, quotes,
	comparison operators (<, >), slashes, and non-ASCII letters. Runs of
	whitespace, underscores and hyphens collapse to a single hyphen.

	Dropping commas is the point — a comma inside a term id makes a
	comma-separated filter like `?category=a,b` ambiguous to split.

	Non-Latin scripts are stripped rather than transliterated, so a name written
	only in e.g. Amharic slugs to "". Callers must supply their own fallback;
	`get_unique_term_id` does.
	"""
	if not text:
		return ""
	cleaned = re.sub(r"[^A-Za-z0-9\s_-]", "", str(text))
	return re.sub(r"[\s_-]+", "-", cleaned).strip("-").lower()


def get_unique_term_id(term_name: str, current_term_id: str | None = None) -> str:
	"""Generate a unique term_id for A2C Term with numeric increment on collision."""
	base_slug = clean_slug(term_name)
	if not base_slug:
		base_slug = "term"

	slug = base_slug
	counter = 1
	while True:
		existing = frappe.db.get_value("A2C Term", slug, ["name", "term_name"], as_dict=True)
		if not existing or existing.name == current_term_id:
			return slug
		if existing.term_name and existing.term_name.strip().lower() == str(term_name).strip().lower():
			return slug
		counter += 1
		slug = f"{base_slug}-{counter}"


def get_category_children(parent_category: str) -> list[str]:
	"""Get all descendant category term IDs for a given parent"""
	if not parent_category:
		return []

	children = frappe.get_all("A2C Term Category", filters={"parent_category": parent_category}, pluck="name")

	all_descendants = list(children)
	for child in children:
		all_descendants.extend(get_category_children(child))

	return all_descendants

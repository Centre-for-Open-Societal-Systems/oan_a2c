"""Queries over the A2C Administrative Area tree, for the location dropdowns."""

import frappe
from pypika import Criterion

DOCTYPE = "A2C Administrative Area"
MAX_LIMIT = 500


def get_ancestors(area_id_or_path: str) -> dict:
	"""The chain from the country down to the given area, matched by name or path_code."""
	name = area_id_or_path if frappe.db.exists(DOCTYPE, area_id_or_path) else None
	name = name or frappe.db.get_value(DOCTYPE, {"path_code": area_id_or_path}, "name")
	if not name:
		return {"breadcrumbs": [], "current": None}

	node = frappe.db.get_value(
		DOCTYPE, name, ["name", "area_name", "path_code", "level_name", "lft", "rgt"], as_dict=True
	)
	breadcrumbs = frappe.get_all(
		DOCTYPE,
		filters=[["lft", "<=", node.lft], ["rgt", ">=", node.rgt], ["depth", ">", 0]],
		fields=["name as area_id", "area_name", "code", "path_code", "level_name", "depth"],
		order_by="lft asc",
		ignore_permissions=True,
	)
	return {
		"current": {
			"area_id": node.name,
			"area_name": node.area_name,
			"path_code": node.path_code,
			"level_name": node.level_name,
		},
		"breadcrumbs": breadcrumbs,
	}


def search_areas(parents=None, level_name=None, search=None, limit=100) -> list[dict]:
	"""Active areas filtered by parent, tier and name.

	With no parent, level or search it returns the top-level regions. A parent that is
	not of the requested tier yields the descendants of that tier (every woreda of a
	region); a parent one tier up yields its direct children.
	"""
	limit = min(int(limit or 100), MAX_LIMIT)
	parents = [p for p in (parents if isinstance(parents, list) else [parents]) if p]

	subtree_parents = []
	direct_parents = []
	if parents:
		parent_docs = frappe.get_all(
			DOCTYPE,
			or_filters=[
				{"name": ["in", parents]},
				{"path_code": ["in", parents]},
				{"code": ["in", parents]},
				{"area_name": ["in", parents]},
			],
			fields=["name", "path_code", "code", "area_name", "level_name", "lft", "rgt"],
			order_by="lft asc",
			ignore_permissions=True,
		)
		# Display names recur across the tree, so a value resolves by name, then path_code,
		# then code, and only last by area_name; within a field the first area in tree order wins.
		by_field = {field: {} for field in ("name", "path_code", "code", "area_name")}
		for doc in parent_docs:
			for field, index in by_field.items():
				key = doc.get(field)
				if key and key in parents:
					index.setdefault(key, doc)

		for p in parents:
			doc = next((by_field[field][p] for field in by_field if p in by_field[field]), None)
			if not doc:
				direct_parents.append(p)
			elif level_name and doc.level_name != level_name:
				subtree_parents.append(doc)
			else:
				direct_parents.append(doc.name)

	Area = frappe.qb.DocType(DOCTYPE)
	query = (
		frappe.qb.from_(Area)
		.select(
			Area.name.as_("area_id"),
			Area.area_name,
			Area.code,
			Area.path_code,
			Area.level_name,
			Area.parent_administrative_area,
			Area.is_group,
			Area.depth,
		)
		.where(Area.is_active == 1)
		.orderby(Area.area_name)
		.limit(limit)
	)

	if parents:
		conditions = []
		if direct_parents:
			conditions.append(Area.parent_administrative_area.isin(direct_parents))
		for sp in subtree_parents:
			conditions.append((Area.lft > sp.lft) & (Area.rgt < sp.rgt))
		query = query.where(Criterion.any(conditions))
	elif not search and not level_name:
		query = query.where(Area.level_name == "Region")

	if level_name:
		query = query.where(Area.level_name == level_name)
	if search:
		query = query.where(Area.area_name.like(f"%{search.strip()}%"))

	return query.run(as_dict=True)

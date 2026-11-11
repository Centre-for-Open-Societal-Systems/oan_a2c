"""Administrative area lookups for the location dropdowns (bank registration, visit scheduling)."""

from oan_a2c.a2c_marketplace.administrative_area import get_ancestors, search_areas
from oan_a2c.api.utils import handle_api_errors, parse_multi_value, success_response


@handle_api_errors
def get_areas(
	parent: str | list[str] | None = None,
	level_name: str | None = None,
	search: str | None = None,
	ancestors_of: str | None = None,
	limit: int = 100,
	**kwargs,
):
	"""Areas for a cascading Region > Zone > Woreda > Kebele dropdown.

	- No parent: the top-level regions.
	- `parent` (name, path_code, or a comma-separated list): its children, or its descendants
	  of `level_name` when that is given.
	- `search`: matches the area name.
	- `ancestors_of`: the breadcrumb chain down to that area instead.
	"""
	if ancestors_of:
		return success_response(data=get_ancestors(ancestors_of))

	parents = parse_multi_value(parent or kwargs.get("parents"))
	level_name = str(level_name).strip() if level_name else None
	areas = search_areas(parents=parents, level_name=level_name, search=search, limit=limit)
	return success_response(
		data={
			"areas": areas,
			"count": len(areas),
			"parent": parents[0] if len(parents) == 1 else (parents or None),
			"level_name": level_name,
		}
	)


@handle_api_errors
def get_area_ancestors(area_id_or_path: str):
	return success_response(data=get_ancestors(area_id_or_path))

"""Read an Aggregation Layer beneficiary360 record into A2C Farmer Profile fields.

The AL returns (oan-aggregation-api bene360.py, deploy/registries.yaml):

	{"beneficiary": {"foundationalId", "matchedRegistryCount"},
	 "registries": [{"registryCode", "registers": [
	     {"registerMnemonic", "attributes": {<scope>: {...}},
	      "tables": [{"tableMnemonic", "attributes": {...}, "tables": [...]}]}]}],
	 "meta": {"warnings": [{"system", "code", "message"}], ...}}

`extract_profile_fields` is the one place that says which registry value feeds which
profile column. To surface another field, add a line to ATTRIBUTE_FIELDS (a value under
the register's attributes) or TABLE_FIELDS (a value in a table row). The AL must expose
the field first (its scope's `fields` in registries.yaml) and the A2C Farmer Profile
needs the column.
"""

from frappe.utils import cint

ACRE_TO_HECTARE = 0.40468564224

FARMER_REGISTRY = "FARMER_REGISTRY"
PERSONAL = "farmer_personal_details"


def _capitalize(value):
	return str(value).capitalize()


def _first(value):
	"""The registry sends a phone as a list (`["+2519..."]`); the profile keeps one number."""
	if isinstance(value, list):
		return next((v for v in value if v not in (None, "")), None)
	return value


# profile field -> ("<scope>.<dotted path>", transform), read under the register's `attributes`.
ATTRIBUTE_FIELDS = {
	"gender": (f"{PERSONAL}.demographic_info.sex", _capitalize),
	"date_of_birth": (f"{PERSONAL}.demographic_info.birth_date", None),
	"marital_status": (f"{PERSONAL}.marital_status", _capitalize),
	"education_level": (f"{PERSONAL}.education_level", None),
}

# profile field -> (TABLE mnemonic, dotted path in the row, transform). The first row, at
# any depth, that has a value wins.
TABLE_FIELDS = {
	"size_of_family": ("HOUSEHOLD", "group_size", cint),
	"land_ownership_status": ("FARM", "land_tenure", None),
	"kebele": ("FARM", "place.name", None),
}


def _dig(obj, path):
	"""`obj["a"]["b"]` for "a.b"; None when any step is missing or not a dict."""
	for key in path.split("."):
		if not isinstance(obj, dict):
			return None
		obj = obj.get(key)
	return obj


def _tables(register, mnemonic):
	"""Every table row with this mnemonic, at any depth under the register."""
	stack = list(register.get("tables") or [])
	while stack:
		table = stack.pop(0)
		if table.get("tableMnemonic") == mnemonic:
			yield table.get("attributes") or {}
		stack.extend(table.get("tables") or [])


def _farmer_register(record):
	return next(
		(
			reg
			for registry in (record or {}).get("registries") or []
			if registry.get("registryCode") == FARMER_REGISTRY
			for reg in registry.get("registers") or []
		),
		None,
	)


def _farmland_hectares(farms):
	hectares = 0.0
	for farm in farms:
		try:
			size = float(farm.get("land_size") or 0)
		except (TypeError, ValueError):
			continue
		is_acre = str(farm.get("measurement") or "").lower().startswith("acre")
		hectares += size * ACRE_TO_HECTARE if is_acre else size
	return round(hectares, 4)


def extract_profile_fields(record) -> dict:
	"""A2C Farmer Profile columns from a beneficiary360 record; empty values are left out.

	Empty when the farmer is in no FARMER_REGISTRY register (the AL answers 200 without a
	warning then)."""
	register = _farmer_register(record)
	if not register:
		return {}

	attributes = register.get("attributes") or {}
	fields = {}

	# More than a path lookup: the name is split into parts, the Fayda ID is the
	# beneficiary's foundational ID, and the farmland size is summed over the farms.
	name = _dig(attributes, f"{PERSONAL}.demographic_info.name") or {}
	fields["first_name"] = name.get("given_name")
	fields["last_name"] = " ".join(p for p in (name.get("second_name"), name.get("surname")) if p)
	fields["phone_number"] = _first(_dig(attributes, f"{PERSONAL}.demographic_info.phone_number"))

	fayda_id = (record.get("beneficiary") or {}).get("foundationalId")
	if fayda_id:
		fields["id_type"] = "uid"
		fields["id_number"] = str(fayda_id)

	for field, (path, transform) in ATTRIBUTE_FIELDS.items():
		value = _dig(attributes, path)
		fields[field] = transform(value) if transform and value not in (None, "") else value

	for field, (mnemonic, path, transform) in TABLE_FIELDS.items():
		value = next(
			(v for row in _tables(register, mnemonic) if (v := _dig(row, path)) not in (None, "")), None
		)
		fields[field] = transform(value) if transform and value is not None else value

	farms = list(_tables(register, "FARM"))
	if farms:
		fields["farmland_size_hectares"] = _farmland_hectares(farms)

	return {k: v for k, v in fields.items() if v not in (None, "")}

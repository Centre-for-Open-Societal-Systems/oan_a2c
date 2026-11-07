"""Reshape an Aggregation Layer beneficiary360 record into consent `selected_data`.

The AL returns (oan-aggregation-api bene360.py, deploy/registries.yaml):

	{"beneficiary": {"foundationalId", "matchedRegistryCount"},
	 "registries": [{"registryCode", "registers": [
	     {"registerMnemonic", "attributes": {<scope>: {...}},
	      "tables": [{"tableMnemonic", "attributes": {...}, "tables": [...]}]}]}],
	 "meta": {"warnings": [{"system", "code", "message"}], ...}}

`process_consent_data` (webhook_consent_data.py) reads the first dict in
`selected_data` through `build_field_getter`, keyed by the labels the legacy
OpenG2P payload used ("Full Name", "Mobile Number", ...). This builds that dict
from the FARMER_REGISTRY record and keeps the full registry tree alongside it,
so nothing the AL returned is lost.

To surface another registry field, add a line to FIELD_MAP (or TABLE_FIELD_MAP for
a table row); no new code. The AL must expose the field first (its scope's
`fields` in registries.yaml) and `process_consent_data` must read the label.
"""

ACRE_TO_HECTARE = 0.40468564224

FARMER_REGISTRY = "FARMER_REGISTRY"

# label -> dotted path under the register's `attributes`, "<scope>.<field path>".
FIELD_MAP = {
	"Gender": "farmer_personal_details.demographic_info.sex",
	"Date of Birth": "farmer_personal_details.demographic_info.birth_date",
	"Marital Status": "farmer_personal_details.marital_status",
	"Education Level": "farmer_personal_details.education_level",
}

# label -> (TABLE mnemonic, dotted path in the row). The first row at any depth wins.
TABLE_FIELD_MAP = {
	"Size of Family": ("HOUSEHOLD", "group_size"),
	"Land Ownership Status": ("FARM", "land_tenure"),
	"Kebele": ("FARM", "place.name"),
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
			for registry in record.get("registries") or []
			if registry.get("registryCode") == FARMER_REGISTRY
			for reg in registry.get("registers") or []
		),
		None,
	)


def _full_name(register):
	name = _dig(register.get("attributes") or {}, "farmer_personal_details.demographic_info.name") or {}
	return " ".join(p for p in (name.get("given_name"), name.get("second_name"), name.get("surname")) if p)


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


def to_selected_data(record) -> dict:
	"""`{"farmer": {Label: value}, "registries": [...]}`; `farmer` is empty when the
	farmer is in no FARMER_REGISTRY register (the AL answers 200 without a warning then)."""
	registries = record.get("registries") or []
	register = _farmer_register(record)
	if not register:
		return {"farmer": {}, "registries": registries}

	attributes = register.get("attributes") or {}
	phone = _dig(attributes, "farmer_personal_details.demographic_info.phone_number")

	# Fields that need more than a path lookup.
	farmer = {
		"Full Name": _full_name(register),
		"Mobile Number": [phone] if phone else [],
		"Fayda ID": (record.get("beneficiary") or {}).get("foundationalId"),
	}

	for label, path in FIELD_MAP.items():
		farmer[label] = _dig(attributes, path)

	for label, (mnemonic, path) in TABLE_FIELD_MAP.items():
		farmer[label] = next(
			(v for row in _tables(register, mnemonic) if (v := _dig(row, path)) not in (None, "")), None
		)

	farms = list(_tables(register, "FARM"))
	if farms:
		farmer["Farmland Size (Hectares)"] = _farmland_hectares(farms)

	# Drop empty values so the getter's defaults apply instead of blanks.
	farmer = {k: v for k, v in farmer.items() if v not in (None, "", [])}
	return {"farmer": farmer, "registries": registries}

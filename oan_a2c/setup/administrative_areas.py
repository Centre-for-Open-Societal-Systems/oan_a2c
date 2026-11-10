import gzip
import json
import os

import frappe

DOCTYPE = "A2C Administrative Area"
SEED_PATH = os.path.join(os.path.dirname(__file__), "data", "ethiopia_administrative_areas.json.gz")


def seed_administrative_areas():
	"""Load the Ethiopian Region > Zone > Woreda > Kebele tree (21,028 nodes) when the table is empty."""
	if frappe.db.count(DOCTYPE) or not os.path.exists(SEED_PATH):
		return 0

	with gzip.open(SEED_PATH, "rt", encoding="utf-8") as f:
		seed = json.load(f)

	frappe.db.bulk_insert(DOCTYPE, seed["columns"], seed["rows"], ignore_duplicates=True)
	return frappe.db.count(DOCTYPE)

"""Seed script for Banks, Products, Product Attributes, Categories, and Tags in OAN-A2C.

This script seeds:
- Multiple Participating Banks (Bank Accounts / Entities)
- Base Terms (Attributes, Categories, and Tags)
- Term Categories (with hierarchy and terms)
- Term Tags (with descriptions and terms)
- Loan Products (with attributes, metadata, category links, and tag links)
- Loan Product Attribute Lookups & Term Relationships

Usage:
  bench --site <site_name> execute oan_a2c.seed_catalog.seed_all
"""

import frappe


def seed_all():
	"""Run all seed catalog functions in dependency order. Idempotent."""
	frappe.set_user("Administrator")
	print("🌱 Starting Catalog Seeding (Banks, Products, Attributes, Categories, Tags)...")

	banks = seed_banks()
	terms = seed_terms()
	categories = seed_categories(terms)
	tags = seed_tags(terms)
	products = seed_products(banks)
	seed_product_attributes_and_relationships(products, banks, categories, tags, terms)

	frappe.db.commit()
	print("✅ Catalog Seeding completed successfully!")


def seed_banks():
	print("  - Seeding Participating Banks (Bank Accounts/Entities)...")
	banks_data = [
		{
			"bank_code": "CBE",
			"bank_name": "Commercial Bank of Ethiopia",
			"entity_type": "State-Owned Commercial Bank",
			"brand_name": "CBE Agro-Credit",
			"status": "Active",
			"website": "https://www.combanketh.et",
			"registered_email": "a2c-admin@cbe.com.et",
			"registered_phone": "+251115515004",
			"registered_city": "Addis Ababa",
			"registered_country": "Ethiopia",
			"commission_rate": 1.5,
			"gro_name": "Dawit Tesfaye",
			"gro_mobile": "+251911223344",
		},
		{
			"bank_code": "COOP",
			"bank_name": "Cooperative Bank of Oromia",
			"entity_type": "Cooperative Financial Institution",
			"brand_name": "CoopBank Agribusiness",
			"status": "Active",
			"website": "https://www.coopbankoromia.com.et",
			"registered_email": "agri@coopbankoromia.com.et",
			"registered_phone": "+251115150229",
			"registered_city": "Addis Ababa",
			"registered_country": "Ethiopia",
			"commission_rate": 1.2,
			"gro_name": "Tigist Alemu",
			"gro_mobile": "+251922334455",
		},
		{
			"bank_code": "AWASH",
			"bank_name": "Awash Bank",
			"entity_type": "Private Commercial Bank",
			"brand_name": "Awash Farmer Finance",
			"status": "Active",
			"website": "https://www.awashbank.com",
			"registered_email": "credit@awashbank.com",
			"registered_phone": "+251115570080",
			"registered_city": "Addis Ababa",
			"registered_country": "Ethiopia",
			"commission_rate": 1.8,
			"gro_name": "Yonas Hailu",
			"gro_mobile": "+251933445566",
		},
		{
			"bank_code": "DASHEN",
			"bank_name": "Dashen Bank",
			"entity_type": "Private Commercial Bank",
			"brand_name": "Dashen Rural Finance",
			"status": "Active",
			"website": "https://www.dashenbanksc.com",
			"registered_email": "info@dashenbanksc.com",
			"registered_phone": "+251115180353",
			"registered_city": "Addis Ababa",
			"registered_country": "Ethiopia",
			"commission_rate": 2.0,
			"gro_name": "Meron Assefa",
			"gro_mobile": "+251944556677",
		},
		{
			"bank_code": "BOA",
			"bank_name": "Bank of Abyssinia",
			"entity_type": "Private Commercial Bank",
			"brand_name": "Abyssinia Agri Loan",
			"status": "Active",
			"website": "https://www.bankofabyssinia.com",
			"registered_email": "agri@bankofabyssinia.com",
			"registered_phone": "+251115514130",
			"registered_city": "Addis Ababa",
			"registered_country": "Ethiopia",
			"commission_rate": 1.6,
			"gro_name": "Kifle Worku",
			"gro_mobile": "+251955667788",
		},
	]

	created_banks = {}
	for bdata in banks_data:
		code = bdata["bank_code"]
		existing = frappe.db.get_value("A2C Participating Bank", {"bank_code": code}, "name")
		if not existing:
			frappe.get_doc({"doctype": "A2C Participating Bank", **bdata}).insert(ignore_permissions=True)
			frappe.db.sql("UPDATE `tabA2C Participating Bank` SET name=%s WHERE bank_code=%s", (code, code))
			created_banks[code] = code
		else:
			created_banks[code] = existing

	return created_banks


def seed_terms():
	print("  - Seeding Base Terms (A2C Term)...")
	terms_data = [
		# Category terms
		{"term_id": "cat-land-property", "term_name": "Land & Property", "slug": "cat-land-property"},
		{"term_id": "cat-crop-production", "term_name": "Crop & Production", "slug": "cat-crop-production"},
		{
			"term_id": "cat-livestock-assets",
			"term_name": "Livestock & Assets",
			"slug": "cat-livestock-assets",
		},
		{
			"term_id": "cat-credit-financial",
			"term_name": "Credit & Financial",
			"slug": "cat-credit-financial",
		},
		{
			"term_id": "cat-coop-community",
			"term_name": "Cooperative & Community",
			"slug": "cat-coop-community",
		},
		{
			"term_id": "cat-irrigation-infra",
			"term_name": "Irrigation & Infrastructure",
			"slug": "cat-irrigation-infra",
		},
		# Tag terms
		{"term_id": "tag-fast-track", "term_name": "Fast-Track Approval", "slug": "tag-fast-track"},
		{"term_id": "tag-low-interest", "term_name": "Low-Interest Rate", "slug": "tag-low-interest"},
		{"term_id": "tag-no-collateral", "term_name": "Collateral-Free", "slug": "tag-no-collateral"},
		{"term_id": "tag-women-focused", "term_name": "Women-Farmer Special", "slug": "tag-women-focused"},
		{
			"term_id": "tag-emergency-relief",
			"term_name": "Emergency & Climate Relief",
			"slug": "tag-emergency-relief",
		},
		{"term_id": "tag-post-harvest", "term_name": "Post-Harvest Storage", "slug": "tag-post-harvest"},
		# Product Attribute terms
		{
			"term_id": "attr-land-registry",
			"term_name": "Land Registry Certificate",
			"slug": "attr-land-registry",
		},
		{
			"term_id": "attr-crop-registry",
			"term_name": "Crop Production Registry",
			"slug": "attr-crop-registry",
		},
		{
			"term_id": "attr-livestock-registry",
			"term_name": "Livestock Ownership Registry",
			"slug": "attr-livestock-registry",
		},
		{
			"term_id": "attr-credit-score-min",
			"term_name": "Minimum Credit Score (600+)",
			"slug": "attr-credit-score-min",
		},
		{
			"term_id": "attr-farm-size-min",
			"term_name": "Minimum Farm Size (1.5 Hectares)",
			"slug": "attr-farm-size-min",
		},
		{
			"term_id": "attr-coop-membership",
			"term_name": "Active Cooperative Membership",
			"slug": "attr-coop-membership",
		},
		{
			"term_id": "attr-annual-income-min",
			"term_name": "Minimum Annual Farm Income (50,000 ETB)",
			"slug": "attr-annual-income-min",
		},
		{
			"term_id": "attr-irrigation-access",
			"term_name": "Guaranteed Water Rights / Irrigation",
			"slug": "attr-irrigation-access",
		},
		{
			"term_id": "attr-warehouse-receipt",
			"term_name": "Warehouse Receipt System Certificate",
			"slug": "attr-warehouse-receipt",
		},
		{
			"term_id": "attr-harvest-guarantee",
			"term_name": "Off-taker Harvest Purchase Agreement",
			"slug": "attr-harvest-guarantee",
		},
	]

	created_terms = {}
	for tdata in terms_data:
		tid = tdata["term_id"]
		if not frappe.db.exists("A2C Term", tid):
			doc = frappe.get_doc({"doctype": "A2C Term", **tdata}).insert(ignore_permissions=True)
			created_terms[tid] = doc.name
		else:
			created_terms[tid] = tid

	return created_terms


def seed_categories(terms):
	print("  - Seeding Categories (A2C Term Category)...")
	categories_data = [
		{
			"term": "cat-land-property",
			"description": "Verification terms related to land ownership, land certificates, and parcel sizing",
			"generation": 1,
		},
		{
			"term": "cat-crop-production",
			"description": "Verification terms for crop yields, harvest history, seed varieties, and crop registries",
			"generation": 1,
		},
		{
			"term": "cat-livestock-assets",
			"description": "Verification terms for livestock inventory, dairy herds, feed assets, and farm machinery",
			"generation": 1,
		},
		{
			"term": "cat-credit-financial",
			"description": "Financial solvency, credit score thresholds, banking history, and repayment capacity",
			"generation": 1,
		},
		{
			"term": "cat-coop-community",
			"description": "Cooperative membership, union status, and community endorsement terms",
			"generation": 1,
		},
		{
			"term": "cat-irrigation-infra",
			"description": "Water access, irrigation pump infrastructure, solar installations, and greenhouse setups",
			"generation": 1,
		},
	]

	created_categories = {}
	for cdata in categories_data:
		term_id = cdata["term"]
		if not frappe.db.exists("A2C Term Category", term_id):
			doc = frappe.get_doc({"doctype": "A2C Term Category", **cdata}).insert(ignore_permissions=True)
			created_categories[term_id] = doc.name
		else:
			created_categories[term_id] = term_id

	return created_categories


def seed_tags(terms):
	print("  - Seeding Tags (A2C Term Tag)...")
	tags_data = [
		{
			"term": "tag-fast-track",
			"description": "Products with simplified documentation and 48-hour approval turnaround",
		},
		{
			"term": "tag-low-interest",
			"description": "Subsidized or low interest rates tailored for smallholder farmers",
		},
		{
			"term": "tag-no-collateral",
			"description": "Group-guaranteed or cashflow-based microloans requiring no physical land titles",
		},
		{
			"term": "tag-women-focused",
			"description": "Tailored loan terms and rate discounts for female agricultural entrepreneurs",
		},
		{
			"term": "tag-emergency-relief",
			"description": "Rapid disbursement micro-grants and emergency credit for climate recovery",
		},
		{
			"term": "tag-post-harvest",
			"description": "Financing dedicated to grain storage, cold chains, and warehouse receipt backed loans",
		},
	]

	created_tags = {}
	for tdata in tags_data:
		term_id = tdata["term"]
		if not frappe.db.exists("A2C Term Tag", term_id):
			doc = frappe.get_doc({"doctype": "A2C Term Tag", **tdata}).insert(ignore_permissions=True)
			created_tags[term_id] = doc.name
		else:
			created_tags[term_id] = term_id

	return created_tags


def seed_products(banks):
	print("  - Seeding Loan Products...")
	products_data = [
		{
			"product_name": "CBE Smallholder Input Loan",
			"bank": banks.get("CBE", "CBE"),
			"slug": "cbe-smallholder-input-loan",
			"min_interest_rate": 6.5,
			"max_interest_rate": 8.5,
			"min_amount": 10000,
			"max_amount": 100000,
			"tenure_months": 12,
			"status": "Active",
			"description": "Short-term microcredit for purchasing certified seeds, fertilizers, and crop protection inputs.",
			"product_meta": [
				{"meta_key": "repayment_frequency", "meta_value": "Seasonal (Post-Harvest)"},
				{"meta_key": "grace_period_months", "meta_value": "6"},
				{"meta_key": "collateral_required", "meta_value": "No"},
				{"meta_key": "target_crops", "meta_value": "Wheat, Maize, Teff"},
			],
		},
		{
			"product_name": "CBE Farm Mechanization Credit",
			"bank": banks.get("CBE", "CBE"),
			"slug": "cbe-farm-mechanization-credit",
			"min_interest_rate": 8.0,
			"max_interest_rate": 10.5,
			"min_amount": 100000,
			"max_amount": 1000000,
			"tenure_months": 36,
			"status": "Active",
			"description": "Medium-term equipment financing for tractors, irrigation pumps, threshers, and harvesting tools.",
			"product_meta": [
				{"meta_key": "repayment_frequency", "meta_value": "Quarterly"},
				{"meta_key": "grace_period_months", "meta_value": "3"},
				{"meta_key": "collateral_required", "meta_value": "Machinery Lien"},
			],
		},
		{
			"product_name": "CoopBank Oromia Crop Advance",
			"bank": banks.get("COOP", "COOP"),
			"slug": "coopbank-oromia-crop-advance",
			"min_interest_rate": 7.0,
			"max_interest_rate": 9.0,
			"min_amount": 20000,
			"max_amount": 250000,
			"tenure_months": 18,
			"status": "Active",
			"description": "Flexible working capital for cereal, pulse, and oilseed farmers across Oromia region.",
			"product_meta": [
				{"meta_key": "repayment_frequency", "meta_value": "Post-Harvest Lump Sum"},
				{"meta_key": "grace_period_months", "meta_value": "9"},
				{"meta_key": "collateral_required", "meta_value": "Cooperative Union Guarantee"},
			],
		},
		{
			"product_name": "CoopBank Livestock & Dairy Loan",
			"bank": banks.get("COOP", "COOP"),
			"slug": "coopbank-livestock-dairy-loan",
			"min_interest_rate": 9.0,
			"max_interest_rate": 11.0,
			"min_amount": 50000,
			"max_amount": 500000,
			"tenure_months": 24,
			"status": "Active",
			"description": "Financing for cattle fattening, dairy herd expansion, artificial insemination, and veterinary care.",
			"product_meta": [
				{"meta_key": "repayment_frequency", "meta_value": "Monthly"},
				{"meta_key": "grace_period_months", "meta_value": "2"},
				{"meta_key": "collateral_required", "meta_value": "Livestock Valuation & Tagging"},
			],
		},
		{
			"product_name": "Awash Agro-Processing Microfinance",
			"bank": banks.get("AWASH", "AWASH"),
			"slug": "awash-agro-processing-microfinance",
			"min_interest_rate": 10.0,
			"max_interest_rate": 12.0,
			"min_amount": 30000,
			"max_amount": 300000,
			"tenure_months": 12,
			"status": "Active",
			"description": "Post-harvest financing for grain milling, oil extraction, processing machinery, and storage.",
			"product_meta": [
				{"meta_key": "repayment_frequency", "meta_value": "Bi-Monthly"},
				{"meta_key": "grace_period_months", "meta_value": "4"},
				{"meta_key": "collateral_required", "meta_value": "Warehouse Receipt / Inventory"},
			],
		},
		{
			"product_name": "Dashen Solar Irrigation Finance",
			"bank": banks.get("DASHEN", "DASHEN"),
			"slug": "dashen-solar-irrigation-finance",
			"min_interest_rate": 7.5,
			"max_interest_rate": 9.5,
			"min_amount": 40000,
			"max_amount": 400000,
			"tenure_months": 24,
			"status": "Active",
			"description": "Clean energy credit line for installing solar water pumps and drip irrigation kits for smallholders.",
			"product_meta": [
				{"meta_key": "repayment_frequency", "meta_value": "Quarterly"},
				{"meta_key": "grace_period_months", "meta_value": "6"},
				{"meta_key": "collateral_required", "meta_value": "Solar Equipment Lien"},
			],
		},
		{
			"product_name": "Abyssinia Women Farmer Growth Line",
			"bank": banks.get("BOA", "BOA"),
			"slug": "abyssinia-women-farmer-growth-line",
			"min_interest_rate": 5.5,
			"max_interest_rate": 7.5,
			"min_amount": 15000,
			"max_amount": 150000,
			"tenure_months": 18,
			"status": "Active",
			"description": "Special concessionary credit line for female-headed agricultural households and cooperatives.",
			"product_meta": [
				{"meta_key": "repayment_frequency", "meta_value": "Seasonal"},
				{"meta_key": "grace_period_months", "meta_value": "6"},
				{"meta_key": "collateral_required", "meta_value": "Group Guarantee / Peer Security"},
			],
		},
	]

	created_products = {}
	for pdata in products_data:
		slug = pdata["slug"]
		if not frappe.db.exists("A2C Loan Product", {"slug": slug}):
			doc = frappe.get_doc({"doctype": "A2C Loan Product", **pdata}).insert(ignore_permissions=True)
			created_products[slug] = doc
		else:
			pname = frappe.db.get_value("A2C Loan Product", {"slug": slug}, "name")
			doc = frappe.get_doc("A2C Loan Product", pname)
			created_products[slug] = doc

	return created_products


def seed_product_attributes_and_relationships(products, banks, categories, tags, terms):
	print("  - Seeding Product Attributes (Lookups) & Term Relationships (Categories/Tags)...")

	# Map of product slug to taxonomy attributes, categories, and tags
	catalog_mappings = {
		"cbe-smallholder-input-loan": {
			"categories": ["cat-crop-production", "cat-credit-financial"],
			"tags": ["tag-fast-track", "tag-low-interest"],
			"attributes": {
				"Crop & Production": ["attr-crop-registry"],
				"Credit & Financial": ["attr-credit-score-min"],
				"Land & Property": ["attr-farm-size-min"],
			},
		},
		"cbe-farm-mechanization-credit": {
			"categories": ["cat-livestock-assets", "cat-land-property"],
			"tags": ["tag-fast-track"],
			"attributes": {
				"Land & Property": ["attr-land-registry", "attr-farm-size-min"],
				"Credit & Financial": ["attr-annual-income-min", "attr-credit-score-min"],
			},
		},
		"coopbank-oromia-crop-advance": {
			"categories": ["cat-crop-production", "cat-coop-community"],
			"tags": ["tag-no-collateral", "tag-low-interest"],
			"attributes": {
				"Cooperative & Community": ["attr-coop-membership"],
				"Crop & Production": ["attr-crop-registry", "attr-harvest-guarantee"],
			},
		},
		"coopbank-livestock-dairy-loan": {
			"categories": ["cat-livestock-assets"],
			"tags": ["tag-women-focused"],
			"attributes": {
				"Livestock & Assets": ["attr-livestock-registry"],
				"Credit & Financial": ["attr-credit-score-min"],
			},
		},
		"awash-agro-processing-microfinance": {
			"categories": ["cat-credit-financial", "cat-irrigation-infra"],
			"tags": ["tag-post-harvest", "tag-fast-track"],
			"attributes": {
				"Post-Harvest & Storage": ["attr-warehouse-receipt"],
				"Credit & Financial": ["attr-annual-income-min"],
			},
		},
		"dashen-solar-irrigation-finance": {
			"categories": ["cat-irrigation-infra", "cat-land-property"],
			"tags": ["tag-low-interest", "tag-women-focused"],
			"attributes": {
				"Irrigation & Infrastructure": ["attr-irrigation-access"],
				"Land & Property": ["attr-land-registry", "attr-farm-size-min"],
			},
		},
		"abyssinia-women-farmer-growth-line": {
			"categories": ["cat-coop-community", "cat-crop-production"],
			"tags": ["tag-women-focused", "tag-no-collateral", "tag-fast-track"],
			"attributes": {
				"Cooperative & Community": ["attr-coop-membership"],
				"Crop & Production": ["attr-crop-registry"],
			},
		},
	}

	for slug, mapping in catalog_mappings.items():
		product = products.get(slug)
		if not product:
			continue

		prod_name = product.name
		bank_code = product.bank

		# 1. Seed Category Relationships
		for cat_id in mapping.get("categories", []):
			if frappe.db.exists("A2C Term Category", cat_id):
				rel_exists = frappe.db.exists(
					"A2C Term Relationship",
					{"loan_product": prod_name, "term_type": "Category", "term_category": cat_id},
				)
				if not rel_exists:
					rel = frappe.get_doc(
						{
							"doctype": "A2C Term Relationship",
							"loan_product": prod_name,
							"term_type": "Category",
							"term_category": cat_id,
							"bank": bank_code,
						}
					)
					rel.insert(ignore_permissions=True)

		# 2. Seed Tag Relationships
		for tag_id in mapping.get("tags", []):
			if frappe.db.exists("A2C Term Tag", tag_id):
				rel_exists = frappe.db.exists(
					"A2C Term Relationship",
					{"loan_product": prod_name, "term_type": "Tag", "term_tag": tag_id},
				)
				if not rel_exists:
					rel = frappe.get_doc(
						{
							"doctype": "A2C Term Relationship",
							"loan_product": prod_name,
							"term_type": "Tag",
							"term_tag": tag_id,
							"bank": bank_code,
						}
					)
					rel.insert(ignore_permissions=True)

		# 3. Seed Product Attributes (A2C Loan Product Attribute Lookup)
		for taxonomy, attr_term_ids in mapping.get("attributes", {}).items():
			for term_id in attr_term_ids:
				if frappe.db.exists("A2C Term", term_id):
					lookup_exists = frappe.db.exists(
						"A2C Loan Product Attribute Lookup",
						{"loan_product": prod_name, "taxonomy": taxonomy, "term_id": term_id},
					)
					if not lookup_exists:
						lookup = frappe.get_doc(
							{
								"doctype": "A2C Loan Product Attribute Lookup",
								"loan_product": prod_name,
								"bank": bank_code,
								"taxonomy": taxonomy,
								"term_id": term_id,
								"accepting": 1,
							}
						)
						lookup.insert(ignore_permissions=True)


if __name__ == "__main__":
	seed_all()

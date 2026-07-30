"""Maximal Seed Script for OpenAgriNet Access to Credit (OAN-A2C).

This module seeds a comprehensive, production-realistic dataset across all A2C Doctypes:
- Terms & Term Categories
- Participating Banks
- Bank Roles, Users & User Permissions
- Loan Products & Product Meta
- Farmer Profiles & Credit Information
- Leads & Lead Audit Events
- Loan Applications & Snapshots
- Real-time & Bell Notifications (Notification Log)
- Consent Requests & Data

Usage:
  bench --site <site_name> execute oan_a2c.seed.seed_all
"""

import frappe
from frappe.utils import add_days, now, nowdate

from oan_a2c.a2c_marketplace.roles import (
	A2C_ADMIN_ROLE,
	BANK_ADMIN_ROLE,
	BANK_AGENT_ROLE,
	BANK_ROLES,
)


def seed_all():
	"""Run all seed functions in dependency order. Idempotent."""
	frappe.set_user("Administrator")
	print("🌱 Starting maximal seeding for oan_a2c...")

	seed_terms_and_categories()
	banks = seed_participating_banks()

	# Run Catalog Seeding for Banks, Products, Attributes, Categories & Tags
	from oan_a2c.seed_catalog import seed_all as seed_catalog_all

	seed_catalog_all()

	users = seed_users_and_permissions(banks)
	products = seed_loan_products(banks)
	farmers, _credit_infos = seed_farmers_and_credit_info()
	leads = seed_leads(farmers, users)
	applications = seed_loan_applications(banks, products, farmers, leads, users)
	seed_notifications(users, applications, products)
	seed_consent_records(farmers, banks)

	frappe.db.commit()
	print("✅ Maximal seeding completed successfully!")


def seed_terms_and_categories():
	print("  - Seeding Terms & Term Categories...")
	categories = [
		{
			"name": "Land & Property",
			"description": "Verification terms related to land ownership and certificates",
		},
		{
			"name": "Crop & Production",
			"description": "Verification terms for crop yields, harvest history, and registries",
		},
		{
			"name": "Livestock & Assets",
			"description": "Verification terms for livestock inventory and farm machinery",
		},
		{
			"name": "Credit & Financial",
			"description": "Financial solvency, credit score thresholds, and banking history",
		},
		{
			"name": "Cooperative & Community",
			"description": "Cooperative membership and community endorsement terms",
		},
	]

	for cat in categories:
		if not frappe.db.exists("A2C Term Category", cat["name"]):
			frappe.get_doc({"doctype": "A2C Term Category", **cat}).insert(ignore_permissions=True)

	terms = [
		{"term_id": "land-registry", "term_name": "Land Registry Certificate", "slug": "land-registry"},
		{"term_id": "crop-registry", "term_name": "Crop Production Registry", "slug": "crop-registry"},
		{
			"term_id": "livestock-registry",
			"term_name": "Livestock Ownership Registry",
			"slug": "livestock-registry",
		},
		{
			"term_id": "credit-score-min",
			"term_name": "Minimum Credit Score (600+)",
			"slug": "credit-score-min",
		},
		{
			"term_id": "farm-size-min",
			"term_name": "Minimum Farm Size (1.5 Hectares)",
			"slug": "farm-size-min",
		},
		{
			"term_id": "coop-membership",
			"term_name": "Active Cooperative Membership",
			"slug": "coop-membership",
		},
		{
			"term_id": "annual-income-min",
			"term_name": "Minimum Annual Farm Income (50,000 ETB)",
			"slug": "annual-income-min",
		},
	]

	for t in terms:
		if not frappe.db.exists("A2C Term", t["term_id"]):
			frappe.get_doc({"doctype": "A2C Term", **t}).insert(ignore_permissions=True)


def seed_participating_banks():
	print("  - Seeding Participating Banks...")
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
			"status": "Onboarding",
			"website": "https://www.dashenbanksc.com",
			"registered_email": "info@dashenbanksc.com",
			"registered_phone": "+251115180353",
			"registered_city": "Addis Ababa",
			"registered_country": "Ethiopia",
			"commission_rate": 2.0,
			"gro_name": "Meron Assefa",
			"gro_mobile": "+251944556677",
		},
	]

	created_banks = {}
	for bdata in banks_data:
		code = bdata["bank_code"]
		existing = frappe.db.get_value("A2C Participating Bank", {"bank_code": code}, "name")
		if not existing:
			frappe.get_doc({"doctype": "A2C Participating Bank", **bdata}).insert(ignore_permissions=True)
			# Update name if SQL update needed to enforce code as name in test environments
			frappe.db.sql("UPDATE `tabA2C Participating Bank` SET name=%s WHERE bank_code=%s", (code, code))
			created_banks[code] = code
		else:
			created_banks[code] = existing

	return created_banks


def seed_users_and_permissions(banks):
	print("  - Seeding Users, Roles & Permissions...")
	users_data = [
		{
			"email": "cbe_admin@example.com",
			"first_name": "CBE",
			"last_name": "Admin",
			"role": BANK_ADMIN_ROLE,
			"bank": "CBE",
		},
		{
			"email": "cbe_agent@example.com",
			"first_name": "CBE",
			"last_name": "Agent",
			"role": BANK_AGENT_ROLE,
			"bank": "CBE",
		},
		{
			"email": "coop_admin@example.com",
			"first_name": "Coop",
			"last_name": "Admin",
			"role": BANK_ADMIN_ROLE,
			"bank": "COOP",
		},
		{
			"email": "coop_agent@example.com",
			"first_name": "Coop",
			"last_name": "Agent",
			"role": BANK_AGENT_ROLE,
			"bank": "COOP",
		},
		{
			"email": "awash_admin@example.com",
			"first_name": "Awash",
			"last_name": "Admin",
			"role": BANK_ADMIN_ROLE,
			"bank": "AWASH",
		},
		{
			"email": "awash_agent@example.com",
			"first_name": "Awash",
			"last_name": "Agent",
			"role": BANK_AGENT_ROLE,
			"bank": "AWASH",
		},
	]

	users_map = {}
	for udata in users_data:
		email = udata["email"]
		bank_code = udata["bank"]

		if not frappe.db.exists("User", email):
			user_doc = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": udata["first_name"],
					"last_name": udata["last_name"],
					"send_welcome_email": 0,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		else:
			user_doc = frappe.get_doc("User", email)

		# Add Role
		if udata["role"] not in [r.role for r in user_doc.roles]:
			user_doc.add_roles(udata["role"])

		# Add User Permission for Bank
		target_bank = banks.get(bank_code, bank_code)
		if not frappe.db.exists(
			"User Permission", {"user": email, "allow": "A2C Participating Bank", "for_value": target_bank}
		):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": email,
					"allow": "A2C Participating Bank",
					"for_value": target_bank,
				}
			).insert(ignore_permissions=True)

		users_map[email] = user_doc

	return users_map


def seed_loan_products(banks):
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
			"description": "Medium-term equipment financing for tractors, irrigation pumps, and threshers.",
		},
		{
			"product_name": "CBE Emergency Seed Grant",
			"bank": banks.get("CBE", "CBE"),
			"slug": "cbe-emergency-seed-grant",
			"min_interest_rate": 5.0,
			"max_interest_rate": 6.0,
			"min_amount": 5000,
			"max_amount": 30000,
			"tenure_months": 6,
			"status": "Draft",
			"description": "Draft product pending bank admin approval for drought-affected smallholders.",
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
			"description": "Flexible working capital for cereal and pulse farmers in Oromia region.",
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
			"description": "Financing for cattle fattening, dairy herd expansion, and veterinary care.",
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
			"description": "Post-harvest financing for grain milling, oil extraction, and storage.",
		},
	]

	created_products = []
	for pdata in products_data:
		if not frappe.db.exists("A2C Loan Product", {"slug": pdata["slug"]}):
			doc = frappe.get_doc({"doctype": "A2C Loan Product", **pdata}).insert(ignore_permissions=True)
			created_products.append(doc)
		else:
			name = frappe.db.get_value("A2C Loan Product", {"slug": pdata["slug"]}, "name")
			created_products.append(frappe.get_doc("A2C Loan Product", name))

	return created_products


def seed_farmers_and_credit_info():
	print("  - Seeding Farmers & Credit Profiles...")
	farmers_data = [
		{
			"national_id": "ETH-99011234",
			"first_name": "Abebe",
			"last_name": "Bikila",
			"phone_number": "+251911110001",
			"email": "abebe.bikila@example.com",
			"region": "Oromia",
			"woreda": "Lume",
			"kebele": "Mojo 01",
			"farmland_size_hectares": 3.5,
			"land_ownership_status": "Owned",
			"gender": "Male",
			"education_level": "Secondary",
			"source_of_income": "Agriculture",
			"size_of_family": 5,
			"no_of_males_family": 3,
			"no_of_females_family": 2,
		},
		{
			"national_id": "ETH-99022345",
			"first_name": "Kebede",
			"last_name": "Tadesse",
			"phone_number": "+251922220002",
			"email": "kebede.tadesse@example.com",
			"region": "Amhara",
			"woreda": "Debre Birhan",
			"kebele": "Kebele 03",
			"farmland_size_hectares": 2.0,
			"land_ownership_status": "Crop Sharing",
			"gender": "Male",
			"education_level": "Primary",
			"source_of_income": "Agriculture",
			"size_of_family": 4,
			"no_of_males_family": 2,
			"no_of_females_family": 2,
		},
		{
			"national_id": "ETH-99033456",
			"first_name": "Almaz",
			"last_name": "Ayana",
			"phone_number": "+251933330003",
			"email": "almaz.ayana@example.com",
			"region": "Oromia",
			"woreda": "Holeta",
			"kebele": "Kebele 02",
			"farmland_size_hectares": 4.2,
			"land_ownership_status": "Owned",
			"gender": "Female",
			"education_level": "Tertiary",
			"source_of_income": "Mixed Farming",
			"size_of_family": 6,
			"no_of_males_family": 2,
			"no_of_females_family": 4,
		},
		{
			"national_id": "ETH-99044567",
			"first_name": "Derartu",
			"last_name": "Tulu",
			"phone_number": "+251944440004",
			"email": "derartu.tulu@example.com",
			"region": "Sidama",
			"woreda": "Hawassa Zuria",
			"kebele": "Tula 01",
			"farmland_size_hectares": 1.8,
			"land_ownership_status": "Rented",
			"gender": "Female",
			"education_level": "Secondary",
			"source_of_income": "Coffee & Farming",
			"size_of_family": 3,
			"no_of_males_family": 1,
			"no_of_females_family": 2,
		},
		{
			"national_id": "ETH-99055678",
			"first_name": "Haile",
			"last_name": "Gebrselassie",
			"phone_number": "+251955550005",
			"email": "haile.g@example.com",
			"region": "SNNPR",
			"woreda": "Arba Minch",
			"kebele": "Chamo 02",
			"farmland_size_hectares": 5.0,
			"land_ownership_status": "Owned",
			"gender": "Male",
			"education_level": "Secondary",
			"source_of_income": "Horticulture",
			"size_of_family": 7,
			"no_of_males_family": 4,
			"no_of_females_family": 3,
		},
	]

	created_farmers = []
	created_credit_infos = []

	for fdata in farmers_data:
		nid = fdata["national_id"]
		if not frappe.db.exists("A2C Farmer Profile", {"national_id": nid}):
			farmer = frappe.get_doc({"doctype": "A2C Farmer Profile", **fdata}).insert(
				ignore_permissions=True
			)
		else:
			fname = frappe.db.get_value("A2C Farmer Profile", {"national_id": nid}, "name")
			farmer = frappe.get_doc("A2C Farmer Profile", fname)
		created_farmers.append(farmer)

		# Credit Information for this farmer
		phone = fdata["phone_number"]
		credit_data = {
			"phone_number": phone,
			"first_name": fdata["first_name"],
			"last_name": fdata["last_name"],
			"national_id": nid,
			"credit_score": 720
			if fdata["first_name"] == "Abebe"
			else (650 if fdata["first_name"] == "Kebede" else 780),
			"recommended_credit_limit": fdata["farmland_size_hectares"] * 50000,
			"risk_category": "Low Risk" if fdata["farmland_size_hectares"] > 3.0 else "Medium Risk",
			"existing_debt": 0 if fdata["farmland_size_hectares"] > 3.0 else 15000,
			"primary_crop": "Wheat" if fdata["region"] == "Amhara" else "Maize",
		}
		if not frappe.db.exists("A2C Credit Information", {"phone_number": phone}):
			ci = frappe.get_doc({"doctype": "A2C Credit Information", **credit_data}).insert(
				ignore_permissions=True
			)
		else:
			ciname = frappe.db.get_value("A2C Credit Information", {"phone_number": phone}, "name")
			ci = frappe.get_doc("A2C Credit Information", ciname)
		created_credit_infos.append(ci)

	return created_farmers, created_credit_infos


def seed_leads(farmers, users):
	print("  - Seeding Leads...")
	cbe_agent = "cbe_agent@example.com"
	coop_agent = "coop_agent@example.com"

	leads_data = [
		{
			"phone_number": "+251911110001",
			"first_name": "Abebe",
			"last_name": "Bikila",
			"email": "abebe.bikila@example.com",
			"lead_source": "IVR",
			"status": "Verified",
			"assigned_to": cbe_agent,
			"assigned_date": nowdate(),
			"loan_amount": 50000,
			"farmer_profile": farmers[0].name,
		},
		{
			"phone_number": "+251922220002",
			"first_name": "Kebede",
			"last_name": "Tadesse",
			"email": "kebede.tadesse@example.com",
			"lead_source": "SMS",
			"status": "Active",
			"assigned_to": cbe_agent,
			"assigned_date": nowdate(),
			"loan_amount": 25000,
			"farmer_profile": farmers[1].name,
		},
		{
			"phone_number": "+251933330003",
			"first_name": "Almaz",
			"last_name": "Ayana",
			"email": "almaz.ayana@example.com",
			"lead_source": "Agent Entry",
			"status": "Processed",
			"assigned_to": coop_agent,
			"assigned_date": nowdate(),
			"loan_amount": 150000,
			"farmer_profile": farmers[2].name,
		},
		{
			"phone_number": "+251944440004",
			"first_name": "Derartu",
			"last_name": "Tulu",
			"email": "derartu.tulu@example.com",
			"lead_source": "Missed Call",
			"status": "Granted",
			"assigned_to": coop_agent,
			"assigned_date": nowdate(),
			"loan_amount": 80000,
			"farmer_profile": farmers[3].name,
		},
		{
			"phone_number": "+251955550005",
			"first_name": "Haile",
			"last_name": "Gebrselassie",
			"email": "haile.g@example.com",
			"lead_source": "IVR",
			"status": "Dormant",
			"assigned_to": cbe_agent,
			"assigned_date": nowdate(),
			"loan_amount": 200000,
			"farmer_profile": farmers[4].name,
		},
	]

	created_leads = []
	for ldata in leads_data:
		phone = ldata["phone_number"]
		if not frappe.db.exists("A2C Lead", {"phone_number": phone}):
			lead = frappe.get_doc({"doctype": "A2C Lead", **ldata}).insert(ignore_permissions=True)
		else:
			lname = frappe.db.get_value("A2C Lead", {"phone_number": phone}, "name")
			lead = frappe.get_doc("A2C Lead", lname)
		created_leads.append(lead)

	return created_leads


def seed_loan_applications(banks, products, farmers, leads, users):
	print("  - Seeding Loan Applications...")
	apps_data = [
		{
			"bank": banks.get("CBE", "CBE"),
			"loan_product": products[0].name,
			"lead_id": leads[0].name,
			"farmer_id": farmers[0].name,
			"first_name": farmers[0].first_name,
			"last_name": farmers[0].last_name,
			"phone_number": farmers[0].phone_number,
			"email": farmers[0].email,
			"region": farmers[0].region,
			"woreda": farmers[0].woreda,
			"kebele": farmers[0].kebele,
			"requested_amount": 50000,
			"approved_amount": 50000,
			"interest_rate": 7.5,
			"tenure_months": 12,
			"status": "Approved",
			"current_step": 4,
			"loan_officer": "cbe_agent@example.com",
			"loan_reason": "Purchasing certified wheat seeds and fertilizer",
		},
		{
			"bank": banks.get("CBE", "CBE"),
			"loan_product": products[1].name,
			"lead_id": leads[1].name,
			"farmer_id": farmers[1].name,
			"first_name": farmers[1].first_name,
			"last_name": farmers[1].last_name,
			"phone_number": farmers[1].phone_number,
			"email": farmers[1].email,
			"region": farmers[1].region,
			"woreda": farmers[1].woreda,
			"kebele": farmers[1].kebele,
			"requested_amount": 150000,
			"approved_amount": 0,
			"interest_rate": 8.5,
			"tenure_months": 24,
			"status": "Processing",
			"current_step": 2,
			"loan_officer": "cbe_agent@example.com",
			"loan_reason": "Co-funding a walking tractor and pump",
		},
		{
			"bank": banks.get("COOP", "COOP"),
			"loan_product": products[3].name,
			"lead_id": leads[2].name,
			"farmer_id": farmers[2].name,
			"first_name": farmers[2].first_name,
			"last_name": farmers[2].last_name,
			"phone_number": farmers[2].phone_number,
			"email": farmers[2].email,
			"region": farmers[2].region,
			"woreda": farmers[2].woreda,
			"kebele": farmers[2].kebele,
			"requested_amount": 120000,
			"approved_amount": 120000,
			"interest_rate": 7.0,
			"tenure_months": 18,
			"status": "Approved",
			"current_step": 4,
			"loan_officer": "coop_agent@example.com",
			"loan_reason": "Pre-harvest working capital for maize field expansion",
		},
		{
			"bank": banks.get("COOP", "COOP"),
			"loan_product": products[4].name,
			"lead_id": leads[3].name,
			"farmer_id": farmers[3].name,
			"first_name": farmers[3].first_name,
			"last_name": farmers[3].last_name,
			"phone_number": farmers[3].phone_number,
			"email": farmers[3].email,
			"region": farmers[3].region,
			"woreda": farmers[3].woreda,
			"kebele": farmers[3].kebele,
			"requested_amount": 80000,
			"approved_amount": 0,
			"interest_rate": 9.0,
			"tenure_months": 12,
			"status": "Rejected",
			"current_step": 4,
			"loan_officer": "coop_agent@example.com",
			"loan_reason": "Dairy herd expansion - failed collateral verification",
		},
		{
			"bank": banks.get("AWASH", "AWASH"),
			"loan_product": products[5].name,
			"lead_id": leads[4].name,
			"farmer_id": farmers[4].name,
			"first_name": farmers[4].first_name,
			"last_name": farmers[4].last_name,
			"phone_number": farmers[4].phone_number,
			"email": farmers[4].email,
			"region": farmers[4].region,
			"woreda": farmers[4].woreda,
			"kebele": farmers[4].kebele,
			"requested_amount": 200000,
			"approved_amount": 0,
			"interest_rate": 10.0,
			"tenure_months": 12,
			"status": "Draft",
			"current_step": 1,
			"loan_officer": "awash_agent@example.com",
			"loan_reason": "Post-harvest grain storage facility construction",
		},
	]

	created_apps = []
	for adata in apps_data:
		existing = frappe.db.get_value(
			"A2C Loan Application",
			{"farmer_id": adata["farmer_id"], "loan_product": adata["loan_product"]},
			"name",
		)
		if not existing:
			app = frappe.get_doc({"doctype": "A2C Loan Application", **adata}).insert(ignore_permissions=True)
		else:
			app = frappe.get_doc("A2C Loan Application", existing)
		created_apps.append(app)

	return created_apps


def seed_notifications(users, applications, products):
	print("  - Seeding Notification Logs (Bell Notifications)...")
	notifications = [
		{
			"for_user": "cbe_admin@example.com",
			"type": "Alert",
			"subject": "New loan product created",
			"email_content": f"New loan product {products[2].product_name} created in Draft status.",
			"document_type": "A2C Loan Product",
			"document_name": products[2].name,
			"read": 0,
		},
		{
			"for_user": "cbe_agent@example.com",
			"type": "Alert",
			"subject": "New loan application submitted",
			"email_content": f"New application submitted by Abebe Bikila for {products[0].product_name}.",
			"document_type": "A2C Loan Application",
			"document_name": applications[0].name,
			"read": 0,
		},
		{
			"for_user": "cbe_agent@example.com",
			"type": "Alert",
			"subject": "KYC status updated",
			"email_content": "KYC status updated for Kebede Tadesse.",
			"document_type": "A2C Lead",
			"document_name": applications[1].lead_id,
			"read": 1,
		},
		{
			"for_user": "coop_agent@example.com",
			"type": "Alert",
			"subject": "Approval pending for application",
			"email_content": f"Approval decision pending for {applications[2].name}.",
			"document_type": "A2C Loan Application",
			"document_name": applications[2].name,
			"read": 0,
		},
		{
			"for_user": "coop_admin@example.com",
			"type": "Alert",
			"subject": "Loan application decision updated",
			"email_content": f"Application {applications[3].name} was Rejected by coop_agent@example.com.",
			"document_type": "A2C Loan Application",
			"document_name": applications[3].name,
			"read": 1,
		},
		{
			"for_user": "awash_admin@example.com",
			"type": "Alert",
			"subject": "New bank agent assigned",
			"email_content": "User awash_agent@example.com has been granted Bank Agent permissions.",
			"document_type": "User",
			"document_name": "awash_agent@example.com",
			"read": 0,
		},
	]

	for n in notifications:
		if not frappe.db.exists("Notification Log", {"for_user": n["for_user"], "subject": n["subject"]}):
			frappe.get_doc({"doctype": "Notification Log", **n}).insert(ignore_permissions=True)


def seed_consent_records(farmers, banks):
	print("  - Seeding Consent Requests & Data...")
	consents = [
		{
			"farmer_id": farmers[0].name,
			"phone_number": farmers[0].phone_number,
			"bank": banks.get("CBE", "CBE"),
			"status": "Granted",
			"consent_scope": "land_registry,credit_score,crop_yield",
			"expires_on": add_days(nowdate(), 180),
		},
		{
			"farmer_id": farmers[2].name,
			"phone_number": farmers[2].phone_number,
			"bank": banks.get("COOP", "COOP"),
			"status": "Granted",
			"consent_scope": "land_registry,credit_score",
			"expires_on": add_days(nowdate(), 90),
		},
		{
			"farmer_id": farmers[4].name,
			"phone_number": farmers[4].phone_number,
			"bank": banks.get("AWASH", "AWASH"),
			"status": "Pending",
			"consent_scope": "credit_score",
			"expires_on": add_days(nowdate(), 30),
		},
	]

	for c in consents:
		if frappe.db.exists("DocType", "A2C Consent Request"):
			if not frappe.db.exists("A2C Consent Request", {"farmer_id": c["farmer_id"], "bank": c["bank"]}):
				frappe.get_doc({"doctype": "A2C Consent Request", **c}).insert(ignore_permissions=True)


if __name__ == "__main__":
	seed_all()

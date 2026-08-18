import frappe

def seed_loans():
	banks_to_create = [
		{"name": "Commercial Bank of Ethiopia", "code": "CBE"},
		{"name": "Awash Bank", "code": "AIB"},
		{"name": "Dashen Bank", "code": "DB"},
		{"name": "Bank of Abyssinia", "code": "BOA"},
		{"name": "Cooperative Bank of Oromia", "code": "CBO"}
	]
	
	for bank in banks_to_create:
		if not frappe.db.exists("A2C Participating Bank", {"bank_name": bank["name"]}):
			doc = frappe.get_doc({
				"doctype": "A2C Participating Bank",
				"bank_name": bank["name"],
				"bank_code": bank["code"],
				"entity_type": "Bank",
				"registered_email": f"contact@{bank['code'].lower()}.local",
				"registered_phone": "+251911000000",
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
				"status": "Active",
				"kyc_document": "http://example.com/kyc.pdf",
				"gro_name": "John Doe",
				"ops_name": "Jane Doe"
			})
			try:
				doc.insert(ignore_permissions=True, ignore_mandatory=True)
			except Exception as e:
				print(f"Failed to insert bank {bank['name']}: {e}")

	loans_to_create = [
		{
			"doctype": "A2C Loan Product",
			"product_name": "Agricultural Loan",
			"bank": "Commercial Bank of Ethiopia",
			"min_amount": 10000,
			"max_amount": 200000,
			"min_interest_rate": 8.5,
			"max_interest_rate": 12.0,
			"tenure_months": 12,
			"description": "General purpose agricultural loan for all farming needs.",
			"status": "Active"
		},
		{
			"doctype": "A2C Loan Product",
			"product_name": "Fertilizer Credit",
			"bank": "Awash Bank",
			"min_amount": 5000,
			"max_amount": 150000,
			"min_interest_rate": 9.0,
			"max_interest_rate": 11.5,
			"tenure_months": 6,
			"description": "Short-term credit line designed specifically for fertilizer and chemical inputs.",
			"status": "Active"
		},
		{
			"doctype": "A2C Loan Product",
			"product_name": "Equipment Financing",
			"bank": "Dashen Bank",
			"min_amount": 50000,
			"max_amount": 1000000,
			"min_interest_rate": 7.5,
			"max_interest_rate": 10.0,
			"tenure_months": 36,
			"description": "Long-term financing for tractors, harvesters, and other major farming equipment.",
			"status": "Active"
		},
		{
			"doctype": "A2C Loan Product",
			"product_name": "Seed Loan",
			"bank": "Bank of Abyssinia",
			"min_amount": 2000,
			"max_amount": 50000,
			"min_interest_rate": 8.0,
			"max_interest_rate": 10.5,
			"tenure_months": 4,
			"description": "Micro-loan for purchasing high-yield seeds before the planting season.",
			"status": "Active"
		},
		{
			"doctype": "A2C Loan Product",
			"product_name": "Livestock Loan",
			"bank": "Cooperative Bank of Oromia",
			"min_amount": 20000,
			"max_amount": 300000,
			"min_interest_rate": 9.5,
			"max_interest_rate": 12.5,
			"tenure_months": 24,
			"description": "Specialized loan for purchasing livestock for breeding or meat production.",
			"status": "Active"
		}
	]

	created = 0
	for data in loans_to_create:
		bank_id = frappe.db.get_value("A2C Participating Bank", {"bank_name": data["bank"]}, "name")
		if bank_id:
			data["bank"] = bank_id
			if not frappe.db.exists("A2C Loan Product", {"product_name": data["product_name"], "bank": bank_id}):
				try:
					doc = frappe.get_doc(data)
					doc.insert(ignore_permissions=True, ignore_mandatory=True)
					created += 1
				except Exception as e:
					print(f"Failed to insert loan product {data['product_name']}: {e}")
		else:
			print(f"Could not find bank ID for {data['bank']}")

	frappe.db.commit()
	return f"Created {created} loan products."

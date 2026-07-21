import frappe
from frappe.utils import text_type

def execute():
    frappe.flags.in_test = True # Bypass permission checks during seeding
    
    # 1. Create Taxonomies
    taxonomies = [
        {"name": "loan_category", "description": "Loan Categories"},
        {"name": "loan_tag", "description": "Loan Tags"},
        {"name": "pa_region", "description": "Region Attribute"},
        {"name": "pa_land_size_band", "description": "Land Size Band Attribute"},
        {"name": "pa_crop_type", "description": "Crop Type Attribute"},
        {"name": "pa_rate_type", "description": "Rate Type Attribute"},
        {"name": "pa_tenure_band", "description": "Tenure Band Attribute"}
    ]
    
    for tax in taxonomies:
        if not frappe.db.exists("A2C Term Taxonomy", tax["name"]):
            doc = frappe.get_doc({
                "doctype": "A2C Term Taxonomy",
                "taxonomy": tax["name"],
                "description": tax["description"]
            })
            doc.insert(ignore_permissions=True)
            print(f"Created A2C Term Taxonomy: {tax['name']}")
            
    # 2. Create Terms
    terms_data = [
        # loan_category
        {"name": "Input Financing", "taxonomy": "loan_category"},
        {"name": "Machinery/Equipment", "taxonomy": "loan_category"},
        {"name": "Conventional", "taxonomy": "loan_category"},
        {"name": "Murabaha", "taxonomy": "loan_category"},
        
        # pa_rate_type
        {"name": "Fixed", "taxonomy": "pa_rate_type"},
        {"name": "Floating", "taxonomy": "pa_rate_type"}
    ]
    
    for td in terms_data:
        # Check if term exists (the label)
        from frappe.utils import slug
        term_name_exists = frappe.db.exists("A2C Term", {"term_name": td["name"]})
        term_name = term_name_exists
        
        if not term_name_exists:
            term_doc = frappe.get_doc({
                "doctype": "A2C Term",
                "term_name": td["name"],
                "slug": slug(text_type(td["name"]))
            })
            term_doc.insert(ignore_permissions=True)
            term_name = term_doc.name
            print(f"Created A2C Term: {td['name']}")
            
        # Create Term Taxonomy mapping if it doesn't exist
        tax_id = f"{term_name}-{td['taxonomy']}"
        tax_exists = frappe.db.exists("A2C Term Taxonomy", tax_id)
        if not tax_exists:
            tax_doc = frappe.get_doc({
                "doctype": "A2C Term Taxonomy",
                "term": term_name,
                "taxonomy": td["taxonomy"]
            })
            tax_doc.insert(ignore_permissions=True)
            print(f"Created A2C Term Taxonomy for {td['name']} in {td['taxonomy']}")
    
    frappe.db.commit()
    print("Taxonomies and terms seeded successfully.")

import json

base_path = '/workspace/development/frappe-bench/apps/oan_a2c/oan_a2c/openagrinet_access_to_credit/doctype/'
farmer_path = base_path + 'farmer_profile/farmer_profile.json'
loan_path = base_path + 'loan_application/loan_application.json'

with open(farmer_path, 'r') as f:
    farmer = json.load(f)

fields = farmer['fields']
if not any(f.get('fieldname') == 'farmer_id' for f in fields):
    fields.append({
        "fieldname": "farmer_id",
        "fieldtype": "Data",
        "label": "Farmer ID"
    })
    farmer['field_order'].append('farmer_id')

with open(farmer_path, 'w') as f:
    json.dump(farmer, f, indent=1)


with open(loan_path, 'r') as f:
    loan = json.load(f)

fields_to_keep = [
    'application_id', 'status', 'current_step', 'loan_officer',
    'consent_request', 'consent_receipt'
]

new_loan_fields = []
has_lead_id = False
for field in loan['fields']:
    if field['fieldname'] in fields_to_keep:
        new_loan_fields.append(field)
    elif field['fieldname'] == 'farmer':
        field['fieldname'] = 'farmer_profile'
        field['label'] = 'Farmer Profile'
        field['options'] = 'Farmer Profile'
        new_loan_fields.append(field)
    elif field['fieldname'] == 'lead_id':
        has_lead_id = True
        new_loan_fields.append(field)
        
if not has_lead_id:
    new_loan_fields.append({
        "fieldname": "lead_id",
        "fieldtype": "Link",
        "label": "Lead ID",
        "options": "A2C Lead"
    })

loan['fields'] = new_loan_fields
loan['field_order'] = [f['fieldname'] for f in new_loan_fields]

with open(loan_path, 'w') as f:
    json.dump(loan, f, indent=1)

print("JSON files updated successfully!")

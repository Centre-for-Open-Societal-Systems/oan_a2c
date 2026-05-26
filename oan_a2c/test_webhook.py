import requests
import json

FRAPPE_URL = "http://localhost:8000"

payload = {
  "source": "g2p_ati_consent_mgt",
  "event_type": "WEBSUB_INDIVIDUAL_UPDATED",
  "published_at": "2026-05-25 14:48:40",
  "consent": {
    "id": 83,
    "consent_creation_request_id": "3dd01408-87fb-4a87-9514-0b9c8411efbb",
    "consent_type": "specific",
    "status": "approved",
    "approved_at": "2026-05-25 14:48:40",
    "requested_field_codes": ["farmer_basic", "phone_number"],
    "published_field_codes": ["farmer_basic", "phone_number"],
    "data_field_mode": "dynamic"
  },
  "consent_partner": {
    "id": 67,
    "name": "Test Application (A2C)"
  },
  "farmer": {
    "id": 63,
    "name": "GEE PAUL VARGHESE"
  },
  "selected_data": {
    "farmer": {
      "given_name": "gee",
      "family_name": "paul",
      "email": "testemail@gmail.com",
      "phone_no": ["9988776655"]
    },
    "phone_number": ["9988776655"]
  }
}

response = requests.post(
    f"{FRAPPE_URL}/api/method/oan_a2c.api.webhook_api.receive_consent_data",
    json=payload,
    headers={"Content-Type": "application/json"},
    timeout=30
)

print(f"Status Code: {response.status_code}")
print(f"Response: {response.text}")

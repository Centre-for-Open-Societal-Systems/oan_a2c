import json

import requests

url = "https://raw.githubusercontent.com/frappe/semgrep-rules/main/rules/frappe-manual-commit.yml"
response = requests.get(url)
print(response.text)

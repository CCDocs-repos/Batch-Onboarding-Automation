"""
BambooHR Reports To Extractor
Saves results to reports_to.json
Author: CC Docs Assistant
"""

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import os
import sys
import json

# ============
# LOAD ENV VARIABLES
# ============

load_dotenv()
API_KEY = os.getenv("BAMBOOHR_API_KEY")
SUBDOMAIN = os.getenv("BAMBOOHR_SUBDOMAIN")

if not API_KEY or not SUBDOMAIN:
    print("❌ Missing BAMBOOHR_API_KEY or BAMBOOHR_SUBDOMAIN in .env")
    sys.exit(1)

# ============
# FUNCTION
# ============

def get_reports_to():
    url = f"https://api.bamboohr.com/api/gateway.php/{SUBDOMAIN}/v1/employees/directory"
    print(f"📌 Requesting URL: {url}")

    try:
        response = requests.get(
            url,
            headers={"Accept": "application/json"},
            auth=HTTPBasicAuth(API_KEY, "")
        )

        if response.status_code == 200:
            data = response.json()
            employees = data.get("employees", [])

            # Build ID to name map
            id_name_map = {emp["id"]: emp.get("displayName") for emp in employees}

            results = []

            print("\n✅ Employee -> Reports To:")
            for emp in employees:
                emp_name = emp.get("displayName")
                emp_id = emp.get("id")
                supervisor_id = emp.get("supervisorId")

                if supervisor_id:
                    supervisor_name = id_name_map.get(supervisor_id, "Unknown")
                    print(f"{emp_name} (ID: {emp_id}) -> {supervisor_name} (ID: {supervisor_id})")
                else:
                    supervisor_name = None
                    print(f"{emp_name} (ID: {emp_id}) -> No supervisor")

                results.append({
                    "employee_id": emp_id,
                    "employee_name": emp_name,
                    "supervisor_id": supervisor_id,
                    "supervisor_name": supervisor_name
                })

            # Write results to JSON file
            with open("reports_to.json", "w") as f:
                json.dump(results, f, indent=4)

            print("\n✅ Saved reports_to.json successfully!")

        else:
            print(f"❌ Error: HTTP {response.status_code}")
            print(response.text)
            sys.exit(1)

    except Exception as e:
        print(f"❌ Exception: {str(e)}")
        sys.exit(1)

# ============
# ENTRY POINT
# ============

if __name__ == "__main__":
    print("🔗 Connecting to BambooHR...")
    get_reports_to()

"""
BambooHR Location Extractor
Saves results to locations.json
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

def get_locations():
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

            # Extract unique locations
            locations = set()
            for emp in employees:
                location = emp.get("location")
                if location:
                    locations.add(location)

            results = list(locations)

            print("\n✅ Unique Locations Found:")
            for location in results:
                print(f"📍 {location}")

            # Write to JSON file
            with open("locations.json", "w") as f:
                json.dump(results, f, indent=4)

            print("\n✅ Saved locations.json successfully!")

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
    get_locations()

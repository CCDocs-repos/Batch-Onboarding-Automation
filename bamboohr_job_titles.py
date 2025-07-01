"""
BambooHR Job Titles Extractor (with .env support)
Author: CC Docs Assistant
"""

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import os
import sys

# ============
# LOAD ENV VARIABLES
# ============

# 1. Load from .env file
load_dotenv()

# 2. Read credentials from env
API_KEY = os.getenv("BAMBOOHR_API_KEY")
SUBDOMAIN = os.getenv("BAMBOOHR_SUBDOMAIN")

if not API_KEY or not SUBDOMAIN:
    print("❌ Missing BAMBOOHR_API_KEY or BAMBOOHR_SUBDOMAIN in your .env file")
    sys.exit(1)

# ============
# FUNCTION
# ============

def get_unique_job_titles():
    # 1. Build URL
    url = f"https://api.bamboohr.com/api/gateway.php/{SUBDOMAIN}/v1/employees/directory"
    print(f"📌 Requesting URL: {url}")

    # 2. Make GET request
    try:
        response = requests.get(
            url,
            headers={
                "Accept": "application/json"
            },
            auth=HTTPBasicAuth(API_KEY, "")
        )

        if response.status_code == 200:
            data = response.json()
            employees = data.get("employees", [])

            job_titles = set()
            for emp in employees:
                job_title = emp.get("jobTitle")
                if job_title and job_title.strip():
                    job_titles.add(job_title.strip())

            print("\n✅ Unique Job Titles:")
            for title in sorted(job_titles):
                print(f"- {title}")

            print(f"\nTotal unique titles found: {len(job_titles)}")

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
    get_unique_job_titles()

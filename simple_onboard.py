#!/usr/bin/env python3
"""
Simple Employee Onboarding Script
"""

import os
import json
import time
import logging
import requests
import base64
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
from google.oauth2 import service_account
from googleapiclient.discovery import build
from slack_sdk import WebClient
import datetime

# ──────────────── ENV SETUP ────────────────
script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(script_dir, '.env'))
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# ──────────────── LOGGING ────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
file_handler = logging.FileHandler(f"onboarding_{timestamp}.log", encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
logger.addHandler(file_handler)

logger.info("=== SIMPLE ONBOARDING SCRIPT START ===")

# ──────────────── CONFIG ────────────────
BAMBOO_SUB = os.getenv("BAMBOOHR_SUBDOMAIN")
BAMBOO_KEY = os.getenv("BAMBOOHR_API_KEY")
WEBWORK_URL = os.getenv("WEBWORK_URL")
WEBWORK_USERNAME = os.getenv("WEBWORK_USERNAME")
WEBWORK_PASSWORD = os.getenv("WEBWORK_PASSWORD")
SHEET_ID = os.getenv("SHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "Sheet1")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL")
SERVICE_ACCOUNT_FILE = os.path.join(script_dir, "SERVICE_ACCOUNT_FILE .json")

# ──────────────── HELPERS ────────────────
def setup_google_sheets():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds).spreadsheets()

def setup_slack_client():
    return WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None

def find_employee_by_email(email):
    logger.info(f"Searching for employee with email: {email}")
    url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUB}/v1/employees/directory"
    auth = HTTPBasicAuth(BAMBOO_KEY, 'x')
    headers = {"Accept": "application/json"}
    resp = requests.get(url, auth=auth, headers=headers)
    if resp.ok:
        for emp in resp.json().get("employees", []):
            work_email = emp.get("workEmail")
            if work_email and work_email.lower() == email.lower():
                eid = emp.get("id")
                logger.info(f"✅ Found existing employee ID: {eid} for {email}")
                return eid
        logger.info(f"❌ No employee found for {email}")
    else:
        logger.error(f"Directory lookup failed: {resp.status_code}")
    return None


def create_employee(emp):
    logger.info(f"Creating new employee: {emp['First Name']} {emp['Last Name']}")
    url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUB}/v1/employees/"
    auth = HTTPBasicAuth(BAMBOO_KEY, 'x')
    payload = {
        "firstName": emp["First Name"].strip(),
        "lastName": emp["Last Name"].strip(),
        "workEmail": emp["Email"],
        "hireDate": emp["Start Date"],
        "jobTitle": emp.get("Job Title", emp.get("Position", "")),
        "department": emp.get("Department", ""),
        "location": emp.get("Location", "")
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    resp = requests.post(url, auth=auth, json=payload, headers=headers)
    if resp.status_code == 201:
        eid = resp.headers.get("Location", "").split("/")[-1]
        logger.info(f"✅ Created employee ID: {eid}")
        return eid
    else:
        logger.error(f"❌ Create failed: {resp.status_code} - {resp.text}")
        return None

def update_employee_info(eid, emp):
    logger.info(f"Updating employee {eid} information")
    url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUB}/v1/employees/{eid}"
    auth = HTTPBasicAuth(BAMBOO_KEY, 'x')
    payload = {
        "jobTitle": emp.get("Job Title", emp.get("Position", "")),
        "department": emp.get("Department", ""),
        "location": emp.get("Location", "")
    }
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, auth=auth, json=payload, headers=headers)
    if resp.ok:
        logger.info(f"✅ Updated employee {eid}")
        return True
    else:
        logger.error(f"❌ Update failed: {resp.status_code} - {resp.text}")
        return False

def add_compensation(eid, emp):
    try:
        logger.info(f"Adding compensation for employee {eid}")
        base_url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUB}/v1/employees/{eid}/tables/compensation/"
        auth = HTTPBasicAuth(BAMBOO_KEY, 'x')
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        pay_rate = emp.get("Pay Rate", "").strip().replace('$', '').replace(',', '')
        if not pay_rate:
            logger.warning("No pay rate. Skipping.")
            return True

        payload = {
            "payRate": pay_rate,
            "payPer": "Hourly",
            "currency": "USD",
            "effectiveDate": emp.get("Start Date", "")
        }

        logger.info(f"Compensation payload: {json.dumps(payload, indent=2)}")
        get_resp = requests.get(base_url, auth=auth, headers=headers)
        logger.warning(f"GET raw text: {get_resp.text}")

        try:
            rows = get_resp.json().get("employees", [])
        except Exception:
            logger.warning("Could not parse GET response as JSON. Will POST instead.")
            rows = []

        for row in rows:
            if row.get("effectiveDate") == payload["effectiveDate"]:
                row_id = row.get("id")
                put_resp = requests.put(f"{base_url}{row_id}", json=payload, auth=auth, headers=headers)
                if put_resp.ok:
                    logger.info(f"✅ Updated compensation row {row_id}")
                    return True
                else:
                    logger.error(f"❌ PUT failed: {put_resp.status_code} - {put_resp.text}")
                    return False

        post_resp = requests.post(base_url, json=payload, auth=auth, headers=headers)
        if post_resp.ok:
            logger.info(f"✅ Created compensation for employee {eid}")
            return True
        else:
            logger.error(f"❌ POST failed: {post_resp.status_code} - {post_resp.text}")
            return False

    except Exception as e:
        logger.error(f"Error in add_compensation: {e}")
        return False

def provision_self_service(eid, email):
    logger.info(f"Provisioning self-service for employee {eid}")
    url = f"https://api.bamboohr.com/api/gateway.php/{BAMBOO_SUB}/v1/meta/users"
    auth = HTTPBasicAuth(BAMBOO_KEY, 'x')
    payload = {"employeeId": int(eid), "accessLevel": "Employee Self-Service", "email": email}
    resp = requests.post(url, json=payload, auth=auth)
    if resp.status_code == 200:
        logger.info(f"✅ Provisioned self-service for {eid}")
        return True
    elif resp.status_code == 404:
        logger.warning(f"Self-service endpoint not available. Skipping for {eid}")
        return True
    else:
        logger.error(f"❌ Self-service failed: {resp.status_code} - {resp.text}")
        return False

def create_webwork_account(emp):
    logger.info(f"Creating WebWork account for {emp['Email']}")
    headers = {
        "Authorization": f"Basic {base64.b64encode(f'{WEBWORK_USERNAME}:{WEBWORK_PASSWORD}'.encode()).decode()}",
        "Content-Type": "application/json"
    }
    payload = {
        "email": emp["Email"],
        "firstname": emp["First Name"],
        "lastname": emp["Last Name"],
        "position": emp.get("Job Title", emp.get("Position", "")),
        "role": 30,
        "team": "New Joiners - Onboarding Team"
    }
    resp = requests.post(WEBWORK_URL, json=payload, headers=headers)
    if resp.ok:
        logger.info(f"✅ Created WebWork account for {emp['Email']}")
        return True
    else:
        logger.error(f"❌ WebWork failed: {resp.status_code} - {resp.text}")
        return False

def write_back_to_sheet(sheets, row_index, status, notes):
    logger.info(f"Updating sheet row {row_index}: {status} - {notes}")
    sheets.values().update(
        spreadsheetId=SHEET_ID, range=f"{SHEET_NAME}!P{row_index}",
        valueInputOption="RAW", body={"values": [[status]]}
    ).execute()
    sheets.values().update(
        spreadsheetId=SHEET_ID, range=f"{SHEET_NAME}!O{row_index}",
        valueInputOption="RAW", body={"values": [[notes]]}
    ).execute()
    logger.info(f"✅ Updated row {row_index}")

def send_slack_notification(slack, msg):
    if slack and SLACK_CHANNEL:
        slack.chat_postMessage(channel=SLACK_CHANNEL, text=msg)
        logger.info(f"✅ Sent Slack: {msg}")

def process_employee(sheets, row, row_index):
    emp = {
        "First Name": row[0],
        "Last Name": row[1],
        "Email": row[2],
        "Start Date": row[3],
        "Job Title": row[7],
        "Department": row[9],
        "Location": row[11],
        "Pay Rate": row[12],
    }
    eid = find_employee_by_email(emp['Email'])
    if not eid:
        eid = create_employee(emp)
        if not eid:
            write_back_to_sheet(sheets, row_index, "FAILED", "Create failed")
            return False
    ok1 = update_employee_info(eid, emp)
    ok2 = add_compensation(eid, emp)
    ok3 = provision_self_service(eid, emp["Email"])
    ok4 = create_webwork_account(emp)
    all_ok = all([ok1, ok2, ok3, ok4])
    notes = []
    if not ok1: notes.append("Update failed")
    if not ok2: notes.append("Compensation failed")
    if not ok3: notes.append("Self-service failed")
    if not ok4: notes.append("WebWork failed")
    write_back_to_sheet(sheets, row_index, "SUCCESS" if all_ok else "FAILED", "; ".join(notes) or "OK")
    return all_ok

def main():
    sheets = setup_google_sheets()
    slack = setup_slack_client()
    logger.info("Reading pending rows...")
    rows = sheets.values().get(spreadsheetId=SHEET_ID, range=f"{SHEET_NAME}!A2:P").execute().get("values", [])
    successes, failures = 0, 0
    for i, row in enumerate(rows, start=2):
        if len(row) > 15 and row[15]: continue
        if not row or len(row) < 3:
            write_back_to_sheet(sheets, i, "FAILED", "Missing fields")
            continue
        if process_employee(sheets, row, i): successes += 1
        else: failures += 1
        time.sleep(1)
    msg = f"Onboarding complete: {successes} succeeded, {failures} failed"
    logger.info(msg)
    send_slack_notification(slack, msg)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Unhandled: {e}")

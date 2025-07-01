#!/usr/bin/env python3
"""
Employee Onboarding Automation Script

This script automates the new-hire onboarding process by:
1. Reading pending hires from a Google Sheet
2. Sending personalized offer letters via BambooHR's e-signature API
3. Creating user accounts in WebWork time tracking system
4. Updating the status and notes in the Google Sheet
5. Sending a summary notification to Slack

Environment Variables Required:
- BAMBOOHR_SUBDOMAIN: Your BambooHR subdomain (e.g., "ccdocs")
- BAMBOOHR_API_KEY: Your BambooHR API key
- BAMBOOHR_TEMPLATE_ID: The template ID for the offer letter
- WEBWORK_URL: The WebWork API URL (should be https://www.webwork-tracker.com/rest-api/users)
- WEBWORK_USERNAME: The WebWork admin username
- WEBWORK_PASSWORD: The WebWork admin password
- SHEET_ID: The Google Sheet ID (from the URL)
- SHEET_NAME: The name of the sheet tab
- SLACK_BOT_TOKEN: Your Slack Bot token
- SLACK_CHANNEL: The Slack channel to post notifications (e.g., "#hr-alerts")
"""

import os
import time
import logging
import requests
import base64
from google.oauth2 import service_account
from googleapiclient.discovery import build
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import json
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

# Find the absolute path of the directory containing the script
script_dir = os.path.dirname(os.path.abspath(__file__))
# Construct the path to the .env file
dotenv_path = os.path.join(script_dir, '.env')
# Load environment variables from .env file
load_dotenv(dotenv_path=dotenv_path)

# Selenium and 2FA imports for automated login
import pyotp
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ─── Configure Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ─── Config from ENV ──────────────────────────────────────────────────────────
BAMBOO_SUB   = os.getenv("BAMBOOHR_SUBDOMAIN", "ccdocs")  # Default to "ccdocs" as specified
BAMBOO_KEY   = os.getenv("BAMBOOHR_API_KEY", "d15339ce41287e33908e18cb480115b0cc935d9b")  # Use the provided key as default
TEMPLATE_ID  = os.getenv("BAMBOOHR_TEMPLATE_ID")

WEBWORK_URL  = os.getenv("WEBWORK_URL", "https://www.webwork-tracker.com/rest-api/users")
WEBWORK_USERNAME = os.getenv("WEBWORK_USERNAME")  # Admin username
WEBWORK_PASSWORD = os.getenv("WEBWORK_PASSWORD")  # Admin password

SHEET_ID     = os.getenv("SHEET_ID", "1SU_GoWTxY0eBiWA-FGRjC0yEeMHQYLGWpCkHseXCZBA")
SHEET_NAME   = os.getenv("SHEET_NAME", "Hires")  # Updated to match the actual sheet name

SLACK_BOT_TOKEN  = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL    = os.getenv("SLACK_CHANNEL", "#hr-alerts")

# Path to the service account JSON file
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SERVICE_ACCOUNT_FILE .json")

# ─── Google Sheets Setup ─────────────────────────────────────────────────────
def setup_google_sheets():
    """Initialize Google Sheets API client."""
    try:
        SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        return build("sheets", "v4", credentials=creds).spreadsheets()
    except Exception as e:
        logger.error(f"Failed to setup Google Sheets API: {str(e)}")
        raise

# ─── Slack Client (for summary) ───────────────────────────────────────────────
def setup_slack_client():
    """Initialize Slack client."""
    if not SLACK_BOT_TOKEN:
        logger.warning("SLACK_BOT_TOKEN not set. Slack notifications will be disabled.")
        return None
    return WebClient(token=SLACK_BOT_TOKEN)

# ─── Helper Functions ─────────────────────────────────────────────────────────

def read_pending_rows(sheets):
    """Fetch rows where Overall status column is blank."""
    try:
        logger.info(f"Reading data from sheet: {SHEET_ID}, tab: {SHEET_NAME}")
        resp = sheets.values().get(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_NAME}!A1:P"  # Include all columns up to P
        ).execute()
        rows = resp.get("values", [])
        
        if not rows:
            logger.warning("No data found in the sheet.")
            return [], []
            
        headers = rows[0]
        # Clean up header names by stripping whitespace
        clean_headers = [h.strip() if isinstance(h, str) else h for h in headers]
        
        data = rows[1:]
        pending = []
        
        logger.info(f"Found {len(data)} total rows, checking for pending hires...")
        
        for i, row in enumerate(data, start=2):
            # Extend row to match headers length
            row += [""] * (len(headers) - len(row))
            
            # Create dictionary with both original and cleaned headers
            row_dict = dict(zip(headers, row))
            clean_row_dict = dict(zip(clean_headers, row))
            
            # Merge the dictionaries, preferring original headers
            merged_dict = {**clean_row_dict, **row_dict}
            
            # Check if this row is pending (Overall status is empty)
            status_value = merged_dict.get("Overall status", "").strip()
            if status_value == "":
                # Convert date format if needed
                if "Start Date" in merged_dict:
                    date_str = merged_dict["Start Date"].strip()
                    # Try to convert MM/DD/YY to YYYY-MM-DD
                    try:
                        from datetime import datetime
                        dt = datetime.strptime(date_str, "%m/%d/%y")
                        merged_dict["Start Date"] = dt.strftime("%Y-%m-%d")
                    except:
                        # If conversion fails, keep original
                        pass
                        
                pending.append((i, merged_dict))
                
        logger.info(f"Found {len(pending)} pending hires to process")
        return headers, pending
    except Exception as e:
        logger.error(f"Error reading from Google Sheet: {str(e)}")
        raise

def write_back(sheets, row_index, status, notes):
    """Update Overall status & Notes for a specific row."""
    try:
        logger.info(f"Updating row {row_index} with status: {status}, notes: {notes}")
        
        # First, get the current headers to find the correct columns
        resp = sheets.values().get(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_NAME}!A1:P1"  # Get header row
        ).execute()
        
        headers = resp.get("values", [[]])[0]
        
        # Find the column indices for "Overall status" and "Notes"
        status_col = None
        notes_col = None
        
        for i, header in enumerate(headers):
            if header.strip() == "Overall status":
                status_col = i
            if header.strip() == "Notes":
                notes_col = i
        
        if status_col is None or notes_col is None:
            logger.error("Could not find 'Overall status' or 'Notes' columns in the sheet")
            return
            
        # Convert column indices to A1 notation
        status_col_letter = chr(65 + status_col)  # A=65 in ASCII
        notes_col_letter = chr(65 + notes_col)
        
        # Update status column
        sheets.values().update(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_NAME}!{status_col_letter}{row_index}",
            valueInputOption="RAW",
            body={"values": [[status]]}
        ).execute()
        
        # Update notes column
        sheets.values().update(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_NAME}!{notes_col_letter}{row_index}",
            valueInputOption="RAW",
            body={"values": [[notes]]}
        ).execute()
        
        logger.info(f"Row {row_index} updated successfully")
    except Exception as e:
        logger.error(f"Error updating Google Sheet: {str(e)}")
        # Continue processing other rows even if one update fails

def prepare_contract_data(employee):
    """
    Prepare contract data for BambooHR signature request.
    Maps employee data from Google Sheet to contract fields.
    """
    logger.info(f"Preparing contract data for {employee.get('First Name', '')} {employee.get('Last Name', '')}")
    
    # Get current date for the contract
    today = time.strftime("%B %d, %Y")  # Format: June 27, 2025
    
    # Map employee data to contract fields
    contract_data = {
        # Page 1: Contractor's Name
        "contractorName": f"{employee.get('First Name', '')} {employee.get('Last Name', '')}",
        
        # Page 8: Employee Signature Block
        "employeeName": f"{employee.get('First Name', '')} {employee.get('Last Name', '')}",
        "signatureDate": today,
        
        # Additional fields that might be in the contract
        "position": employee.get("Position", ""),
        "startDate": employee.get("Start Date", ""),
        "salary": employee.get("Salary", ""),
        "email": employee.get("Email", ""),
    }
    
    logger.info(f"Contract data prepared: {contract_data}")
    return contract_data

def load_bamboo_headers():
    """Load saved authentication headers from bamboo_headers.json"""
    headers_file = "bamboo_headers.json"
    try:
        with open(headers_file, "r") as f:
            headers = json.load(f)
        logger.info(f"Successfully loaded authentication headers from {headers_file}")
        return headers
    except FileNotFoundError:
        logger.error(f"{headers_file} not found. Please run bamboo_auth.py first to generate it.")
        return None
    except Exception as e:
        logger.error(f"Error loading {headers_file}: {str(e)}")
        return None

def send_bamboo_signature_request(employee, auth_headers):
    """Send signature request via BambooHR's AJAX endpoint."""
    try:
        logger.info(f"Sending BambooHR signature request to {employee['Email']}")
        
        # Get employee ID - might be in different formats in the sheet
        employee_id = employee.get('id') or employee.get('ID') or employee.get('Employee ID')
        
        if not employee_id:
            logger.error(f"Missing employee ID for {employee['Email']}")
            return 400, "Missing employee ID"
        
        # Construct URL with employee ID
        url = f"https://ccdocs.bamboohr.com/ajax/files/send_signature_request.php?esignatureTemplateId=319&employeeId={employee_id}"
        logger.info(f"Request URL: {url}")
        
        # Make authenticated request
        response = requests.get(url, headers=auth_headers)
        
        if response.status_code == 200:
            logger.info(f"BambooHR signature request sent successfully to {employee['Email']}")
            return 200, "Signature request sent successfully"
        else:
            logger.error(f"BambooHR API error: {response.status_code} - {response.text}")
            return response.status_code, response.text
            
    except Exception as e:
        logger.error(f"Error sending BambooHR signature request: {str(e)}")
        return 500, str(e)

def add_user_to_team(email, team_name, auth_headers):
    """Add an existing user to a team."""
    try:
        logger.info(f"Adding user {email} to team {team_name}")
        
        # Step 1: Find the user by email
        resp = requests.get(f"https://www.webwork-tracker.com/rest-api/users", headers=auth_headers)
        resp.raise_for_status()
        
        users = resp.json()
        user = None
        for u in users:
            if u.get("email") == email:
                user = u
                break
        
        if not user:
            logger.error(f"User with email {email} not found")
            return False
        
        user_id = user.get("id")
        logger.info(f"Found user with ID: {user_id}")
        
        # Step 2: Add user to team
        # Note: This is a guess at the API endpoint - may need adjustment
        payload = {
            "user_id": user_id,
            "team": team_name
        }
        
        resp = requests.post(f"https://www.webwork-tracker.com/rest-api/users/teams", 
                            json=payload, headers=auth_headers)
        
        if resp.status_code == 200:
            logger.info(f"Successfully added user to team {team_name}")
            return True
        else:
            logger.error(f"Failed to add user to team. Status code: {resp.status_code}")
            logger.error(f"Response: {resp.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error adding user to team: {str(e)}")
        return False

def invite_webwork(employee):
    """Invite employee to WebWork time tracking system."""
    if not all([WEBWORK_URL, WEBWORK_USERNAME, WEBWORK_PASSWORD]):
        logger.error("Missing WebWork configuration. Check environment variables.")
        return 500, "Missing WebWork configuration"
        
    headers = {
        "Authorization": f"Basic {base64.b64encode(f'{WEBWORK_USERNAME}:{WEBWORK_PASSWORD}'.encode()).decode()}",
        "Content-Type": "application/json"
    }
    
    try:
        logger.info(f"Creating WebWork account for {employee['Email']}")
        
        # First try with teams array
        payload = {
            "email":      employee["Email"],
            "firstname": employee["First Name"],
            "lastname":  employee["Last Name"],
            "position":   employee["Position"],
            "role":       30,
            "teams":      ["New Joiners - Onboarding Team", "AGENTS"],  # Try with array first
            "project":    "Training"  # Assign Training project by default
        }
        
        r = requests.post(WEBWORK_URL, json=payload, headers=headers)
        
        # The WebWork API returns a 200 OK even for failures,
        # so we need to check the JSON response body.
        response_json = r.json()
        if response_json.get("success") is False:
            # If teams array failed, try with single team
            logger.warning(f"Failed with teams array. Trying with single team.")
            
            payload = {
                "email":      employee["Email"],
                "firstname": employee["First Name"],
                "lastname":  employee["Last Name"],
                "position":   employee["Position"],
                "role":       30,
                "team":       "New Joiners - Onboarding Team",  # Primary team
                "project":    "Training"  # Assign Training project by default
            }
            
            r = requests.post(WEBWORK_URL, json=payload, headers=headers)
            response_json = r.json()
            
            if response_json.get("success") is False:
                # It's still an error
                error_messages = response_json.get("message", ["Unknown WebWork error."])
                error_text = "; ".join(error_messages)
                logger.error(f"WebWork API error for {employee['Email']}: {error_text}")
                return 500, error_text
            else:
                # Success with single team, now add to second team
                logger.info(f"WebWork account created successfully with primary team. Adding to second team.")
                if add_user_to_team(employee["Email"], "AGENTS", headers):
                    logger.info(f"Successfully added user to AGENTS team")
                    return 200, "WebWork account created successfully and added to both teams."
                else:
                    logger.warning(f"Created account but failed to add to second team")
                    return 200, "WebWork account created but failed to add to second team."
        else:
            logger.info(f"WebWork account created successfully for {employee['Email']} with multiple teams")
            return 200, "WebWork account created successfully with multiple teams."
            
    except Exception as e:
        logger.error(f"Error creating WebWork account: {str(e)}")
        return 500, str(e)

def send_slack_notification(slack, message):
    """Send notification to Slack channel."""
    if not slack or not SLACK_CHANNEL:
        logger.warning("Slack notification skipped: client not configured")
        return False
        
    try:
        logger.info(f"Sending Slack notification to {SLACK_CHANNEL}")
        response = slack.chat_postMessage(channel=SLACK_CHANNEL, text=message)
        logger.info("Slack notification sent successfully")
        return True
    except SlackApiError as e:
        logger.error(f"Error sending Slack notification: {e.response['error']}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending Slack notification: {str(e)}")
        return False

# ─── Helpers for BambooHR REST API ──────────────────────────────────────────

def create_employee(subdomain, api_key, person):
    """
    1. POST /employees/ to create the record.
    Returns the new employeeId on success, or None+error text on failure.
    """
    url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/"
    
    # Extract supervisor ID from "Reports To" field if present
    supervisor_id = None
    reports_to = person.get("Reports To", "")
    if reports_to:
        # Extract ID from format like "Name (ID)"
        import re
        match = re.search(r'\((\d+)\)$', reports_to)
        if match:
            supervisor_id = match.group(1)
    
    # Build comprehensive payload with all available fields
    payload = {
        "firstName": person["First Name"],
        "lastName":  person["Last Name"],
        "workEmail": person["Email"],
        "hireDate":  person["Start Date"],
        "jobTitle":  person.get("Job Title", person.get("Position", "")),
        "department": person.get("Department", ""),
        "division": person.get("Division", ""),
        "location": person.get("Location", ""),
        "employmentStatus": person.get("Employment Status", "")
    }
    
    # Add supervisor if found
    if supervisor_id:
        payload["supervisor"] = supervisor_id
    
    # Remove any empty fields
    payload = {k: v for k, v in payload.items() if v}
    
    logger.info(f"Creating employee with data: {payload}")
    
    auth = HTTPBasicAuth(api_key, "x")
    for attempt in range(3):
        r = requests.post(url, json=payload, auth=auth)
        if r.ok:
            # bamboo returns new record in Location header: .../employees/{id}
            loc = r.headers.get("Location", "")
            eid = loc.rstrip("/").split("/")[-1]
            return eid, None
        time.sleep(2 ** attempt)
    return None, f"Create failed: {r.status_code} {r.text}"

def update_employee(subdomain, api_key, employee_id, person):
    """
    2. PUT /employees/{id} to populate job & personal fields.
    """
    url = f"https://api.bamboohr.com/api/v1/employees/{employee_id}"
    
    # Extract the supervisor ID from "Reports To" field if present
    supervisor_id = None
    reports_to = person.get("Reports To", "")
    if reports_to:
        # Extract ID from format like "Name (ID)"
        import re
        match = re.search(r'\((\d+)\)$', reports_to)
        if match:
            supervisor_id = match.group(1)
    
    # Build payload with all job information fields
    payload = {
        "jobTitle": person.get("Job Title", person.get("Position", "")),  # Try Job Title first, fall back to Position
        "department": person.get("Department", ""),
        "division": person.get("Division", ""),
        "location": person.get("Location", ""),
        "employmentStatus": person.get("Employment Status", ""),
    }
    
    # Add supervisor ID if found
    if supervisor_id:
        payload["supervisor"] = supervisor_id
    
    # Remove any empty fields
    payload = {k: v for k, v in payload.items() if v}
    
    logger.info(f"Updating employee {employee_id} with job information: {payload}")
    
    auth = HTTPBasicAuth(api_key, "x")
    for attempt in range(3):
        r = requests.put(url, json=payload, auth=auth)
        if r.ok:
            return True, None
        time.sleep(2 ** attempt)
    return False, f"Update failed: {r.status_code} {r.text}"

def add_compensation(subdomain, api_key, employee_id, person):
    """
    3. POST /employees/{id}/tables/compensation to set up pay.
    """
    url = f"https://api.bamboohr.com/api/v1/employees/{employee_id}/tables/compensation/"
    
    # Get values from sheet with fallbacks to default values
    pay_rate = person.get("Pay Rate", person.get("Salary", ""))
    pay_type = person.get("Pay Type", "Hourly")  # Default to Hourly if not specified
    pay_schedule = person.get("Pay Schedule", "Biweekly")  # Default to Biweekly if not specified
    
    # Determine payPer based on pay type
    pay_per = "Hour"
    if pay_type and pay_type.lower() in ["salary", "salaried"]:
        pay_per = "Year"
    
    payload = {
        "payRate": pay_rate,
        "payPer": pay_per,
        "currency": "USD",
        "payType": pay_type,
        "paySchedule": pay_schedule,
        "effectiveDate": person.get("Start Date", "")  # Use start date as effective date
    }
    
    # Remove any empty fields
    payload = {k: v for k, v in payload.items() if v}
    
    logger.info(f"Adding compensation for employee {employee_id}: {payload}")
    
    auth = HTTPBasicAuth(api_key, "x")
    for attempt in range(3):
        r = requests.post(url, json=payload, auth=auth)
        if r.ok:
            return True, None
        time.sleep(2 ** attempt)
    return False, f"Comp failed: {r.status_code} {r.text}"

def provision_self_service(subdomain, api_key, employee_id, person):
    """
    4. POST /meta/users to grant portal access.
    """
    url = f"https://api.bamboohr.com/api/v1/meta/users"
    payload = {
        "employeeId": int(employee_id),
        "accessLevel": "Employee Self-Service",
        "email": person["Email"]
    }
    auth = HTTPBasicAuth(api_key, "x")
    for attempt in range(3):
        r = requests.post(url, json=payload, auth=auth)
        if r.ok:
            return True, None
        time.sleep(2 ** attempt)
    return False, f"Provision failed: {r.status_code} {r.text}"

def send_new_hire_packet(employee_id, auth_headers):
    """
    Send a new hire packet to the employee via BambooHR.
    This function is called after an employee is created in BambooHR.
    """
    try:
        logger.info(f"Sending new hire packet to employee ID: {employee_id}")
        
        # Construct URL for sending new hire packet
        url = f"https://ccdocs.bamboohr.com/ajax/onboarding/sendPacket"
        
        # Prepare payload for the request
        payload = {
            "employeeId": employee_id,
            "sendWelcomeEmail": True
        }
        
        # Make authenticated request
        response = requests.post(url, json=payload, headers=auth_headers)
        
        if response.status_code == 200:
            logger.info(f"New hire packet sent successfully to employee ID: {employee_id}")
            return 200, "New hire packet sent successfully"
        else:
            logger.error(f"BambooHR API error when sending new hire packet: {response.status_code} - {response.text}")
            return response.status_code, response.text
            
    except Exception as e:
        logger.error(f"Error sending new hire packet: {str(e)}")
        return 500, str(e)

def find_candidate_by_email(subdomain, api_key, email):
    """
    Search for a candidate in BambooHR's applicant tracking system by email.
    Returns the candidate ID if found, None otherwise.
    """
    if not email:
        logger.error("Cannot search for candidate: email is missing")
        return None, "Email is missing"
        
    try:
        logger.info(f"Searching for candidate with email: {email}")
        
        # API endpoint for applicant tracking system
        url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/applicant_tracking/applications"
        
        # Add query parameter for email search
        params = {
            "email": email
        }
        
        # Make authenticated request
        auth = HTTPBasicAuth(api_key, "x")
        response = requests.get(url, params=params, auth=auth)
        
        if response.status_code == 200:
            applications = response.json()
            
            if applications and len(applications) > 0:
                # Found at least one application with this email
                candidate_id = applications[0].get("id")
                logger.info(f"Found candidate with ID: {candidate_id} for email: {email}")
                return candidate_id, None
            else:
                logger.info(f"No candidates found with email: {email}")
                return None, "No candidates found with this email"
        else:
            logger.error(f"Error searching for candidate: {response.status_code} - {response.text}")
            return None, f"API error: {response.status_code}"
            
    except Exception as e:
        logger.error(f"Exception while searching for candidate: {str(e)}")
        return None, str(e)

def hire_candidate(subdomain, api_key, candidate_id, employee_data):
    """
    Convert a candidate to an employee in BambooHR.
    """
    if not candidate_id:
        logger.error("Cannot hire candidate: candidate ID is missing")
        return None, "Candidate ID is missing"
        
    try:
        logger.info(f"Hiring candidate with ID: {candidate_id}")
        
        # API endpoint for hiring a candidate
        url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/applicant_tracking/applications/{candidate_id}/hire"
        
        # Extract supervisor ID from "Reports To" field if present
        supervisor_id = None
        reports_to = employee_data.get("Reports To", "")
        if reports_to:
            # Extract ID from format like "Name (ID)"
            import re
            match = re.search(r'\((\d+)\)$', reports_to)
            if match:
                supervisor_id = match.group(1)
        
        # Prepare payload with employee data
        payload = {
            "firstName": employee_data["First Name"],
            "lastName": employee_data["Last Name"],
            "jobTitle": employee_data.get("Job Title", employee_data.get("Position", "")),
            "department": employee_data.get("Department", ""),
            "division": employee_data.get("Division", ""),
            "location": employee_data.get("Location", ""),
            "hireDate": employee_data["Start Date"],
            "status": "active",
            "employmentStatus": employee_data.get("Employment Status", "")
        }
        
        # Add supervisor if found
        if supervisor_id:
            payload["supervisor"] = supervisor_id
        
        # Remove any empty fields
        payload = {k: v for k, v in payload.items() if v}
        
        logger.info(f"Hiring candidate with data: {payload}")
        
        # Make authenticated request
        auth = HTTPBasicAuth(api_key, "x")
        response = requests.post(url, json=payload, auth=auth)
        
        if response.status_code == 200 or response.status_code == 201:
            result = response.json()
            employee_id = result.get("employeeId")
            logger.info(f"Successfully hired candidate {candidate_id} as employee {employee_id}")
            return employee_id, None
        else:
            logger.error(f"Error hiring candidate: {response.status_code} - {response.text}")
            return None, f"API error: {response.status_code} - {response.text}"
            
    except Exception as e:
        logger.error(f"Exception while hiring candidate: {str(e)}")
        return None, str(e)

# ─── Main Logic ────────────────────────────────────────────────────────────────

class BambooHRManager:
    """
    Manages BambooHR authentication, session, and API calls.
    Includes logic to automatically refresh the session and look up employees by email.
    """
    def __init__(self, subdomain, api_key, username, password, totp_secret, template_id):
        self.subdomain = subdomain
        self.api_key = api_key
        self.username = username
        self.password = password
        self.totp_secret = totp_secret
        self.template_id = template_id
        self.headers_file = "bamboo_headers.json"
        self.headers = self._load_headers_from_file()
        self.directory_cache = None

    def _load_headers_from_file(self):
        """Loads session headers from the JSON file."""
        if not os.path.exists(self.headers_file):
            return None
        try:
            with open(self.headers_file, "r") as f:
                return json.load(f)
        except Exception:
            return None

    def _save_headers_to_file(self):
        """Saves session headers to the JSON file."""
        try:
            with open(self.headers_file, "w") as f:
                json.dump(self.headers, f)
            logger.info("Successfully saved new session headers.")
        except Exception as e:
            logger.error(f"Failed to save session headers: {e}")

    def _get_employee_directory(self):
        """
        Fetches the full employee directory using the BambooHR API
        and caches it for the duration of the script run.
        """
        if self.directory_cache:
            return self.directory_cache
        
        logger.info("Fetching employee directory from BambooHR API...")
        url = f"https://api.bamboohr.com/api/gateway.php/{self.subdomain}/v1/employees/directory"
        headers = {'Accept': 'application/json'}
        auth = (self.api_key, 'x')
        
        try:
            response = requests.get(url, headers=headers, auth=auth)
            response.raise_for_status() # Raises an exception for bad responses (4xx or 5xx)
            self.directory_cache = response.json()
            logger.info(f"Successfully fetched directory with {len(self.directory_cache.get('employees', []))} employees.")
            return self.directory_cache
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch employee directory: {e}")
            return None

    def get_employee_id_by_email(self, email):
        """Looks up an employee's ID by their email address."""
        if not email:
            return None, "Email address is missing."
        
        directory = self._get_employee_directory()
        if not directory or 'employees' not in directory:
            return None, "Could not retrieve employee directory."
            
        # Search for the employee by work email, case-insensitively
        for emp in directory['employees']:
            if emp.get('workEmail', '').lower() == email.lower():
                logger.info(f"Found employee ID {emp['id']} for email {email}.")
                return emp['id'], "ID found."

        logger.warning(f"Could not find an employee with email: {email}")
        return None, f"Employee with email {email} not found in BambooHR."

    def update_employee_record(self, employee_id, employee_data):
        """
        Updates an employee's record in BambooHR with data from the Google Sheet.
        This ensures the contract has the most up-to-date information.
        """
        if not employee_id:
            return False, "Cannot update record without an employee ID."

        logger.info(f"Updating BambooHR profile for employee ID: {employee_id}")
        url = f"https://api.bamboohr.com/api/gateway.php/{self.subdomain}/v1/employees/{employee_id}"
        auth = (self.api_key, 'x')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}

        # Map Google Sheet columns to BambooHR API field names
        # NOTE: You may need to adjust these field names to match your BambooHR setup.
        # You can find the correct field names in BambooHR Settings > Fields.
        update_payload = {
            "jobTitle": employee_data.get("Position"),
            "payRate": employee_data.get("Salary"),
            # Add other fields from your sheet that are in the contract
            # "department": employee_data.get("Department"), # Example
            # "division": employee_data.get("Division"), # Example
        }

        # Filter out any keys where the value is None
        update_payload = {k: v for k, v in update_payload.items() if v is not None}

        if not update_payload:
            logger.info("No new data from the sheet to update in BambooHR.")
            return True, "No data to update."

        try:
            response = requests.post(url, headers=headers, auth=auth, json=update_payload)
            response.raise_for_status()
            logger.info(f"Successfully updated profile for employee ID: {employee_id}")
            return True, "Employee profile updated."
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to update employee profile for ID {employee_id}: {e}")
            if e.response:
                logger.error(f"Response Body: {e.response.text}")
            return False, f"Failed to update profile: {e}"

    def _create_new_session(self):
        """
        Performs a full browser-based login to get new session headers.
        """
        logger.info("Creating a new BambooHR session via automated browser...")
        # (The full Selenium logic remains here, unchanged from the previous version)
        if not all([self.username, self.password, self.totp_secret]):
            logger.error("Missing BambooHR credentials for new session.")
            return False
        opts = Options()
        opts.headless = True
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        try:
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
            driver.get(f"https://{self.subdomain}.bamboohr.com/")
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "lemail"))).send_keys(self.username)
            driver.find_element(By.ID, "password").send_keys(self.password)
            driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
            time.sleep(3)
            if "multi_factor_authentication" in driver.current_url:
                totp = pyotp.TOTP(self.totp_secret)
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "oneTimeCode"))).send_keys(totp.now())
                WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='Continue']"))).click()
                time.sleep(3)
            if "trusted_browser" in driver.current_url:
                WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='Yes, Trust this Browser']"))).click()
                WebDriverWait(driver, 15).until(EC.url_contains("home"))
            cookies = driver.get_cookies()
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            self.headers = {
                "Cookie": cookie_str, "X-Requested-With": "XMLHttpRequest",
                "Referer": f"https://{self.subdomain}.bamboohr.com/files/", "Accept": "application/json, text/plain, */*",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
            }
            self._save_headers_to_file()
            logger.info("New BambooHR session created successfully.")
            return True
        except Exception as e:
            logger.error(f"An error occurred during automated login: {e}")
            driver.save_screenshot("bamboo_error.png")
            return False
        finally:
            if 'driver' in locals():
                driver.quit()

    def send_signature_request(self, employee):
        """
        Sends a signature request, finding and updating the user first.
        """
        employee_id, error_msg = self.get_employee_id_by_email(employee.get('Email'))
        if not employee_id:
            return 404, error_msg

        # New Step: Update the employee's profile before sending the contract
        update_success, update_message = self.update_employee_record(employee_id, employee)
        if not update_success:
            return 500, f"Update failed: {update_message}"
            
        # Prepare contract data with field mappings
        contract_data = prepare_contract_data(employee)
        logger.info(f"Using contract data: {contract_data}")

        # Now, proceed with sending the signature request
        if not self.headers:
            if not self._create_new_session():
                return 500, "Failed to create an initial BambooHR session."

        # Construct base URL for signature request
        base_url = f"https://{self.subdomain}.bamboohr.com/ajax/files/send_signature_request.php"
        
        # Add required parameters
        params = {
            "esignatureTemplateId": self.template_id,
            "employeeId": employee_id
        }
        
        # Add contract field mappings to parameters
        for field, value in contract_data.items():
            params[f"fields[{field}]"] = value
            
        # Construct full URL with parameters
        url = f"{base_url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
        logger.info(f"Sending signature request with mapped fields to employee ID {employee_id}...")
        
        # Make the request
        response = requests.get(url, headers=self.headers)
        
        if response.status_code in [401, 403]:
            logger.warning("BambooHR session expired. Re-authenticating...")
            if not self._create_new_session():
                return 500, "Failed to re-authenticate."
            response = requests.get(url, headers=self.headers)
            
        if response.status_code == 200:
            logger.info(f"Successfully sent signature request for employee ID {employee_id}")
            return 200, "Signature request sent successfully"
        else:
            return response.status_code, response.text.strip()

def main(test_mode=False):
    """
    Main execution function.
    
    Args:
        test_mode: If True, will process a single test employee instead of reading from Google Sheets
    """
    logger.info("--- SCRIPT START ---")
    
    bamboo_manager = BambooHRManager(
        subdomain=os.getenv("BAMBOOHR_SUBDOMAIN", "ccdocs"),
        api_key=os.getenv("BAMBOOHR_API_KEY"),
        username=os.getenv("BAMBOOHR_USERNAME"),
        password=os.getenv("BAMBOOHR_PASSWORD"),
        totp_secret=os.getenv("BAMBOOHR_TOTP_SECRET"),
        template_id=os.getenv("BAMBOOHR_TEMPLATE_ID", "319")
    )

    # Initialize API clients
    try:
        logger.info("Initializing Google Sheets...")
        sheets = setup_google_sheets()
        logger.info("Initializing Slack client...")
        slack = setup_slack_client()
        logger.info("All non-bamboo clients initialized.")

    except Exception as e:
        logger.critical(f"Failed to initialize API clients: {str(e)}")
        return
    
    # Test mode - process a single employee
    if test_mode:
        logger.info("--- RUNNING IN TEST MODE ---")
        
        # Create a test employee
        test_employee = {
            "ID": "4774",  # Use a valid employee ID from BambooHR
            "First Name": "Test",
            "Last Name": "User",
            "Email": "test@example.com",  # Use a real email you can check
            "Position": "Software Developer",
            "Job Title": "Software Developer",
            "Start Date": "2025-07-01",
            "Reports To": "Alberto Arellano Perez (601)",  # Format: Name (ID)
            "Department": "Engineering",
            "Division": "Technology",
            "Location": "Remote",
            "Pay Type": "Salary",
            "Pay Rate": "75000",
            "Pay Schedule": "Biweekly",
            "Employment Status": "Full-Time",
            "Onboarding Tier": "Standard",
            "Status": ""
        }
        
        # Process test employee
        logger.info(f"Processing test employee: {test_employee['First Name']} {test_employee['Last Name']}")
        
        # ── Check if candidate already exists in BambooHR ────────────────────────
        candidate_id, candidate_err = find_candidate_by_email(BAMBOO_SUB, BAMBOO_KEY, test_employee.get("Email"))
        
        if candidate_id:
            # Candidate exists, hire them directly
            logger.info(f"Found existing candidate with ID {candidate_id} for {test_employee['Email']}")
            eid, err = hire_candidate(BAMBOO_SUB, BAMBOO_KEY, candidate_id, test_employee)
        else:
            # No candidate found, create a new employee directly
            logger.info(f"No existing candidate found for {test_employee['Email']}, creating new employee")
            eid, err = create_employee(BAMBOO_SUB, BAMBOO_KEY, test_employee)
            
        if err:
            logger.error(f"Create/Hire Error: {err}")
        else:
            # Update the test_employee with the new ID
            test_employee["ID"] = eid
            
            ok, err = update_employee(BAMBOO_SUB, BAMBOO_KEY, eid, test_employee)
            if not ok:
                logger.error(f"Update Error: {err}")
            else:
                ok, err = add_compensation(BAMBOO_SUB, BAMBOO_KEY, eid, test_employee)
                if not ok:
                    logger.error(f"Comp Error: {err}")
                else:
                    ok, err = provision_self_service(BAMBOO_SUB, BAMBOO_KEY, eid, test_employee)
                    if not ok:
                        logger.error(f"Provision Error: {err}")
                    else:
                        # ── Existing signature + WebWork steps ───────────────────────────────────
                        b_code, b_resp = bamboo_manager.send_signature_request(test_employee)
                        logger.info(f"BambooHR signature request result: {b_code} - {b_resp}")
                        
                        # Send new hire packet by default
                        logger.info("Sending new hire packet in test mode...")
                        packet_code, packet_resp = send_new_hire_packet(eid, bamboo_manager.headers)
                        logger.info(f"New hire packet result: {packet_code} - {packet_resp}")
                        
                        # Create WebWork account
                        w_code, w_resp = invite_webwork(test_employee)
                        logger.info(f"WebWork account creation result: {w_code} - {w_resp}")
        
        # Send test notification to Slack
        if slack:
            logger.info("Sending test Slack notification...")
            send_slack_notification(slack, "Test onboarding completed")
        
        logger.info("--- TEST MODE COMPLETE ---")
        return
    
    logger.info("--- RUNNING IN PRODUCTION MODE ---")
    # Read pending rows from Google Sheet
    try:
        logger.info("Reading pending rows from Google Sheet...")
        headers, pending = read_pending_rows(sheets)
        if not pending:
            message = "✅ No new hires to process."
            logger.info(message)
            if slack:
                send_slack_notification(slack, message)
            return
    except Exception as e:
        logger.critical(f"Failed to read pending rows: {str(e)}")
        if slack:
            send_slack_notification(slack, f"❌ Onboarding automation failed: {str(e)}")
        return

    # Process each pending hire
    successes, failures = 0, 0

    for row_index, emp in pending:
        logger.info(f"Processing hire: {emp['First Name']} {emp['Last Name']}")
        
        # ── Check if candidate already exists in BambooHR ────────────────────────
        candidate_id, candidate_err = find_candidate_by_email(BAMBOO_SUB, BAMBOO_KEY, emp.get("Email"))
        
        if candidate_id:
            # Candidate exists, hire them directly
            logger.info(f"Found existing candidate with ID {candidate_id} for {emp['Email']}")
            eid, err = hire_candidate(BAMBOO_SUB, BAMBOO_KEY, candidate_id, emp)
        else:
            # No candidate found, create a new employee directly
            logger.info(f"No existing candidate found for {emp['Email']}, creating new employee")
            eid, err = create_employee(BAMBOO_SUB, BAMBOO_KEY, emp)
            
        if err:
            notes = f"Create/Hire Error: {err}"
            write_back(sheets, row_index, "❌", notes)
            failures += 1
            continue

        ok, err = update_employee(BAMBOO_SUB, BAMBOO_KEY, eid, emp)
        if not ok:
            notes = f"Update Error: {err}"
            write_back(sheets, row_index, "❌", notes)
            failures += 1
            continue

        ok, err = add_compensation(BAMBOO_SUB, BAMBOO_KEY, eid, emp)
        if not ok:
            notes = f"Comp Error: {err}"
            write_back(sheets, row_index, "❌", notes)
            failures += 1
            continue

        ok, err = provision_self_service(BAMBOO_SUB, BAMBOO_KEY, eid, emp)
        if not ok:
            notes = f"Provision Error: {err}"
            write_back(sheets, row_index, "❌", notes)
            failures += 1
            continue
            
        # Update the emp dictionary with the new ID
        emp["ID"] = eid

        # ── Existing signature + WebWork steps ───────────────────────────────────
        b_code, b_resp = bamboo_manager.send_signature_request(emp)
        
        # Throttle API calls
        time.sleep(1)
        
        # Send new hire packet by default for all new employees
        logger.info(f"Sending new hire packet for {emp['First Name']} {emp['Last Name']}")
        packet_code, packet_resp = send_new_hire_packet(eid, bamboo_manager.headers)
        if packet_code != 200:
            logger.warning(f"Failed to send new hire packet: {packet_resp}")
        
        # Create WebWork account
        w_code, w_resp = invite_webwork(emp)

        # Update status and notes
        notes = []
        if b_code != 200: notes.append(f"BambooHR error: {b_resp}")
        if w_code != 200: notes.append(f"WebWork error: {w_code}")
        if packet_code != 200: notes.append(f"New hire packet error: {packet_resp}")
        
        status = "✔️" if not notes else "❌"
        write_back(sheets, row_index, status, "; ".join(notes) or "OK")

        # Track statistics
        if status == "✔️":
            successes += 1
            logger.info(f"Successfully processed {emp['First Name']} {emp['Last Name']}")
        else:
            failures += 1
            logger.warning(f"Failed to process {emp['First Name']} {emp['Last Name']}: {'; '.join(notes)}")

        # Throttle before next hire
        time.sleep(1)

    # Send summary notification
    summary = f"Onboarding run complete: {successes} succeeded, {failures} failed."
    logger.info(summary)
    send_slack_notification(slack, summary)

if __name__ == "__main__":
    try:
        # Set to True to test with a single employee
        main(test_mode=True)
    except Exception as e:
        logger.critical(f"Unhandled exception: {str(e)}")
        try:
            slack = setup_slack_client()
            if slack:
                send_slack_notification(slack, f"❌ Onboarding automation critical error: {str(e)}")
        except:
            pass  # If we can't send to Slack, we've already logged the error
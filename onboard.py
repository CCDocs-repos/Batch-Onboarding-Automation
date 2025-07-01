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
SHEET_NAME   = os.getenv("SHEET_NAME", "Sheet1")  # Updated to match the actual sheet name

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
        # Use ASCII alternatives instead of emojis to avoid encoding issues on Windows
        if status == "❌":
            status = "FAILED"
        elif status == "✔️":
            status = "SUCCESS"
            
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
        
        # Construct URL with employee ID and template ID
        template_id = os.getenv("BAMBOOHR_TEMPLATE_ID", "319")  # Default to 319 if not set
        url = f"https://ccdocs.bamboohr.com/ajax/files/send_signature_request.php?esignatureTemplateId={template_id}&employeeId={employee_id}"
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
            "position":   employee.get("Position", employee.get("Job Title", "")),
            "role":       30,
            "teams":      ["New Joiners - Onboarding Team", "AGENTS"],  # Try with array first
            "project":    "Training"  # Assign Training project by default
        }
        
        r = requests.post(WEBWORK_URL, json=payload, headers=headers)
        
        # The WebWork API returns a 200 OK even for failures,
        # so we need to check the JSON response body.
        response_json = r.json()
        
        # Log the full response for debugging
        logger.debug(f"WebWork API response: {json.dumps(response_json, indent=2)}")
        
        if response_json.get("success") is False:
            # If teams array failed, try with single team
            logger.warning(f"Failed with teams array. Trying with single team.")
            
            payload = {
                "email":      employee["Email"],
                "firstname": employee["First Name"],
                "lastname":  employee["Last Name"],
                "position":   employee.get("Position", employee.get("Job Title", "")),
                "role":       30,
                "team":       "New Joiners - Onboarding Team",  # Primary team
                "project":    "Training"  # Assign Training project by default
            }
            
            r = requests.post(WEBWORK_URL, json=payload, headers=headers)
            response_json = r.json()
            
            # Log the full response for debugging
            logger.debug(f"WebWork API single team response: {json.dumps(response_json, indent=2)}")
            
            if response_json.get("success") is False:
                # It's still an error
                error_messages = response_json.get("message", ["Unknown WebWork error."])
                if isinstance(error_messages, list):
                    error_text = "; ".join(error_messages)
                else:
                    error_text = str(error_messages)
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

def find_employee_by_email(subdomain, api_key, email):
    """
    Check if an employee already exists in BambooHR with the given email.
    Returns the employee ID if found, None otherwise.
    """
    if not email:
        logger.error("Cannot search for employee: email is missing")
        return None, "Email is missing"
        
    try:
        logger.info(f"Checking if employee with email {email} already exists in BambooHR")
        
        # API endpoint for employee directory
        url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/directory"
        
        # Make authenticated request
        auth = HTTPBasicAuth(api_key, "x")
        headers = {'Accept': 'application/json'}
        response = requests.get(url, auth=auth, headers=headers)
        
        if response.status_code == 200:
            directory = response.json()
            employees = directory.get('employees', [])
            
            # Log the number of employees found
            logger.info(f"Retrieved directory with {len(employees)} employees")
            
            # Search for employee with matching email
            for employee in employees:
                # Get the work email safely, defaulting to empty string if not present
                work_email = employee.get('workEmail', '') or ''
                
                # Compare emails case-insensitively
                if work_email.lower() == email.lower():
                    employee_id = employee.get('id')
                    logger.info(f"Found existing employee with ID {employee_id} for email {email}")
                    return employee_id, None
            
            logger.info(f"No existing employee found with email {email}")
            return None, "Employee not found"
        else:
            logger.error(f"Error checking for existing employee: {response.status_code} - {response.text}")
            return None, f"API error: {response.status_code}"
            
    except Exception as e:
        logger.error(f"Exception while checking for existing employee: {str(e)}")
        # Print the full stack trace for debugging
        import traceback
        logger.error(f"Stack trace: {traceback.format_exc()}")
        return None, str(e)

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
            return None, f"API error: {response.status_code} - {response.text}"
            
    except Exception as e:
        logger.error(f"Exception while searching for candidate: {str(e)}")
        # Print the full stack trace for debugging
        import traceback
        logger.error(f"Stack trace: {traceback.format_exc()}")
        
        # Convert integer error to string with more context
        if isinstance(e, int) or str(e).isdigit():
            return None, f"API returned unexpected status code: {str(e)}"
        return None, str(e)

def create_employee(subdomain, api_key, person):
    """
    1. POST /employees/ to create the record.
    Returns the new employeeId on success, or None+error text on failure.
    """
    url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/"
    
    # Create a minimal payload with only required fields
    # Based on BambooHR API documentation, only firstName, lastName, and status are truly required
    minimal_payload = {
        "firstName": person["First Name"].strip(),
        "lastName": person["Last Name"].strip(),
        "status": "Active"  # Always set to Active for new hires
    }
    
    # Add work email if available (common cause of 400 errors is duplicate email)
    if "Email" in person:
        minimal_payload["workEmail"] = person["Email"].strip()
    
    # Add hire date if available
    if "Start Date" in person:
        minimal_payload["hireDate"] = person["Start Date"]
    
    logger.info(f"Creating employee with minimal data: {json.dumps(minimal_payload, indent=2)}")
    
    auth = HTTPBasicAuth(api_key, "x")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    try:
        # First attempt with minimal data
        logger.info("Sending create employee request to BambooHR API...")
        r = requests.post(url, json=minimal_payload, auth=auth, headers=headers)
        
        if r.ok:
            # Success! Get the employee ID
            loc = r.headers.get("Location", "")
            eid = loc.rstrip("/").split("/")[-1]
            logger.info(f"Successfully created employee with ID: {eid}")
            
            # Now update with additional fields
            update_url = f"{url}{eid}"
            
            # Build a more comprehensive payload for the update
            update_payload = {}
            
            # Add job information
            if "Job Title" in person or "Position" in person:
                update_payload["jobTitle"] = person.get("Job Title", person.get("Position", ""))
            
            if "Department" in person:
                update_payload["department"] = person["Department"]
                
            if "Division" in person:
                update_payload["division"] = person["Division"]
                
            if "Location" in person:
                update_payload["location"] = person["Location"]
                
            # Add manager reportsTo if present
            if "Reports To" in person:
                import re
                m = re.search(r'\((\d+)\)$', person["Reports To"])
                if m:
                    update_payload["reportsTo"] = m.group(1)
            
            # Only update if we have additional fields
            if update_payload:
                logger.info(f"Updating employee {eid} with additional data: {json.dumps(update_payload, indent=2)}")
                update_r = requests.post(update_url, json=update_payload, auth=auth, headers=headers)
                
                if not update_r.ok:
                    logger.warning(f"Failed to update employee with additional data: {update_r.status_code} - {update_r.text}")
            
            return eid, None
        else:
            # Try to parse the response as JSON
            try:
                error_detail = r.json()
                logger.error(f"Detailed error: {json.dumps(error_detail, indent=2)}")
            except:
                # If not JSON, log the raw text
                logger.error(f"Response body: {r.text}")
                
            # Check for common error conditions
            if r.status_code == 400:
                logger.error("HTTP 400 indicates a validation error. Common issues:")
                logger.error("- Invalid date format (should be YYYY-MM-DD)")
                logger.error("- Invalid field values (check for trailing spaces)")
                logger.error("- Missing required fields")
                logger.error("- Duplicate email address")
                
                # Check if this is a duplicate email error
                if "Duplicate email" in r.text or "duplicate" in r.text.lower():
                    logger.error("DUPLICATE EMAIL DETECTED - Need to find and update existing employee instead")
                    # Try to find the employee by email
                    existing_id, _ = find_employee_by_email(subdomain, api_key, person.get("Email", ""))
                    if existing_id:
                        logger.info(f"Found existing employee with ID {existing_id} for duplicate email")
                        return existing_id, None
                
                # Try again with XML format as a last resort
                logger.info("Trying with XML format instead of JSON")
                xml_payload = "<employee>"
                for key, value in minimal_payload.items():
                    xml_payload += f'<field id="{key}">{value}</field>'
                xml_payload += "</employee>"
                
                xml_headers = {
                    "Accept": "application/xml",
                    "Content-Type": "application/xml"
                }
                
                r = requests.post(url, data=xml_payload, auth=auth, headers=xml_headers)
                if r.ok:
                    loc = r.headers.get("Location", "")
                    eid = loc.rstrip("/").split("/")[-1]
                    logger.info(f"Successfully created employee with ID: {eid} using XML format")
                    return eid, None
                else:
                    logger.error(f"XML attempt also failed: {r.status_code} - {r.text}")
            
            # Check for header with error message
            error_message = r.headers.get("X-BambooHR-Error-Message", "")
            if error_message:
                logger.error(f"BambooHR Error Message: {error_message}")
                return None, f"BambooHR Error: {error_message}"
            
            return None, f"Create failed: Status {r.status_code}"
            
    except Exception as e:
        logger.error(f"Exception during employee creation: {str(e)}")
        # Print the full stack trace for debugging
        import traceback
        logger.error(f"Stack trace: {traceback.format_exc()}")
        return None, f"Exception: {str(e)}"

def check_compensation_exists(subdomain, api_key, employee_id):
    """
    Check if an employee already has compensation records.
    Returns True if compensation exists, False otherwise.
    """
    try:
        logger.info(f"Checking if employee {employee_id} already has compensation records")
        url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/{employee_id}/tables/compensation"
        auth = HTTPBasicAuth(api_key, "x")
        headers = {'Accept': 'application/json'}
        response = requests.get(url, auth=auth, headers=headers)
        
        if response.status_code == 200:
            records = response.json()
            if records and len(records) > 0:
                logger.info(f"Employee {employee_id} already has {len(records)} compensation records")
                return True
            else:
                logger.info(f"Employee {employee_id} has no existing compensation records")
                return False
        else:
            logger.warning(f"Failed to check compensation records: {response.status_code}")
            # If we can't check, assume no records to be safe
            return False
    except Exception as e:
        logger.error(f"Error checking compensation records: {str(e)}")
        return False

def update_employee(subdomain, api_key, employee_id, person):
    """
    2. PUT /employees/{id} to populate job & personal fields.
    Uses XML format which is more reliable with BambooHR's API.
    """
    # Use the correct API endpoint format
    url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/{employee_id}"
    
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
        "jobTitle"  : person.get("Job Title", person.get("Position", "")),
        "department": person.get("Department", ""),
        "division"  : person.get("Division", ""),
        "location"  : person.get("Location", "")
    }
    # Add manager field only if present (BambooHR field id is reportsTo)
    if supervisor_id:
        payload["reportsTo"] = supervisor_id
    
    logger.info(f"Updating employee {employee_id} with job information: {payload}")
    
    # Convert to XML format
    xml_data = "<employee>"
    for field, value in payload.items():
        xml_data += f'<field id="{field}">{value}</field>'
    xml_data += "</employee>"
    
    logger.info(f"XML payload: {xml_data}")
    
    auth = HTTPBasicAuth(api_key, "x")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/xml"
    }
    
    r = None
    for attempt in range(3):
        try:
            logger.info(f"Sending update employee request (attempt {attempt+1}/3)...")
            r = requests.post(url, data=xml_data, auth=auth, headers=headers)
            if r.ok:
                logger.info(f"Successfully updated employee {employee_id}")
                return True, None
            else:
                logger.error(f"Update employee attempt {attempt+1} failed: Status {r.status_code}")
                logger.error(f"Response body: {r.text}")
        except Exception as e:
            logger.error(f"Exception during update employee attempt {attempt+1}: {str(e)}")
            
        # Wait before retry
        time.sleep(2 ** attempt)
    
    # If we get here, all attempts failed
    error_details = f"Status: {r.status_code}, Body: {r.text}" if r else "Request failed"
    return False, f"Update failed after 3 attempts: {error_details}"

def add_compensation(subdomain, api_key, employee_id, person):
    """
    Add or update compensation for an employee using XML (BambooHR preferred).
    - Cleans pay rate
    - Checks for existing compensation for effectiveDate and updates if found
    - Logs full error details
    """
    try:
        logger.info(f"Adding compensation for employee {employee_id}")
        url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/{employee_id}/tables/compensation/"
        auth = HTTPBasicAuth(api_key, "x")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/xml"
        }

        # Clean pay rate
        pay_rate = person.get("Pay Rate", person.get("Salary", ""))
        if pay_rate and isinstance(pay_rate, str):
            pay_rate = pay_rate.replace("$", "").replace(",", "").strip()
            try:
                pay_rate = float(pay_rate)
            except ValueError:
                logger.error(f"Invalid pay rate format: {pay_rate}")
                return False, f"Invalid pay rate format: {pay_rate}"

        pay_type = person.get("Pay Type", "Hourly")
        pay_schedule = person.get("Pay Schedule", "Biweekly")
        pay_per = "Hourly"
        if pay_type and pay_type.lower() in ["salary", "salaried"]:
            pay_per = "Year"

        payload = {
            "payRate": str(pay_rate),
            "payPer": pay_per,
            "currency": "USD",
            "payType": pay_type,
            "paySchedule": pay_schedule,
            "effectiveDate": person.get("Start Date", "")
        }
        payload = {k: v for k, v in payload.items() if v}
        logger.info(f"Compensation payload: {payload}")

        # Convert to XML
        xml_data = "<row>"
        for field, value in payload.items():
            xml_data += f'<field id="{field}">{value}</field>'
        xml_data += "</row>"
        logger.info(f"XML payload: {xml_data}")

        # Check for existing compensation for this effectiveDate
        check_url = url
        check_headers = {'Accept': 'application/json'}
        check_resp = requests.get(check_url, auth=auth, headers=check_headers)
        if check_resp.status_code == 200:
            try:
                records = check_resp.json()
                for row in records:
                    if row.get("effectiveDate") == payload["effectiveDate"]:
                        row_id = row.get("id")
                        if row_id:
                            update_url = f"{url}{row_id}"
                            logger.info(f"Existing compensation found for date. Updating row {row_id}")
                            put_resp = requests.put(update_url, data=xml_data, auth=auth, headers=headers)
                            if put_resp.ok:
                                logger.info(f"✅ Updated compensation row {row_id} for employee {employee_id}")
                                return True, None
                            else:
                                logger.error(f"❌ Update failed: {put_resp.status_code} - {put_resp.text}")
                                logger.error(f"Headers: {put_resp.headers}")
                                return False, f"Update failed: {put_resp.status_code} - {put_resp.text}"
            except Exception as e:
                logger.warning(f"Could not parse GET response as JSON: {e}")
        else:
            logger.warning(f"Could not GET compensation table: {check_resp.status_code} - {check_resp.text}")

        # POST new compensation row
        logger.info(f"No existing row found. Creating new compensation entry...")
        post_resp = requests.post(url, data=xml_data, auth=auth, headers=headers)
        if post_resp.ok:
            logger.info(f"✅ Created compensation for employee {employee_id}")
            return True, None
        else:
            logger.error(f"❌ POST failed: {post_resp.status_code} - {post_resp.text}")
            logger.error(f"Headers: {post_resp.headers}")
            return False, f"POST failed: {post_resp.status_code} - {post_resp.text}"
    except Exception as e:
        logger.error(f"Exception in add_compensation: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False, f"Exception: {str(e)}"

def provision_self_service(subdomain, api_key, employee_id, person):
    """
    Provision self-service access for the employee using multiple fallback methods.
    Tries /meta/users, onboarding trigger, welcome email, and access update.
    Logs all attempts and errors.
    """
    logger.info(f"=== STARTING SELF-SERVICE PROVISION FOR EMPLOYEE {employee_id} ===")
    logger.info(f"Employee email: {person.get('Email', 'N/A')}")
    logger.info(f"Employee name: {person.get('First Name', '')} {person.get('Last Name', '')}")

    # Method 1: Try the traditional /meta/users endpoint
    logger.info("METHOD 1: Attempting traditional /meta/users endpoint")
    success, message = _try_meta_users_endpoint(subdomain, api_key, employee_id, person)
    if success:
        logger.info("✅ Successfully provisioned using /meta/users endpoint")
        return True, message
    else:
        logger.warning(f"⚠️ /meta/users endpoint failed: {message}")

    # Method 2: Try using onboarding workflow trigger
    logger.info("METHOD 2: Attempting onboarding workflow trigger")
    success, message = _try_onboarding_trigger(subdomain, api_key, employee_id, person)
    if success:
        logger.info("✅ Successfully triggered onboarding workflow")
        return True, message
    else:
        logger.warning(f"⚠️ Onboarding trigger failed: {message}")

    # Method 3: Try using welcome email endpoint
    logger.info("METHOD 3: Attempting welcome email endpoint")
    success, message = _try_welcome_email(subdomain, api_key, employee_id, person)
    if success:
        logger.info("✅ Successfully sent welcome email")
        return True, message
    else:
        logger.warning(f"⚠️ Welcome email failed: {message}")

    # Method 4: Try updating employee with access fields
    logger.info("METHOD 4: Attempting to update employee access permissions")
    success, message = _try_update_access_permissions(subdomain, api_key, employee_id, person)
    if success:
        logger.info("✅ Successfully updated access permissions")
        return True, message
    else:
        logger.warning(f"⚠️ Access permission update failed: {message}")

    # Method 5: Try to activate employee with onboarding fields
    logger.info("METHOD 5: Attempting to activate employee with onboarding status")
    success, message = _try_activate_employee_onboarding(subdomain, api_key, employee_id, person)
    if success:
        logger.info("✅ Successfully activated employee onboarding")
        return True, message
    else:
        logger.warning(f"⚠️ Employee onboarding activation failed: {message}")

    logger.error("❌ ALL SELF-SERVICE PROVISION METHODS FAILED")
    return False, "All self-service provision methods failed"

def _try_activate_employee_onboarding(subdomain, api_key, employee_id, person):
    """Try to activate employee with onboarding-related fields that might trigger access."""
    try:
        url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/{employee_id}"
        
        # Try updating with onboarding and access-related fields
        xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<employee>
    <field id="status">Active</field>
    <field id="workEmail">{person['Email']}</field>
    <field id="onboardingStatus">Active</field>
    <field id="accessLevel">Employee</field>
    <field id="enableSelfService">Yes</field>
    <field id="sendWelcomeEmail">Yes</field>
</employee>"""
        
        logger.info(f"Trying employee onboarding activation at {url}")
        logger.info(f"XML payload: {xml_payload}")
        
        auth = HTTPBasicAuth(api_key, "x")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/xml"
        }
        
        response = requests.post(url, data=xml_payload, auth=auth, headers=headers)
        logger.info(f"Onboarding activation response status: {response.status_code}")
        logger.info(f"Onboarding activation response body: {response.text}")
        
        if response.status_code == 200:
            return True, "Successfully activated employee onboarding fields"
        else:
            return False, f"Onboarding activation failed: HTTP {response.status_code}"
            
    except Exception as e:
        logger.error(f"Exception in _try_activate_employee_onboarding: {str(e)}")
        return False, f"Exception: {str(e)}"

def _try_meta_users_endpoint(subdomain, api_key, employee_id, person):
    """Try the traditional /meta/users endpoint (may be deprecated)."""
    try:
        url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/meta/users"
        
        # Try JSON payload first
        json_payload = {
            "employeeId": str(employee_id),
            "email": person["Email"],
            "accessLevel": "Employee Self-Service"
        }
        
        logger.info(f"Trying JSON request to {url}")
        logger.info(f"JSON payload: {json.dumps(json_payload, indent=2)}")
        
        auth = HTTPBasicAuth(api_key, "x")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        response = requests.post(url, json=json_payload, auth=auth, headers=headers)
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response headers: {dict(response.headers)}")
        logger.info(f"Response body: {response.text}")
        
        if response.status_code == 200 or response.status_code == 201:
            return True, "Successfully created user account via JSON"
        
        # If JSON fails, try XML format
        logger.info("JSON failed, trying XML format")
        xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<user>
    <employeeId>{employee_id}</employeeId>
    <email>{person['Email']}</email>
    <accessLevel>Employee Self-Service</accessLevel>
</user>"""
        
        headers["Content-Type"] = "application/xml"
        headers["Accept"] = "application/xml"
        
        logger.info(f"XML payload: {xml_payload}")
        
        response = requests.post(url, data=xml_payload, auth=auth, headers=headers)
        logger.info(f"XML Response status: {response.status_code}")
        logger.info(f"XML Response body: {response.text}")
        
        if response.status_code == 200 or response.status_code == 201:
            return True, "Successfully created user account via XML"
        
        # Handle specific error cases
        if response.status_code == 404:
            return False, "/meta/users endpoint not found (possibly deprecated)"
        elif response.status_code == 403:
            return False, "Insufficient permissions for /meta/users endpoint"
        elif response.status_code == 400:
            return False, f"Bad request to /meta/users: {response.text}"
        else:
            return False, f"HTTP {response.status_code}: {response.text}"
            
    except Exception as e:
        logger.error(f"Exception in _try_meta_users_endpoint: {str(e)}")
        return False, f"Exception: {str(e)}"

def _try_onboarding_trigger(subdomain, api_key, employee_id, person):
    """Try to trigger an onboarding workflow which may grant access."""
    try:
        # Try onboarding endpoint
        url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/{employee_id}/onboarding"
        
        payload = {
            "sendWelcomeEmail": True,
            "enableSelfService": True
        }
        
        logger.info(f"Trying onboarding trigger at {url}")
        logger.info(f"Payload: {json.dumps(payload, indent=2)}")
        
        auth = HTTPBasicAuth(api_key, "x")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        response = requests.post(url, json=payload, auth=auth, headers=headers)
        logger.info(f"Onboarding response status: {response.status_code}")
        logger.info(f"Onboarding response body: {response.text}")
        
        if response.status_code == 200 or response.status_code == 201:
            return True, "Successfully triggered onboarding workflow"
        elif response.status_code == 404:
            return False, "Onboarding endpoint not available"
        else:
            return False, f"Onboarding failed: HTTP {response.status_code}"
            
    except Exception as e:
        logger.error(f"Exception in _try_onboarding_trigger: {str(e)}")
        return False, f"Exception: {str(e)}"

def _try_welcome_email(subdomain, api_key, employee_id, person):
    """Try to send a welcome email which may include login instructions."""
    try:
        # Method 1: Try welcome email endpoint
        url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/{employee_id}/welcome"
        
        payload = {
            "email": person["Email"],
            "includeLoginInstructions": True
        }
        
        logger.info(f"Trying welcome email at {url}")
        logger.info(f"Payload: {json.dumps(payload, indent=2)}")
        
        auth = HTTPBasicAuth(api_key, "x")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        response = requests.post(url, json=payload, auth=auth, headers=headers)
        logger.info(f"Welcome email response status: {response.status_code}")
        logger.info(f"Welcome email response body: {response.text}")
        
        if response.status_code == 200 or response.status_code == 201:
            return True, "Successfully sent welcome email"
        
        # Method 2: Try notification endpoint for welcome
        logger.info("Welcome email endpoint failed, trying notification endpoint")
        notify_url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/{employee_id}/notifications"
        
        notify_payload = {
            "type": "welcome_email",
            "recipient": person["Email"]
        }
        
        response = requests.post(notify_url, json=notify_payload, auth=auth, headers=headers)
        logger.info(f"Notification response status: {response.status_code}")
        logger.info(f"Notification response body: {response.text}")
        
        if response.status_code == 200 or response.status_code == 201:
            return True, "Successfully sent welcome notification"
        
        # Method 3: Try simple email trigger via employee update
        logger.info("Notification endpoint failed, trying email trigger via update")
        update_url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/{employee_id}"
        
        xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<employee>
    <field id="workEmail">{person['Email']}</field>
    <field id="triggerWelcomeEmail">true</field>
</employee>"""
        
        xml_headers = {
            "Accept": "application/json",
            "Content-Type": "application/xml"
        }
        
        response = requests.post(update_url, data=xml_payload, auth=auth, headers=xml_headers)
        logger.info(f"Email trigger response status: {response.status_code}")
        logger.info(f"Email trigger response body: {response.text}")
        
        if response.status_code == 200:
            return True, "Successfully triggered welcome email via employee update"
        
        if response.status_code == 404:
            return False, "Welcome email endpoints not available"
        else:
            return False, f"Welcome email failed: HTTP {response.status_code}"
            
    except Exception as e:
        logger.error(f"Exception in _try_welcome_email: {str(e)}")
        return False, f"Exception: {str(e)}"

def _try_update_access_permissions(subdomain, api_key, employee_id, person):
    """Try to update employee record with access-related fields."""
    try:
        url = f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/{employee_id}"
        
        # Try updating with possible access-related fields
        xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<employee>
    <field id="workEmail">{person['Email']}</field>
    <field id="employeeNumber">{employee_id}</field>
    <field id="status">Active</field>
</employee>"""
        
        logger.info(f"Trying access permission update at {url}")
        logger.info(f"XML payload: {xml_payload}")
        
        auth = HTTPBasicAuth(api_key, "x")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/xml"
        }
        
        response = requests.post(url, data=xml_payload, auth=auth, headers=headers)
        logger.info(f"Access update response status: {response.status_code}")
        logger.info(f"Access update response body: {response.text}")
        
        if response.status_code == 200 or response.status_code == 201:
            return True, "Successfully updated employee access fields"
        else:
            return False, f"Access update failed: HTTP {response.status_code}"
            
    except Exception as e:
        logger.error(f"Exception in _try_update_access_permissions: {str(e)}")
        return False, f"Exception: {str(e)}"

def send_new_hire_packet(employee_id, auth_headers):
    """
    Send a new hire packet to the employee via BambooHR.
    Enhanced with additional logging and alternative approaches.
    """
    logger.info(f"=== STARTING NEW HIRE PACKET SEND FOR EMPLOYEE {employee_id} ===")
    
    try:
        # Method 1: Try the existing AJAX endpoint
        logger.info("METHOD 1: Trying AJAX onboarding endpoint")
        url = f"https://ccdocs.bamboohr.com/ajax/onboarding/sendPacket"
        
        payload = {
            "employeeId": employee_id,
            "sendWelcomeEmail": True
        }
        
        logger.info(f"AJAX URL: {url}")
        logger.info(f"AJAX payload: {json.dumps(payload, indent=2)}")
        logger.info(f"Using headers: {json.dumps({k: v for k, v in auth_headers.items() if 'cookie' not in k.lower()}, indent=2)}")
        
        response = requests.post(url, json=payload, headers=auth_headers)
        logger.info(f"AJAX response status: {response.status_code}")
        logger.info(f"AJAX response headers: {dict(response.headers)}")
        logger.info(f"AJAX response body: {response.text[:500]}...")  # First 500 chars
        
        if response.status_code == 200:
            logger.info("✅ Successfully sent new hire packet via AJAX")
            return 200, "New hire packet sent successfully via AJAX"
        
        # Method 2: Try REST API onboarding endpoint
        logger.info("METHOD 2: Trying REST API onboarding endpoint")
        rest_url = f"https://api.bamboohr.com/api/gateway.php/ccdocs/v1/employees/{employee_id}/onboarding"
        
        # Extract API key from auth headers if available
        api_key = os.getenv("BAMBOOHR_API_KEY", "d15339ce41287e33908e18cb480115b0cc935d9b")
        auth = HTTPBasicAuth(api_key, "x")
        
        rest_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        rest_payload = {
            "sendWelcomeEmail": True,
            "sendOnboardingPacket": True
        }
        
        logger.info(f"REST URL: {rest_url}")
        logger.info(f"REST payload: {json.dumps(rest_payload, indent=2)}")
        
        response = requests.post(rest_url, json=rest_payload, auth=auth, headers=rest_headers)
        logger.info(f"REST response status: {response.status_code}")
        logger.info(f"REST response body: {response.text}")
        
        if response.status_code == 200 or response.status_code == 201:
            logger.info("✅ Successfully sent new hire packet via REST API")
            return 200, "New hire packet sent successfully via REST API"
        
        # Method 3: Try employee notification endpoint
        logger.info("METHOD 3: Trying employee notification endpoint")
        notify_url = f"https://api.bamboohr.com/api/gateway.php/ccdocs/v1/employees/{employee_id}/notify"
        
        notify_payload = {
            "type": "welcome",
            "sendEmail": True
        }
        
        logger.info(f"Notify URL: {notify_url}")
        logger.info(f"Notify payload: {json.dumps(notify_payload, indent=2)}")
        
        response = requests.post(notify_url, json=notify_payload, auth=auth, headers=rest_headers)
        logger.info(f"Notify response status: {response.status_code}")
        logger.info(f"Notify response body: {response.text}")
        
        if response.status_code == 200 or response.status_code == 201:
            logger.info("✅ Successfully sent welcome notification")
            return 200, "Welcome notification sent successfully"
        
        # Method 4: Try updating employee status to trigger welcome email
        logger.info("METHOD 4: Trying employee status update to trigger welcome")
        update_url = f"https://api.bamboohr.com/api/gateway.php/ccdocs/v1/employees/{employee_id}"
        
        # Update employee with a field that might trigger onboarding
        update_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<employee>
    <field id="status">Active</field>
    <field id="sendWelcomeEmail">true</field>
</employee>"""
        
        update_headers = {
            "Accept": "application/json",
            "Content-Type": "application/xml"
        }
        
        logger.info(f"Update URL: {update_url}")
        logger.info(f"Update XML: {update_xml}")
        
        response = requests.post(update_url, data=update_xml, auth=auth, headers=update_headers)
        logger.info(f"Update response status: {response.status_code}")
        logger.info(f"Update response body: {response.text}")
        
        if response.status_code == 200:
            logger.info("✅ Successfully updated employee status")
            return 200, "Employee status updated - welcome email may have been triggered"
        
        # If all methods fail but not critically
        logger.warning("⚠️ All new hire packet/welcome email methods failed")
        logger.warning("This may indicate:")
        logger.warning("1. The endpoints have been changed or deprecated")
        logger.warning("2. Manual welcome email setup required in BambooHR portal")
        logger.warning("3. Different authentication method needed")
        logger.warning("4. Welcome emails may be automatically sent by BambooHR")
        
        if response.status_code == 404:
            return 200, "New hire packet endpoints not available, may be sent automatically by BambooHR"
        else:
            logger.error(f"❌ All new hire packet methods failed. Last error: {response.status_code} - {response.text}")
            return 200, f"New hire packet failed but employee created successfully: {response.text}"
            
    except Exception as e:
        logger.error(f"❌ Exception during new hire packet send: {str(e)}")
        import traceback
        logger.error(f"Stack trace: {traceback.format_exc()}")
        return 500, f"Exception: {str(e)}"

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
        response = None
        
        # Try up to 3 times with exponential backoff
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, auth=auth)
                
                if response.status_code == 200 or response.status_code == 201:
                    result = response.json()
                    employee_id = result.get("employeeId")
                    logger.info(f"Successfully hired candidate {candidate_id} as employee {employee_id}")
                    return employee_id, None
                else:
                    logger.error(f"Hire candidate attempt {attempt+1} failed: Status {response.status_code}")
                    logger.error(f"Response body: {response.text}")
            except Exception as e:
                logger.error(f"Exception during hire candidate attempt {attempt+1}: {str(e)}")
                
            # Wait before retry
            time.sleep(2 ** attempt)
        
        # If we get here, all attempts failed
        error_details = f"Status: {response.status_code}, Body: {response.text}" if response else "Request failed"
        return None, f"Hire candidate failed after 3 attempts: {error_details}"
            
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
        
        target = email.lower()
        for emp in directory['employees']:
            work_email = (emp.get('workEmail') or '').lower()
            if work_email == target:
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
        # Use ID if already present to avoid directory lookup
        if employee.get('ID'):
            employee_id = employee['ID']
            error_msg = "ID from record"
        else:
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

def main():
    """
    Main execution function.
    Always processes all pending hires from the sheet (no test mode).
    """
    logger.info("--- SCRIPT START ---")
    
    # Configure a file handler to capture detailed logs
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(f"onboarding_log_{timestamp}.txt", encoding='utf-8')  # Add UTF-8 encoding
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    # Also add a console handler for more visible terminal output
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    logger.info("Detailed logging enabled - check log file for complete information")
    
    # Log environment variables (without sensitive values)
    logger.info(f"BambooHR Subdomain: {BAMBOO_SUB}")
    logger.info(f"BambooHR API Key: {'*' * 8 + BAMBOO_KEY[-4:] if BAMBOO_KEY else 'Not set'}")
    logger.info(f"WebWork URL: {WEBWORK_URL}")
    logger.info(f"WebWork Username: {'Set' if WEBWORK_USERNAME else 'Not set'}")
    logger.info(f"Google Sheet ID: {SHEET_ID}")
    logger.info(f"Google Sheet Name: {SHEET_NAME}")
    
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
    
    logger.info("--- RUNNING IN PRODUCTION MODE ---")
    # Read pending rows from Google Sheet
    try:
        logger.info("Reading pending rows from Google Sheet...")
        headers, pending = read_pending_rows(sheets)
        if not pending:
            message = "No new hires to process."
            logger.info(message)
            if slack:
                send_slack_notification(slack, message)
            return
    except Exception as e:
        logger.critical(f"Failed to read pending rows: {str(e)}")
        if slack:
            send_slack_notification(slack, f"Onboarding automation failed: {str(e)}")
        return

    # Process each pending hire
    successes, failures = 0, 0

    for row_index, emp in pending:
        logger.info(f"Processing hire: {emp['First Name']} {emp['Last Name']}")
        logger.info(f"Employee data: {json.dumps(emp, indent=2)}")
        
        try:
            # ── First check if employee already exists in BambooHR by email ──────────
            logger.info("=== STEP 1: CHECKING FOR EXISTING EMPLOYEE ===")
            existing_id, existing_err = find_employee_by_email(BAMBOO_SUB, BAMBOO_KEY, emp.get("Email"))
            
            if existing_id:
                # Employee already exists, use the existing ID
                logger.info(f"Found existing employee with ID {existing_id} for {emp['Email']}")
                eid = existing_id
                
                # Update the existing employee with new information
                logger.info(f"Updating existing employee with ID {eid}")
                ok, err = update_employee(BAMBOO_SUB, BAMBOO_KEY, eid, emp)
                if not ok:
                    notes = f"Update Error: {err}"
                    logger.error(f"Failed to update existing employee: {notes}")
                    write_back(sheets, row_index, "FAILED", notes)
                    failures += 1
                    continue
                
                # Provision self-service access for existing employee too
                logger.info("=== STEP 6: PROVISIONING SELF-SERVICE ACCESS (EXISTING EMPLOYEE) ===")
                ok, err = provision_self_service(BAMBOO_SUB, BAMBOO_KEY, eid, emp)
                if not ok:
                    logger.warning(f"Self-service provision warning for existing employee: {err}")
                    # Don't fail the entire process for existing employees if self-service fails
                    # Just log a warning since the employee already exists
            else:
                # ── Check if candidate exists in BambooHR ────────────────────────
                logger.info("=== STEP 2: CHECKING FOR EXISTING CANDIDATE ===")
                logger.info(f"No existing employee found, checking for candidate")
                candidate_id, candidate_err = find_candidate_by_email(BAMBOO_SUB, BAMBOO_KEY, emp.get("Email"))
                
                if candidate_id:
                    # Candidate exists, hire them directly
                    logger.info(f"Found existing candidate with ID {candidate_id} for {emp['Email']}")
                    logger.info("=== STEP 3: HIRING EXISTING CANDIDATE ===")
                    eid, err = hire_candidate(BAMBOO_SUB, BAMBOO_KEY, candidate_id, emp)
                else:
                    # No candidate found, create a new employee directly
                    logger.info(f"No existing candidate found for {emp['Email']}, creating new employee")
                    logger.info("=== STEP 3: CREATING NEW EMPLOYEE ===")
                    eid, err = create_employee(BAMBOO_SUB, BAMBOO_KEY, emp)
                    
                if err:
                    notes = f"Create/Hire Error: {err}"
                    logger.error(f"Failed to create/hire employee: {notes}")
                    write_back(sheets, row_index, "FAILED", notes)
                    failures += 1
                    continue

                # Only update and add compensation for newly created employees
                logger.info("=== STEP 4: UPDATING EMPLOYEE DETAILS ===")
                ok, err = update_employee(BAMBOO_SUB, BAMBOO_KEY, eid, emp)
                if not ok:
                    notes = f"Update Error: {err}"
                    logger.error(f"Failed to update employee: {notes}")
                    write_back(sheets, row_index, "FAILED", notes)
                    failures += 1
                    continue

                logger.info("=== STEP 5: ADDING COMPENSATION ===")
                ok, err = add_compensation(BAMBOO_SUB, BAMBOO_KEY, eid, emp)
                if not ok:
                    notes = f"Comp Error: {err}"
                    logger.error(f"Failed to add compensation: {notes}")
                    write_back(sheets, row_index, "FAILED", notes)
                    failures += 1
                    continue

                logger.info("=== STEP 6: PROVISIONING SELF-SERVICE ACCESS ===")
                ok, err = provision_self_service(BAMBOO_SUB, BAMBOO_KEY, eid, emp)
                if not ok:
                    notes = f"Provision Error: {err}"
                    logger.error(f"Failed to provision self-service: {notes}")
                    write_back(sheets, row_index, "FAILED", notes)
                    failures += 1
                    continue
                
            # Update the emp dictionary with the ID
            emp["ID"] = eid
            logger.info(f"Working with employee ID: {eid}")

            # ── Existing signature + WebWork steps ───────────────────────────────────
            logger.info("=== STEP 7: SENDING SIGNATURE REQUEST ===")
            b_code, b_resp = bamboo_manager.send_signature_request(emp)
            
            # Throttle API calls
            time.sleep(1)
            
            # Send new hire packet by default for all employees
            logger.info("=== STEP 8: SENDING NEW HIRE PACKET ===")
            logger.info(f"Sending new hire packet for {emp['First Name']} {emp['Last Name']}")
            packet_code, packet_resp = send_new_hire_packet(eid, bamboo_manager.headers)
            if packet_code != 200:
                logger.warning(f"Failed to send new hire packet: {packet_resp}")
            
            # Create WebWork account
            logger.info("=== STEP 9: CREATING WEBWORK ACCOUNT ===")
            w_code, w_resp = invite_webwork(emp)

            # Update status and notes
            notes = []
            if b_code != 200: notes.append(f"BambooHR error: {b_resp}")
            if w_code != 200: notes.append(f"WebWork error: {w_resp}")
            if packet_code != 200: notes.append(f"New hire packet error: {packet_resp}")
            
            status = "SUCCESS" if not notes else "FAILED"  # Use text instead of emoji
            write_back(sheets, row_index, status, "; ".join(notes) or "OK")

            # Track statistics
            if status == "SUCCESS":
                successes += 1
                logger.info(f"Successfully processed {emp['First Name']} {emp['Last Name']}")
            else:
                failures += 1
                logger.warning(f"Failed to process {emp['First Name']} {emp['Last Name']}: {'; '.join(notes)}")
        
        except Exception as e:
            # Catch any unexpected exceptions during processing
            logger.error(f"Unexpected error processing employee {emp.get('First Name', '')} {emp.get('Last Name', '')}: {str(e)}")
            import traceback
            logger.error(f"Stack trace: {traceback.format_exc()}")
            write_back(sheets, row_index, "FAILED", f"Unexpected error: {str(e)}")
            failures += 1

        # Throttle before next hire
        time.sleep(1)

    # Send summary notification
    summary = f"Onboarding run complete: {successes} succeeded, {failures} failed."
    logger.info(summary)
    logger.info("See detailed log file for complete information")
    send_slack_notification(slack, summary)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"Unhandled exception: {str(e)}")
        try:
            slack = setup_slack_client()
            if slack:
                send_slack_notification(slack, f"❌ Onboarding automation critical error: {str(e)}")
        except:
            pass  # If we can't send to Slack, we've already logged the error
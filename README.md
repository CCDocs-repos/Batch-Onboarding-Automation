# Batch-Onboarding-Automation

Automate your employee onboarding process with seamless integration between Google Sheets, BambooHR, WebWork, and custom welcome emails.

---

## 🚀 Overview
This project automates the onboarding of new hires by reading pending employees from a Google Sheet, creating/updating their records in BambooHR, provisioning WebWork accounts, sending Slack notifications, and (optionally) sending custom welcome emails if BambooHR's API cannot.

---

## ✨ Features
- **Google Sheets Integration:** Reads pending hires and updates onboarding status.
- **BambooHR Integration:**
  - Creates or updates employee records.
  - Sets up compensation (using XML for reliability).
  - Triggers onboarding workflows and welcome emails (with multiple API fallbacks).
- **WebWork Integration:** Creates time-tracking accounts for new hires.
- **Slack Integration:** Sends onboarding summaries to a Slack channel.
- **Custom Welcome Email:** Optionally sends a custom welcome email if BambooHR cannot.
- **Robust Logging:** Multi-level logs for debugging and audit.

---

## 🛠️ Setup Instructions

### 1. **Clone the Repository**
```bash
git clone https://github.com/your-org/Batch-Onboarding-Automation.git
cd Batch-Onboarding-Automation
```

### 2. **Install Dependencies**
```bash
pip install -r requirements.txt
```

### 3. **Environment Variables**
Create a `.env` file in the project root with the following variables:
```
BAMBOOHR_SUBDOMAIN=your_bamboohr_subdomain
BAMBOOHR_API_KEY=your_bamboohr_api_key
BAMBOOHR_TEMPLATE_ID=your_bamboohr_template_id
WEBWORK_URL=https://www.webwork-tracker.com/rest-api/users
WEBWORK_USERNAME=your_webwork_admin
WEBWORK_PASSWORD=your_webwork_password
SHEET_ID=your_google_sheet_id
SHEET_NAME=Sheet1
SLACK_BOT_TOKEN=your_slack_bot_token
SLACK_CHANNEL=your_slack_channel_id
```

### 4. **Google Service Account**
- Download your Google service account JSON file and place it in the project root as `SERVICE_ACCOUNT_FILE .json` (note the space in the filename).

### 5. **BambooHR Authentication (if using 2FA endpoints)**
- Run `bamboo_auth.py` to generate session cookies if required for advanced BambooHR endpoints.

---

## ⚡ Usage

### **Run the Onboarding Script**
```bash
python onboard.py
```
- The script will process all pending hires in your Google Sheet.
- Logs are saved with timestamps for debugging.

### **Custom Welcome Email (Optional)**
If BambooHR cannot send welcome emails, you can enable custom email logic in the script (see code comments for setup).

---

## 📝 Google Sheet Format
Your Google Sheet should have columns like:
- First Name
- Last Name
- Email
- Start Date
- Job Title
- Department
- Location
- Pay Rate
- Pay Type
- Pay Schedule
- Overall status (used by the script to track progress)

---

## 🐞 Troubleshooting
- **BambooHR Welcome Emails Not Sending:** This is a BambooHR API limitation. Use the custom email logic or send manually from the BambooHR UI.
- **API 404 Errors:** Some endpoints may not be available for your BambooHR plan. The script will try all known methods and log details.
- **WebWork/Slack Issues:** Check your credentials and permissions.
- **Google Sheets Issues:** Ensure your service account has access to the sheet.
- **Logs:** Check the generated log files for detailed error messages and troubleshooting info.

---

## 🤝 Support & Contributions
- **Issues:** Please open an issue on GitHub for bugs or feature requests.
- **Pull Requests:** Contributions are welcome! Please submit a PR.
- **Contact:** For urgent help, contact the project maintainer or your HR/IT team.

---

## 📄 License
This project is licensed under the MIT License. 
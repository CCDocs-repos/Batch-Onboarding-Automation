#!/usr/bin/env python3
"""
Simple script to authenticate with BambooHR and save the cookies
"""

import os
import time
import json
import logging
import pyotp
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Get credentials from environment variables or use defaults
username = os.getenv("BAMBOOHR_USERNAME", "bolaji+1@ccdocs.com")
password = os.getenv("BAMBOOHR_PASSWORD", "AdminDev2025")
totp_secret = os.getenv("BAMBOOHR_TOTP_SECRET", "ZVFC264YXJVN7SSD")
subdomain = os.getenv("BAMBOOHR_SUBDOMAIN", "ccdocs")

def authenticate_bamboohr():
    """Authenticate with BambooHR and save cookies"""
    logger.info(f"Authenticating to BambooHR as {username}")
    
    # Configure Chrome
    opts = Options()
    opts.headless = False  # Set to False to see the browser
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_experimental_option("detach", True)  # Keep browser open
    
    # Use webdriver_manager to handle driver installation
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    
    try:
        # Navigate to BambooHR
        logger.info(f"Opening BambooHR at https://{subdomain}.bamboohr.com/")
        driver.get(f"https://{subdomain}.bamboohr.com/")
        
        # Wait for page to load
        time.sleep(2)
        logger.info(f"Current URL: {driver.current_url}")
        driver.save_screenshot("01_initial_page.png")
        
        # Handle login
        if "login" in driver.current_url:
            logger.info("Login page detected")
            
            # Take screenshot and save page source
            driver.save_screenshot("02_login_page.png")
            with open("login_page_source.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            logger.info("Saved login page screenshot and source")
            
            # Find username field
            try:
                username_field = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "lemail"))
                )
                logger.info("Found username field")
            except Exception as e:
                logger.error(f"Could not find username field: {str(e)}")
                driver.save_screenshot("error_username_field.png")
                
                # Try different selectors
                try:
                    # Print all input elements
                    inputs = driver.find_elements(By.TAG_NAME, "input")
                    logger.info(f"Found {len(inputs)} input elements")
                    for i, inp in enumerate(inputs):
                        logger.info(f"Input {i}: type={inp.get_attribute('type')}, id={inp.get_attribute('id')}, name={inp.get_attribute('name')}")
                    
                    # Try by CSS selector
                    username_field = driver.find_element(By.CSS_SELECTOR, "input[type='email']")
                    logger.info("Found username field by CSS selector")
                except Exception as e2:
                    logger.error(f"Still could not find username field: {str(e2)}")
                    raise
            
            # Enter username
            logger.info(f"Entering username: {username}")
            username_field.clear()
            username_field.send_keys(username)
            
            # Find password field
            try:
                password_field = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.ID, "password"))
                )
                logger.info("Found password field")
            except Exception as e:
                logger.error(f"Could not find password field: {str(e)}")
                driver.save_screenshot("error_password_field.png")
                
                # Try different selectors
                try:
                    password_field = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
                    logger.info("Found password field by CSS selector")
                except Exception as e2:
                    logger.error(f"Still could not find password field: {str(e2)}")
                    raise
            
            # Enter password
            logger.info("Entering password")
            password_field.clear()
            password_field.send_keys(password)
            driver.save_screenshot("03_credentials_entered.png")
            
            # Find login button
            try:
                login_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']"))
                )
                logger.info("Found login button")
            except Exception as e:
                logger.error(f"Could not find login button: {str(e)}")
                driver.save_screenshot("error_login_button.png")
                
                # Try different selectors
                try:
                    buttons = driver.find_elements(By.TAG_NAME, "button")
                    logger.info(f"Found {len(buttons)} button elements")
                    for i, btn in enumerate(buttons):
                        logger.info(f"Button {i}: text={btn.text}, type={btn.get_attribute('type')}")
                    
                    # Try by text
                    login_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Log In')]")
                    logger.info("Found login button by text")
                except Exception as e2:
                    logger.error(f"Still could not find login button: {str(e2)}")
                    raise
            
            # Click login button
            logger.info("Clicking login button")
            login_button.click()
            
            # Wait for 2FA page or dashboard
            time.sleep(5)
            logger.info(f"After login, URL: {driver.current_url}")
            driver.save_screenshot("04_after_login.png")
            
            # Handle 2FA if needed
            if "multi_factor_authentication" in driver.current_url:
                logger.info("2FA page detected")
                
                # Take screenshot and save page source
                driver.save_screenshot("05_2fa_page.png")
                with open("2fa_page_source.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
                logger.info("Saved 2FA page screenshot and source")
                
                # Print all input elements
                inputs = driver.find_elements(By.TAG_NAME, "input")
                logger.info(f"Found {len(inputs)} input elements on 2FA page")
                for i, inp in enumerate(inputs):
                    logger.info(f"Input {i}: type={inp.get_attribute('type')}, id={inp.get_attribute('id')}, name={inp.get_attribute('name')}")
                
                # Generate OTP code
                totp = pyotp.TOTP(totp_secret)
                code = totp.now()
                logger.info(f"Generated 2FA code: {code}")
                
                # Try different selectors for OTP field
                otp_field = None
                selectors = [
                    (By.NAME, "otp"),
                    (By.ID, "otp"),
                    (By.CSS_SELECTOR, "input[type='text']"),
                    (By.CSS_SELECTOR, "input[type='number']"),
                    (By.CSS_SELECTOR, "input:not([type='hidden'])")
                ]
                
                for by, selector in selectors:
                    try:
                        otp_field = driver.find_element(by, selector)
                        logger.info(f"Found OTP field using {by}: {selector}")
                        break
                    except:
                        logger.info(f"OTP field not found using {by}: {selector}")
                
                if otp_field:
                    # Enter OTP
                    logger.info("Entering OTP code")
                    otp_field.clear()
                    otp_field.send_keys(code)
                    driver.save_screenshot("06_otp_entered.png")

                    # Find and click the 'Continue' button
                    try:
                        # Wait for the button to be clickable, which is more reliable
                        verify_button = WebDriverWait(driver, 10).until(
                            EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='Continue']"))
                        )
                        logger.info("Found clickable 'Continue' button.")
                        logger.info("Clicking 'Continue' button...")
                        verify_button.click()
                        driver.save_screenshot("07_after_verify.png")
                    except Exception as e:
                        logger.error(f"Could not find or click the 'Continue' button. Error: {e}")
                        driver.save_screenshot("error_continue_button.png")
                else:
                    logger.error("Could not find OTP field using any selector")
            
            # Wait for dashboard
            time.sleep(5)
            logger.info(f"Final URL: {driver.current_url}")
            driver.save_screenshot("08_final_page.png")
            
            # Handle the "Trust this browser" page
            if "trusted_browser" in driver.current_url:
                logger.info("Trust this browser page detected.")
                try:
                    trust_button = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='Yes, Trust this Browser']"))
                    )
                    logger.info("Found 'Yes, Trust this Browser' button.")
                    trust_button.click()
                    logger.info("Clicked 'Yes, Trust this Browser'. Waiting for home page...")
                    # Wait for the URL to change to the home page
                    WebDriverWait(driver, 15).until(
                        EC.url_contains("home")
                    )
                    logger.info(f"URL after trusting browser: {driver.current_url}")
                    driver.save_screenshot("09_after_trusting.png")
                except Exception as e:
                    logger.warning(f"Could not click 'Yes, Trust this Browser' button. Continuing anyway. Error: {e}")
                    driver.save_screenshot("error_trust_button.png")
            
            # Check if login was successful by checking for 'home' in the URL
            if "home" in driver.current_url:
                logger.info("Login successful! Landed on home page.")
                
                # Get cookies for API calls
                cookies = driver.get_cookies()
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                logger.info(f"Cookie string: {cookie_str[:50]}...")
                
                # Save cookies to file
                with open("bamboo_cookies.json", "w") as f:
                    json.dump(cookies, f)
                logger.info("Saved cookies to bamboo_cookies.json")
                
                # Create headers
                headers = {
                    "Cookie": cookie_str,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"https://{subdomain}.bamboohr.com/files/",
                    "Accept": "application/json, text/plain, */*",
                    "User-Agent": driver.execute_script("return navigator.userAgent;")
                }
                
                # Save headers to file
                with open("bamboo_headers.json", "w") as f:
                    json.dump(headers, f)
                logger.info("Saved headers to bamboo_headers.json")

                # Navigate to home page for verification
                home_url = f"https://{subdomain}.bamboohr.com/home"
                logger.info(f"Navigating to home page for verification: {home_url}")
                driver.get(home_url)
                time.sleep(3)
                logger.info(f"Current URL is now: {driver.current_url}")
                driver.save_screenshot("bamboo_home_page.png")

                return headers
            else:
                logger.error(f"Login failed. Final URL was: {driver.current_url}")
                driver.save_screenshot("error_login_failed.png")
                return None
    except Exception as e:
        logger.error(f"Error during authentication: {str(e)}")
        if driver:
            driver.save_screenshot("error_exception.png")
        return None
    finally:
        # Keep browser open for debugging
        logger.info("Authentication completed. Browser will remain open for inspection.")
        # Don't close: driver.quit()

if __name__ == "__main__":
    headers = authenticate_bamboohr()
    if headers:
        logger.info("Authentication successful!")
    else:
        logger.error("Authentication failed!")
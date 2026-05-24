import time
import sys
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

def test_standard_selenium():
    print("Testing standard Selenium (Headless)...")
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    
    try:
        driver = webdriver.Chrome(options=options)
        driver.get("https://www.google.com")
        print(f"Success! Title: {driver.title}")
        driver.quit()
        return True
    except Exception as e:
        print(f"Standard Selenium Failed: {e}")
        return False

if __name__ == "__main__":
    if test_standard_selenium():
        print("\nSTANDARD SELENIUM WORKS!")
        sys.exit(0)
    else:
        print("\nSTANDARD SELENIUM FAILED!")
        sys.exit(1)

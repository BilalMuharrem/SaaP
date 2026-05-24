import time
import ssl
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

def main():
    import undetected_chromedriver as uc
    options = uc.ChromeOptions()
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Try fully headless with fake user agent
    driver = uc.Chrome(options=options, headless=True)
    try:
        driver.get("https://www.hepsiburada.com/sinbo-ssm-2513-tost-makinesi-p-MTINBOSSM2513")
        time.sleep(5)
        print("HEADLESS TITLE:", driver.title)
    finally:
        driver.quit()

if __name__ == "__main__":
    main()

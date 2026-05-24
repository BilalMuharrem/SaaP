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
    driver = uc.Chrome(options=options, version_main=145, headless=False)
    try:
        driver.minimize_window()
        url = "https://www.trendyol.com/awox/orion-tost-ve-izgara-makinesi-180-acilabilir-1800w-kirmizi-p-35222956"
        driver.get(url)
        time.sleep(5)
        # Test JS click
        driver.execute_script("window.scrollBy(0, 500);")
        time.sleep(2)
        name = driver.execute_script("return document.querySelector('h1').innerText;")
        print("NAME EXTRACTED:", name)
    finally:
        driver.quit()

if __name__ == "__main__":
    main()

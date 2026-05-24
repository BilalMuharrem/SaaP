import re
import os

with open("worker.py", "r", encoding="utf-8") as f:
    code = f.read()

# Replace check_tracked_products
old_check = """    # OPTIMIZED: Create driver ONCE for the whole batch
    driver = None
    try:
        driver = create_driver()
        for product in products:
            try:
                safe_navigate(driver, product.url)
                time.sleep(6)
                durum = check_blocked(driver)
                
                if durum == "OK":
                    try:
                        driver.execute_script("document.querySelectorAll('.modal, .popup, [id*=\\"onetrust\\"]').forEach(el => el.style.display='none');")
                    except: pass
                    
                    # --- PRICE TRACKING ---
                    fiyat_str = extract_price(driver)"""

new_check = """    # OPTIMIZED: Create driver ONCE for the whole batch
    from playwright.sync_api import sync_playwright
    import time
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
            page = context.new_page()

            for product in products:
                try:
                    page.goto(product.url, timeout=60000)
                    page.wait_for_timeout(5000)
                    durum = check_blocked(page)
                    
                    if durum == "OK":
                        try:
                            page.evaluate("document.querySelectorAll('.modal, .popup, [id*=\\"onetrust\\"]').forEach(el => el.style.display='none');")
                        except: pass
                        
                        # --- PRICE TRACKING ---
                        fiyat_str = extract_price(page)"""
                        
code = code.replace(old_check, new_check)

old_check_finally = """    finally:
        if driver:
            try: 
                driver.quit()
                print("[Worker] Chromedriver closed successfully.")
            except: pass"""
new_check_finally = """    except Exception as e:
        print(f"[Worker] Global error in tracking loop: {e}")"""
code = code.replace(old_check_finally, new_check_finally)

# Replace extract_price logic:
code = code.replace("driver.execute_script", "driver.evaluate")
code = code.replace("from selenium.webdriver.common.by import By", "")
code = code.replace("import undetected_chromedriver as uc", "")

# Rewrite headless functions loops
code = code.replace("driver = None\n        fiyat = \"Bulunamadı\"\n        urun_ismi = \"İsim Bulunamadı\"\n        durum = \"OK\"\n\n        try:\n            driver = create_driver()\n            safe_navigate(driver, url)\n            time.sleep(6)\n            durum = check_blocked(driver)", 
"fiyat = \"Bulunamadı\"\n        urun_ismi = \"İsim Bulunamadı\"\n        durum = \"OK\"\n\n        try:\n            page.goto(url, timeout=60000)\n            page.wait_for_timeout(5000)\n            durum = check_blocked(page)")

# Wrap run_price_headless loop with playwright context
code = re.sub(r'for idx, url in enumerate\(urls\):\n(.*?)url = url.strip\(\)', 
r'''with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        page = context.new_page()
        for idx, url in enumerate(urls):
            url = url.strip()''', code, count=1)

# do the same for run_review_headless
code = re.sub(r'for idx, url in enumerate\(urls\):\n(.*?)url = url.strip\(\)', 
r'''with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        page = context.new_page()
        for idx, url in enumerate(urls):
            url = url.strip()''', code, count=1)

# Remove finally driver.quit() from run_price and run_review
code = re.sub(r'finally:\s+if driver:\s+try: driver.quit\(\)\s+except: pass', '', code)

# Fix safe_navigate etc
old_create_driver = r'def create_driver\(\).*?def safe_navigate.*?def check_blocked'
new_create_driver = r"""def check_blocked"""
code = re.sub(old_create_driver, new_create_driver, code, flags=re.DOTALL)

with open("worker.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Convert finished successfully!")

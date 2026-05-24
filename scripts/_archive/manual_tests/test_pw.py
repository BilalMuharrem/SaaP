from playwright.sync_api import sync_playwright
import time

def test_pw():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            extra_http_headers={
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            }
        )
        # Advanced stealth
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        context.add_init_script("window.chrome = { runtime: {} };")
        page = context.new_page()
        
        url = "https://www.hepsiburada.com/tefal-toast-expert-4-dilim-kapasiteli-1800-watt-izgara-ve-tost-makinesi-inox-p-HBV000002E49W"
        print("Navigating...")
        page.goto(url)
        page.wait_for_timeout(5000)
        
        title = page.title()
        print("Title:", title)
        
        body_text = page.evaluate("document.body.innerText").lower()
        if 'robot' in title.lower() or 'captcha' in title.lower() or 'robot musunuz' in body_text:
            print("BLOCKED BY AKAMAI")
        else:
            price = page.evaluate("""() => {
                var el = document.querySelector('[data-test-id="price-current-price"]');
                return el ? el.innerText : 'No price';
            }""")
            print("SUCCESS! Price:", price)

test_pw()

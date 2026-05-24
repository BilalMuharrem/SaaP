import time
import sys
import json
from worker import create_driver, safe_navigate, check_blocked, extract_product_name, extract_price, analyze_reviews_with_ai

def test_create_driver():
    import undetected_chromedriver as uc
    options = uc.ChromeOptions()
    options.add_argument("--disable-notifications")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    driver = uc.Chrome(options=options, headless=True)
    driver.set_page_load_timeout(120)
    return driver

def main():
    print("Testing Hepsiburada Scraping...")
    url = "https://www.hepsiburada.com/sinbo-ssm-2513-tost-makinesi-p-MTINBOSSM2513?magaza=Hepsiburada"
    driver = test_create_driver()
    try:
        safe_navigate(driver, url)
        time.sleep(6)
        durum = check_blocked(driver)
        print("HB Status:", durum)
        
        name = extract_product_name(driver)
        print("HB Name:", name)
        
        price = extract_price(driver)
        print("HB Price:", price)
        
        if durum == "OK" or durum == "NOT_LOADED" or durum == "BLOCKED":
            print("Taking screenshot...")
            driver.save_screenshot("hb_block.png")
            print("HB Title:", driver.title)
    finally:
        driver.quit()

    print("\n\nTesting Groq AI Analizi...")
    reviews = [
        "Bu fiyata göre harika bir ürün, çok beğendim, tostları çok güzel yapıyor.",
        "Ürün elime kırık ulaştı, iade ediyorum.",
        "Satıcı çok hızlı gönderdi. Ürün gayet kullanışlı ve kaliteli.",
        "Berbat bir makine, peyniri eritiyor ama ekmeği kızartmıyor, kesinlikle çöp.",
        "Plastik kokusu geliyor çalışırken, sağlık açısından sıkıntılı."
    ] * 4  # 20 reviews
    
    # We need the API key from Setting in DB, but since we're outside app context...
    from app import app
    from models import Setting
    with app.app_context():
        key = Setting.get('groq_api_key', '')
        
    if not key:
        print("No GROQ API KEY found in settings!")
        return

    print("Running Groq analysis on 20 dummy reviews...")
    res = analyze_reviews_with_ai(reviews, key)
    print("Groq Result:", json.dumps(res, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()

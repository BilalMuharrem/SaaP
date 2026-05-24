import traceback
from worker import create_driver, safe_navigate, check_blocked, extract_product_name, extract_price

def test_price():
    url = "https://www.trendyol.com/awox/orion-tost-ve-izgara-makinesi-180-acilabilir-1800w-kirmizi-p-35222956"
    driver = None
    try:
        driver = create_driver()
        safe_navigate(driver, url)
        import time
        time.sleep(6)
        durum = check_blocked(driver)
        print("DURUM:", durum)
        if durum == "OK":
            urun_ismi = extract_product_name(driver)
            fiyat = extract_price(driver)
            print("ISIM:", urun_ismi)
            print("FIYAT:", fiyat)
    except Exception as e:
        print("EXCEPTION CAUGHT!")
        traceback.print_exc()
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    test_price()

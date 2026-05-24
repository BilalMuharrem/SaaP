import undetected_chromedriver as uc
import time
import ssl

try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

def test_uc():
    options = uc.ChromeOptions()
    options.add_argument("--disable-notifications")
    options.add_argument("--window-size=1920,1080")
    # Using version_main=146 because the local Chrome is 146
    driver = uc.Chrome(options=options, version_main=146)
    
    url = "https://www.hepsiburada.com/tefal-toast-expert-4-dilim-kapasiteli-1800-watt-izgara-ve-tost-makinesi-inox-p-HBV000002E49W"
    print("Navigating via UC...")
    driver.get(url)
    time.sleep(6)
    
    title = driver.title
    print("UC Title:", title)
    
    hata_durumu = driver.execute_script("""
        var t = document.title.toLowerCase().trim(); 
        var b = document.body.innerText.toLowerCase();
        if (t.includes('robot') || t.includes('captcha') || b.includes('robot musunuz') || t === 'hepsiburada.com' || t.includes('güvenlik')) {
            if (!document.querySelector('#product-name') && !document.querySelector('.product-name')) return 'BLOCKED';
        }
        if (document.querySelector('h1') === null && document.querySelector('img') === null) return 'NOT_LOADED';
        return 'OK';
    """)
    print("UC Status:", hata_durumu)
    if hata_durumu == "OK":
        price = driver.execute_script("""
            var el = document.querySelector('[data-test-id="price-current-price"]');
            return el ? el.innerText : 'No price';
        """)
        print("SUCCESS! Price:", price)
    
    driver.quit()

test_uc()

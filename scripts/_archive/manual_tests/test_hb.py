import re
import requests

def test_hb_fallback(url):
    print(f"Testing URL: {url}")
    sku_match = re.search(r'-p-([A-Z0-9]+)', url)
    if sku_match:
        sku = sku_match.group(1)
        print(f"Extracted SKU: {sku}")
        api_url = f"https://www.hepsiburada.com/product-detail/{sku}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "tr-TR,tr;q=0.9",
        }
        
        hb_data = None
        
        # Direct product detail API
        try:
            resp = requests.get(api_url, headers=headers, timeout=10)
            print(f"Desktop API Status: {resp.status_code}")
            if resp.status_code == 200:
                hb_data = resp.json()
                print("Got JSON from Desktop API")
            else:
                print(resp.text[:100])
        except Exception as e:
            print("Desktop API failed:", e)
            
        # Try mobile API
        if not hb_data:
            try:
                mobile_url = f"https://api.hepsiburada.com/product/detail/{sku}"
                resp = requests.get(mobile_url, headers=headers, timeout=10)
                print(f"Mobile API Status: {resp.status_code}")
                if resp.status_code == 200:
                    hb_data = resp.json()
                    print("Got JSON from Mobile API")
                else:
                    print(resp.text[:100])
            except Exception as e:
                print("Mobile API failed:", e)
                
        if hb_data and isinstance(hb_data, dict):
            print("Data parsing...")
            api_name = hb_data.get("name") or hb_data.get("productName")
            print("Name:", api_name)
            
            price_val = None
            listing = hb_data.get("currentListing") or hb_data.get("listing") or {}
            if listing:
                price_obj = listing.get("price") or {}
                price_val = price_obj.get("value") or price_obj.get("amount")
            elif hb_data.get("price") and isinstance(hb_data["price"], dict):
                price_val = hb_data["price"].get("value") or hb_data["price"].get("amount")
            elif hb_data.get("currentPrice") and isinstance(hb_data["currentPrice"], dict):
                price_val = hb_data["currentPrice"].get("value") or hb_data["currentPrice"].get("amount")
            elif hb_data.get("price"):
                price_val = hb_data.get("price")
                
            print("Price:", price_val)
        else:
            print("No valid hb_data dict retrieved.")
    else:
        print("No SKU matched.")

    # Review Cloudscraper fallback test
    print("\nTesting Review Cloudscraper Fallback...")
    import cloudscraper
    from bs4 import BeautifulSoup
    import json
    try:
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': True, 'platform': 'android'})
        mobile_headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9"
        }
        base_url = url.split('?')[0].replace("-pm-", "-p-").replace("-c-", "-p-")
        review_url = base_url + "-yorumlari" if not url.endswith("-yorumlari") else url
        print("Fetching:", review_url)
        resp = scraper.get(review_url, headers=mobile_headers, timeout=20)
        print("Scraper Status:", resp.status_code)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'lxml')
            reviews = []
            for script in soup.find_all('script', {'id': '__NEXT_DATA__'}):
                print("Found __NEXT_DATA__")
            for p in soup.find_all(['p', 'span', 'div']):
                if p.has_attr('itemprop') and p['itemprop'] == 'reviewBody':
                    txt = p.get_text(strip=True)
                    if len(txt) > 25: reviews.append(txt)
            print("Extracted reviews count:", len(reviews))
        else:
            print(resp.text[:100])
    except Exception as e:
         print("Cloudscraper error:", e)

# Test with a dummy URL from the user's screenshot maybe?
url = "https://www.hepsiburada.com/tefal-toast-expert-4-dilim-kapasiteli-1800-watt-izgara-ve-tost-makinesi-inox-p-HBV000002E49W"
test_hb_fallback(url)

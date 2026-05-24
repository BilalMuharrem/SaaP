import os
import ssl
import time
import re
import json
import csv
import urllib.request
import platform
from datetime import datetime
import webbrowser
from urllib.parse import urlparse

# --- LAZY IMPORTS ---
# undetected_chromedriver and tkinter are only loaded when actually needed
# (i.e., when running the desktop GUI app). The Celery worker only imports
# utility functions from this module, so a top-level import of uc would
# trigger ChromeDriver auto-download and a version-mismatch crash.
def _get_uc():
    import undetected_chromedriver as uc
    return uc

try:
    import tkinter as tk
    from tkinter import messagebox
    from tkinter import LabelFrame
    _HAS_TK = True
except ImportError:
    _HAS_TK = False

from groq import Groq

# --- 1. SECURITY & SSL ---
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context


# --- 2. SHARED CORE ENGINE ---
def fiyati_temizle(fiyat_str):
    try:
        if not fiyat_str:
            return 0.0

        # Step 1: Extract a proper Turkish-format price from the RAW text.
        # Running on the original string preserves spaces/letters as natural boundaries,
        # preventing concatenation of multiple prices or stray digits.
        # Matches: 26.499,24 | 1.350,00 | 135,13 | 829,99 | 5000,99
        tr_match = re.search(r'(\d+(?:\.\d{3})*,\d{2})', fiyat_str)
        if tr_match:
            price_str = tr_match.group(1)
            price_str = price_str.replace('.', '').replace(',', '.')
            return float(price_str)

        # Step 2: Fallback for non-standard formats
        temiz = re.sub(r'[^\d.,]', '', fiyat_str)
        if not temiz:
            return 0.0
        match = re.search(r'[,.](\d{1,2})$', temiz)
        if match:
            decimal_part = match.group(1)
            integer_part = re.sub(r'[,.]', '', temiz[:-len(decimal_part) - 1])
            return float(f"{integer_part}.{decimal_part}")
        else:
            temiz = re.sub(r'[,.]', '', temiz)
            return float(temiz)
    except:
        return 0.0


def standard_fiyat_formati(sayi_float):
    if sayi_float <= 0:
        return "Fiyat Bilgisi Yok"
    try:
        formatted_str = "{:,.2f}".format(sayi_float)
        table = str.maketrans({',': '.', '.': ','})
        final_str = formatted_str.translate(table)
        return f"{final_str} TL"
    except:
        return "Hata"


def urun_ismi_temizle(isim):
    if not isim:
        return "İsim Bulunamadı"
    junk_words = [r"\bfiyatı\b", r"\bfiyatları\b", r"\byorumları\b", r"\byorum\b", r"\bözellikleri\b", r"\bsatın al\b"]
    for j in junk_words:
        isim = re.sub(j, "", isim, flags=re.IGNORECASE)
    return isim.strip(' ,-|')


def marka_adi_bul(url):
    """FAZ 3: Türkiye marketplace canonical isimleri.
    Bilinen platformlar için sabit Türkçe etiket; geri kalan için domain kapitalizasyon."""
    try:
        domain = urlparse(url).netloc.replace("www.", "").lower()
        # Bilinen marketplace eşlemeleri (kanonik isim)
        platform_map = {
            'trendyol.com':     'Trendyol',
            'hepsiburada.com':  'Hepsiburada',
            'n11.com':          'N11',
            'ciceksepeti.com':  'Çiçeksepeti',
            'pttavm.com':       'PttAVM',
            'amazon.com.tr':    'Amazon',
            'amazon.com':       'Amazon',
        }
        for needle, canonical in platform_map.items():
            if needle in domain:
                return canonical
        if "amzn.eu" in domain:
            return "Amazon"
        return domain.split('.')[0].capitalize()
    except:
        return "Bilinmeyen Site"


def get_domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except:
        return ""


def ac_rapor_tarayicida(rapor_yolu):
    if platform.system() == 'Darwin':
        os.system(f'open "{rapor_yolu}"')
    elif platform.system() == 'Windows':
        os.startfile(rapor_yolu)
    else:
        rapor_url = "file:" + urllib.request.pathname2url(rapor_yolu)
        webbrowser.open(rapor_url)


BANNED_UI_PHRASES = [
    "yayımı reddedilebilmekte", "sağlık beyanı", "tıbbi öneri", "cayma hakkı",
    "sözleşmenin feshi", "deneyimlerini paylaşabilecekleri", "gecikmeksizin bildirilecektir",
    "hukuka aykırı", "kriterlere aykırı", "değerlendirme yap", "ilk değerlendiren",
    "değerlendirme bulunmuyor", "satıcıya sor", "iade koşulları", "ürün açıklaması",
    "taksit seçenekleri", "sponsorlu", "sipariş iptali", "taksit", "kargo",
    "satın aldıkları ürünlere", "sepete ekle", "tümünü gör", "ücretsiz kargo",
    "yarın kapında", "ortalama puan", "veri işleme", "aydınlatma metni", "kapat",
    "favorilere ekle", "ürün özellikleri", "ürün değerlendirmeleri", "yardımcı oldu mu", "evet", "hayır", "yanıtla",
    "tükendi", "tükenmek üzere", "ödeme koruma", "gelince haber ver", "satıcıya git", "teslimat adresi", "satın al"
]


# =========================================================================
# 💰 MODULE 1: PRICE & COMPETITOR RADAR
# =========================================================================
def run_price_radar(urls, api_key, durum_etiketi, pencere):
    durum_etiketi.config(text="Status: Initiating Price Radar...", fg="#e67e22")
    pencere.update()

    client = Groq(api_key=api_key) if len(api_key) > 15 else None
    sonuclar = []
    referans_url = urls[0]

    for idx, original_url in enumerate(urls):
        url = original_url.strip()
        if not url:
            continue

        platform_name = marka_adi_bul(url)
        durum_etiketi.config(text=f"Status: Analyzing Price on {platform_name} ({idx + 1}/{len(urls)})...",
                             fg="#3498db")
        pencere.update()

        driver = None
        fiyat_bulundu = "Bulunamadı"
        spesifik_urun_ismi = "İsim Bulunamadı"
        hata_durumu = "OK"

        try:
            uc = _get_uc()
            options = uc.ChromeOptions()
            options.add_argument("--disable-notifications")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-gpu")
            driver = uc.Chrome(options=options)
            driver.set_page_load_timeout(120)

            try:
                driver.get(url)
            except Exception as e:
                if "timeout" in str(e).lower():
                    driver.execute_script("window.stop();")

            time.sleep(6)

            for _ in range(2):
                hata_durumu = driver.execute_script("""
                    var t = document.title.toLowerCase().trim(); 
                    var b = document.body.innerText.toLowerCase();
                    if (t.includes('robot') || t.includes('captcha') || b.includes('robot musunuz') || t === 'hepsiburada.com') {
                        if (!document.querySelector('#product-name') && !document.querySelector('.product-name')) return 'BLOCKED';
                    }
                    if (document.querySelector('h1') === null && document.querySelector('img') === null) return 'NOT_LOADED';
                    return 'OK';
                """)
                if hata_durumu == 'BLOCKED' or hata_durumu == 'NOT_LOADED':
                    time.sleep(3)
                    driver.refresh()
                    time.sleep(6)
                else:
                    break

            if hata_durumu == "OK":
                raw_isim = driver.execute_script("""
                    var domain = window.location.hostname;
                    if (domain.includes('trendyol.com')) { var el = document.querySelector('.pr-new-br h1'); if (el) return el.innerText; }
                    if (domain.includes('hepsiburada.com')) { var el = document.querySelector('#product-name'); if (el) return el.innerText; }
                    if (domain.includes('amazon.')) { var el = document.querySelector('#productTitle'); if (el) return el.innerText; }
                    return document.title.split('|')[0].split('-')[0].trim();
                """)
                spesifik_urun_ismi = urun_ismi_temizle(raw_isim)

                try:
                    driver.execute_script(
                        "document.querySelectorAll('.modal, .popup, [id*=\"onetrust\"]').forEach(el => el.style.display='none');")
                except:
                    pass

                fiyat_bulundu = driver.execute_script("""
                    var domain = window.location.hostname;

                    if (domain.includes('trendyol.com')) {
                        // 1) İndirimli Sepet Fiyatı / Trendyol Plus Fiyatı ("Sepette X TL" - bu en gerçekçi fiyattır)
                        var basketPrice = document.querySelector('.product-price-container .basket-discount, .pr-bx-w .basket-discount, .basket-price, .product-price-container .discounted-price, [data-testid="basket-price"]');
                        if (basketPrice && basketPrice.offsetParent !== null) { 
                            var t = basketPrice.innerText.replace(/Sepette/i, '').trim().split('\\n')[0].trim(); 
                            if(t && /\\d/.test(t)) return t; 
                        }

                        // 2) Normal indirimli fiyat (Üzeri çizilmiş fiyatın altındaki normal fiyat)
                        var el = document.querySelector('.prc-dsc, span.prc-dsc, .product-price-container .prc-dsc');
                        if (el && el.offsetParent !== null) { 
                            var t = el.innerText.trim().split('\\n')[0].trim(); 
                            if(t && /\\d/.test(t)) return t; 
                        }

                        // 3) Lowest price block (Eski fiyat üstte, yeni fiyat alttadır)
                        var lpBtn = document.querySelector('button.lowest-price, .lowest-price');
                        if (lpBtn && lpBtn.offsetParent !== null) { 
                            var spans = lpBtn.querySelectorAll('span'); 
                            if (spans.length > 0) { 
                                var t = spans[spans.length-1].innerText.trim(); 
                                if (t && /\\d/.test(t)) return t; 
                            } 
                        }
                    }

                    if (domain.includes('hepsiburada.com')) {
                        // Sadece ana ürün bölümündeki (soldaki büyük alan) fiyatları ara. Sağdaki "Diğer satıcılar" (other sellers) kısmını yoksay.
                        // 1) Sepet Fiyatı (Eğer varsa en ucuzudur)
                        var basketPrice = document.querySelector('#product-price .basket-price, .product-price-wrapper .basket-price, [data-test-id="price-basket-price"]');
                        if (basketPrice && basketPrice.offsetParent !== null) {
                            var t = basketPrice.innerText.replace(/Sepette/i, '').trim().split('\\n')[0].trim();
                             if(t && /\\d/.test(t)) return t;
                        }

                        // 2) Ana Fiyat
                        var mainPrice = document.querySelector('[data-test-id="price-current-price"], #offering-price');
                        if (mainPrice && mainPrice.offsetParent !== null) { 
                            var t = mainPrice.innerText.trim().split('\\n')[0].trim(); 
                            if(t && /\\d/.test(t)) return t; 
                        }
                    }

                    if (domain.includes('amazon.')) {
                        var el = document.querySelector('#corePriceDisplay_desktop_feature_div .a-price .a-offscreen, #corePrice_desktop .a-price .a-offscreen');
                        if (el && el.offsetParent !== null) { var t = el.innerText.trim().split('\\n')[0].trim(); if(t) return t; }
                    }

                    // Generic fallback: find largest non-strikethrough price on page
                    var max_size = 0; 
                    var best_price = 'Bulunamadı';
                    document.querySelectorAll('*').forEach(el => {
                        if (el.offsetParent !== null && el.children.length === 0) {
                            var txt = (el.innerText || el.textContent || '').trim();
                            if (txt && txt.length < 30 && /\\d/.test(txt) && (txt.includes('TL') || txt.includes('₺'))) {
                                var style = window.getComputedStyle(el);
                                if (style.textDecorationLine !== 'line-through') {
                                    var size = parseFloat(style.fontSize);
                                    if (size > max_size) { 
                                        max_size = size; 
                                        best_price = txt; 
                                    }
                                }
                            }
                        }
                    });
                    return best_price;
                """)

            # Fallback for HepsiBurada using the public API if UI scraping failed or was blocked
            if "hepsiburada.com" in url and (hata_durumu == "BLOCKED" or fiyat_bulundu == "Bulunamadı" or fiyat_bulundu == None):
                try:
                    import re, requests
                    sku_match = re.search(r'-p-([A-Z0-9]+)', url)
                    if sku_match:
                        sku = sku_match.group(1)
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
                            if resp.status_code == 200:
                                hb_data = resp.json()
                        except:
                            pass
                            
                        # Try mobile API
                        if not hb_data:
                            try:
                                mobile_url = f"https://api.hepsiburada.com/product/detail/{sku}"
                                resp = requests.get(mobile_url, headers=headers, timeout=10)
                                if resp.status_code == 200:
                                    hb_data = resp.json()
                            except:
                                pass
                                
                        if hb_data and isinstance(hb_data, dict):
                            # Try to extract name
                            api_name = hb_data.get("name") or hb_data.get("productName")
                            if api_name and spesifik_urun_ismi == "İsim Bulunamadı":
                                spesifik_urun_ismi = api_name
                                
                            # Try to extract price
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
                                
                            if price_val:
                                fiyat_bulundu = str(price_val)
                                hata_durumu = "OK"  # Resurrected by API fallback
                except Exception as api_err:
                    print("HB API Fallback error:", api_err)

            sonuclar.append({
                "Platform": platform_name,
                "UrunAdi": spesifik_urun_ismi,
                "RawFiyat": fiyat_bulundu.strip() if fiyat_bulundu else "Bulunamadı",
                "CleanFiyat": fiyati_temizle(fiyat_bulundu) if fiyat_bulundu else 0.0,
                "URL": url,
                "Durum": hata_durumu
            })

        except Exception as e:
            sonuclar.append({
                "Platform": platform_name,
                "UrunAdi": "Hata",
                "RawFiyat": "Hata",
                "CleanFiyat": 0.0,
                "URL": url,
                "Durum": "ERROR"
            })
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass

    ai_ozet = ""
    analiz_gerekenler = []

    if sonuclar:
        for s in sonuclar:
            if s["URL"] == referans_url and s["Durum"] == "OK":
                analiz_gerekenler.append(f"🎯 REFERANS ({s['Platform']}): {s['CleanFiyat']} TL - {s['UrunAdi']}")
                break
        for s in sonuclar:
            if s["URL"] != referans_url and s["Durum"] == "OK":
                analiz_gerekenler.append(f"🔗 COMPETITOR ({s['Platform']}): {s['CleanFiyat']} TL - {s['UrunAdi']}")

    if client and analiz_gerekenler:
        durum_etiketi.config(text="Status: AI Assistant analyzing...", fg="#9b59b6")
        pencere.update()
        prompt = f"Analyze pricing. Verify if products match via titles. Write 2-3 sentence Turkish executive summary.\nData: {' | '.join(analiz_gerekenler)}"
        try:
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b-versatile",
                temperature=0.3,
                max_tokens=300
            )
            ai_ozet = response.choices[0].message.content.strip()
        except:
            ai_ozet = "⚠️ AI Analizi Başarısız."

    if sonuclar:
        durum_etiketi.config(text="Status: Generating Price Dashboard...", fg="#e67e22")
        pencere.update()
        resmi_fiyat = 0.0

        for s in sonuclar:
            if s["URL"] == referans_url and s["Durum"] == "OK":
                resmi_fiyat = s["CleanFiyat"]
                break

        html_satirlar = ""
        for s in sonuclar:
            platform_name = s["Platform"]
            guncel_fiyat_float = s["CleanFiyat"]
            final_formatted_price = standard_fiyat_formati(guncel_fiyat_float)
            durum_etiketi_ui = "✅ EŞİT"
            renk = "#3b82f6"

            if s["Durum"] == "BLOCKED" or s["Durum"] == "ERROR":
                durum_etiketi_ui = "⛔ ENGEL"
                renk = "#ef4444"
                final_formatted_price = "Erişim Reddedildi"
            elif s["Durum"] == "NOT_LOADED" or s["RawFiyat"] == "Bulunamadı":
                durum_etiketi_ui = "⚠️ OKUNAMADI"
                renk = "#a1a1aa"
                final_formatted_price = "Bulunamadı"
            else:
                if s["URL"] != referans_url:
                    if guncel_fiyat_float <= 0:
                        durum_etiketi_ui = "⚠️ OKUNAMADI"
                        renk = "#a1a1aa"
                    elif guncel_fiyat_float < resmi_fiyat:
                        fark = resmi_fiyat - guncel_fiyat_float
                        durum_etiketi_ui = f"🔻 DÜŞÜK (-{standard_fiyat_formati(fark)})"
                        renk = "#ef4444"
                    elif guncel_fiyat_float > resmi_fiyat:
                        fark = guncel_fiyat_float - resmi_fiyat
                        durum_etiketi_ui = f"🔺 YÜKSEK (+{standard_fiyat_formati(fark)})"
                        renk = "#34d399"

            html_card = f"""
            <div style="background: rgba(24,24,27,0.55); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 20px; margin-bottom: 15px; display: grid; grid-template-columns: 1fr 200px 220px; gap: 20px; align-items: center; border-left: 4px solid {renk if s['URL'] != referans_url else '#818cf8'};">
                <div style="display:flex; flex-direction:column; gap:6px;">
                    <div style="font-weight:800; font-size:19px; color:{'#818cf8' if s['URL'] == referans_url else '#fff'};">
                        🎯 {platform_name} {'<span style="font-size:11px; color:#a1a1aa;">(BAZ ALINAN)</span>' if s['URL'] == referans_url else ''}
                    </div>
                    <a href='{s['URL']}' target='_blank' style='background:rgba(255,255,255,0.05); padding:6px 12px; border-radius:6px; color:#fff; text-decoration:none; font-size:12px; font-weight:bold; width:max-content;'>Ürüne Git ➔</a>
                    <div style="font-size:13px; color:#d1d5db; line-height:1.4;">{s['UrunAdi']}</div>
                </div>
                <div style="font-size:24px; font-weight:800; color:#fff; text-align:left;">{final_formatted_price}</div>
                <div style="text-align:right;">
                    <span style="font-size:13px; font-weight:bold; color:{renk if s['URL'] != referans_url else '#818cf8'}; background:{renk if s['URL'] != referans_url else '#818cf8'}15; padding:6px 12px; border-radius:6px;">{durum_etiketi_ui}</span>
                </div>
            </div>"""
            html_satirlar += html_card

        ai_html = ""
        if ai_ozet:
            ai_html = f"""
            <div style='background:rgba(129,140,248,0.05); border:1px solid rgba(129,140,248,0.2); border-left:4px solid #818cf8; border-radius:12px; padding:25px; margin-bottom:30px; color:#e4e4e7; line-height:1.6;'>
                <b style='color:#818cf8;'>🤖 AI Strateji:</b><br>{ai_ozet}
            </div>"""

        current_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        html_icerik = f"""
        <html>
        <head>
            <meta charset="utf-8">
            <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;700;800&display=swap" rel="stylesheet">
            <style>
                body {{ font-family: 'Plus Jakarta Sans', sans-serif; background: #09090b; color: #f8fafc; padding: 40px; margin:0; }} 
                .container {{ max-width: 1000px; margin: auto; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div style="color:#818cf8; font-weight:bold; letter-spacing:2px; font-size:13px; margin-bottom:5px;">BMK VERİ ODAKLI DANIŞMANLIK HİZMETİ</div>
                <h1 style="margin:0 0 30px 0; font-size:36px;">Ürün Ücret Karşılaştırma Raporu</h1>
                {ai_html}
                {html_satirlar}
                <div style="text-align:center; margin-top:40px; color:#52525b; font-size:14px;">
                    Rapor Tarihi: {current_time}
                </div>
            </div>
        </body>
        </html>"""

        rapor_yolu = os.path.abspath("BMK_Price_Report.html")
        with open(rapor_yolu, "w", encoding="utf-8") as f:
            f.write(html_icerik)

        ac_rapor_tarayicida(rapor_yolu)
        durum_etiketi.config(text="Status: Success! Price Dashboard Opened.", fg="green")
    else:
        durum_etiketi.config(text="Status: Error! No data.", fg="red")


# =========================================================================
# 🗣️ MODULE 2: REVIEW RADAR (V98 - THE TRUE GOLD BUILD)
# =========================================================================
def run_review_radar(urls, api_key, durum_etiketi, pencere):
    durum_etiketi.config(text="Status: Initiating Reputation Radar...", fg="#e67e22")
    pencere.update()

    client = Groq(api_key=api_key) if len(api_key) > 15 else None
    sonuclar = []

    for idx, original_url in enumerate(urls):
        url = original_url.strip()
        if not url:
            continue

        platform_name = marka_adi_bul(url)
        domain = get_domain(url)
        is_trendyol = "trendyol.com" in url
        is_hepsiburada = "hepsiburada.com" in url

        if not (is_trendyol or is_hepsiburada):
            continue

        durum_etiketi.config(text=f"Status: Analyzing Reviews on {platform_name} ({idx + 1}/{len(urls)})...",
                             fg="#3498db")
        pencere.update()

        driver = None
        satici_ismi = "Bulunamadı"
        spesifik_urun_ismi = "İsim Bulunamadı"
        yorum_ozeti = ""
        incelenen_yorum_sayisi = 0
        olumlu_sayisi = 0
        olumsuz_sayisi = 0
        hata_durumu = "OK"
        raw_data_set = set()

        try:
            uc = _get_uc()
            options = uc.ChromeOptions()
            options.add_argument("--disable-notifications")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-gpu")
            driver = uc.Chrome(options=options)
            driver.set_page_load_timeout(120)

            try:
                driver.get(url)
            except Exception as e:
                if "timeout" in str(e).lower():
                    driver.execute_script("window.stop();")

            time.sleep(6)

            # --- SHIELD BREAKER CHECK ---
            for _ in range(2):
                hata_durumu = driver.execute_script("""
                    var t = document.title.toLowerCase().trim(); 
                    var b = document.body.innerText.toLowerCase();
                    if (t.includes('robot') || t.includes('captcha') || b.includes('robot musunuz') || t === 'hepsiburada.com') {
                        if (!document.querySelector('#product-name') && !document.querySelector('.product-name')) {
                            return 'BLOCKED';
                        }
                    }
                    return 'OK';
                """)

                if hata_durumu == 'BLOCKED':
                    time.sleep(3)
                    driver.refresh()
                    time.sleep(7)
                else:
                    break

            if hata_durumu == "OK":
                try:
                    driver.execute_script(
                        "document.querySelectorAll('.modal, .popup, [id*=\"onetrust\"], .cookie-banner').forEach(el => el.style.display='none');")
                except:
                    pass

                # --- EXTRACT NAMES BEFORE NAVIGATION ---
                try:
                    raw_isim = driver.execute_script("""
                        var domain = window.location.hostname;
                        if (domain.includes('trendyol.com')) { var el = document.querySelector('.pr-new-br h1, h1.product-name'); if (el) return el.innerText; }
                        if (domain.includes('hepsiburada.com')) { var el = document.querySelector('#product-name, h1[itemprop="name"]'); if (el) return el.innerText; }
                        var h1 = document.querySelector('h1');
                        if (h1) return h1.innerText;
                        return document.title.split('|')[0].split('-')[0].trim();
                    """)
                    spesifik_urun_ismi = urun_ismi_temizle(raw_isim)
                except:
                    spesifik_urun_ismi = "İsim Bulunamadı"

                try:
                    satici_ismi = driver.execute_script("""
                        var s = '';
                        var ty = document.querySelector('.merchant-box a, .seller-store a, .merchant-text, .seller-name, [data-testid="merchant-name"]');
                        if(ty) s = ty.innerText.trim();
                        if(!s) { var hb = document.querySelector('.merchantLink, a[href*="/magaza/"]'); if(hb) s = hb.innerText.trim(); }
                        if(!s || s.length < 2) {
                            var brand = document.querySelector('.pr-new-br a, .pr-new-br span, .brand-name, .product-brand, .product-brand-name');
                            if(brand && brand.innerText.trim().length > 1) { s = "Marka: " + brand.innerText.trim(); }
                        }
                        if (s) { s = s.split('\\n')[0].replace(/[0-9]+,[0-9]+.*/g, '').replace(/Takip et/gi, '').replace(/Satıcıya sor/gi, '').replace(/Değerlendirme/gi, '').trim().replace(/\\s+[0-9]+$/, ''); }
                        return (s && s.length > 1) ? s : 'Platform Satıcısı';
                    """)
                except:
                    satici_ismi = "Platform Satıcısı"

                # --- V84 TELEPORTATION LOGIC (RESTORED) ---
                # We strip query params and manually navigate. This successfully pulled the 160 reviews.
                if is_trendyol:
                    if "/yorumlar" not in driver.current_url:
                        try:
                            driver.get(driver.current_url.split('?')[0] + "/yorumlar")
                            time.sleep(6)
                            # Initial scroll to trigger lazy-loaded reviews
                            driver.execute_script("window.scrollBy(0, 2000);")
                            time.sleep(2)
                        except:
                            pass
                elif is_hepsiburada:
                    # Step 1: Scroll to reviews section on main page first
                    try:
                        driver.execute_script("""
                            var reviewSection = document.querySelector(
                                '[id*="review"], [id*="yorum"], [id*="Rating"], ' +
                                '[data-test-id*="review"], [data-test-id*="rating"], ' +
                                '[class*="hermes-ReviewCard"], [class*="review-section"], ' +
                                '[class*="rnr-"], [class*="ReviewSummary"]'
                            );
                            if (reviewSection) {
                                reviewSection.scrollIntoView({behavior: 'smooth', block: 'start'});
                            } else {
                                window.scrollBy(0, 3500);
                            }
                        """)
                        time.sleep(2)
                    except:
                        pass

                    # Step 2: Click the review tab (only the FIRST match to avoid double-click issues)
                    try:
                        driver.execute_script("""
                            var clicked = false;
                            document.querySelectorAll('a, button, div[role="tab"], li[role="tab"], span[role="tab"]').forEach(el => {
                                if(clicked) return;
                                if(el.offsetParent === null) return;
                                var txt = (el.innerText || "").toLowerCase().trim();
                                if(txt.length < 60 && !txt.includes('yap') && !txt.includes('soru') &&
                                   (txt.includes('değerlendirme') || txt.includes('yorumlar'))) {
                                    try { el.scrollIntoView({block:'center'}); el.click(); clicked = true; } catch(e){}
                                }
                            });
                        """)
                        time.sleep(3)
                    except:
                        pass

                    # Step 3: If not on reviews, try URL navigation
                    if "-yorumlari" not in driver.current_url and "yorum" not in driver.current_url.lower():
                        base_url = driver.current_url.split('?')[0]
                        base_url = base_url.replace("-pm-", "-p-").replace("-c-", "-p-")
                        try:
                            driver.get(base_url + "-yorumlari")
                            time.sleep(5)
                        except:
                            pass

                # --- LOGIN TRAP ESCAPE ---
                # If the teleport triggered a login screen, retreat immediately to the main page!
                if "giris" in driver.current_url.lower() or "login" in driver.current_url.lower():
                    try:
                        driver.get(url)
                        time.sleep(5)
                        # Scroll aggressively to reach the reviews section on main page
                        driver.execute_script("""
                            var reviewSection = document.querySelector(
                                '[id*="review"], [id*="yorum"], [data-test-id*="review"], ' +
                                '[class*="hermes-ReviewCard"], [class*="review-section"], [class*="rnr-"]'
                            );
                            if (reviewSection) {
                                reviewSection.scrollIntoView({behavior: 'smooth', block: 'start'});
                            } else {
                                window.scrollBy(0, 4000);
                            }
                        """)
                        time.sleep(2)
                    except:
                        pass

                # --- EXTRACTION LOOP ---
                for step in range(25):
                    # FALLBACK: If Trendyol /yorumlar page yielded nothing after 5 attempts,
                    # go back to main product page and try extracting reviews from there.
                    if step == 5 and len(raw_data_set) == 0 and is_trendyol and "/yorumlar" in driver.current_url:
                        try:
                            driver.get(url)
                            time.sleep(5)
                            driver.execute_script("window.scrollBy(0, 3000);")
                            time.sleep(2)
                        except:
                            pass

                    # Trendyol uses infinite scroll; scroll to bottom aggressively
                    if is_trendyol:
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    else:
                        driver.execute_script("window.scrollBy(0, 1500);")

                    # Trendyol: Expand truncated reviews by clicking "Devamını Oku" links
                    if is_trendyol:
                        try:
                            driver.execute_script("""
                                document.querySelectorAll('a, button, span').forEach(el => {
                                    var txt = (el.innerText || "").toLowerCase().trim();
                                    if (txt === 'devamını oku' || txt === 'devamini oku' || txt === 'daha fazla' ||
                                        (el.className && el.className.toLowerCase().includes('read-more'))) {
                                        try { el.click(); } catch(e) {}
                                    }
                                });
                            """)
                        except:
                            pass

                    # The V84 Safe Pagination Clicker
                    js_click = f"""
                        var targetPage = '{(step + 2)}'; 
                        var clicked = false;
                        document.querySelectorAll('button, a, div, span, li').forEach(b => {{
                            if(clicked) return;
                            if(b.offsetParent === null) return;

                            var t = (b.innerText || "").toLowerCase().trim();
                            var c = (b.className || "").toLowerCase();
                            var p = (b.parentElement?.className || "").toLowerCase();

                            // SAFEGUARD: Do not click buttons that trigger login or unrelated actions
                            if(t.includes('yap') || t.includes('soru') || t.includes('giriş') || t.includes('kayıt')) return;

                            // Load more / next page buttons (use includes for flexible matching)
                            if(t.includes('daha fazla') || t === 'tüm yorumları gör' || t === 'sonraki' || t === 'devamını gör' || t === 'ileri' || t === '>' || t.includes('daha fazla yorum') || t.includes('daha fazla değerlendirme') || t === 'load more') {{ 
                                try {{ b.click(); clicked = true; }} catch(e){{}} 
                            }} 
                            else if (t === targetPage && (c.includes('page') || c.includes('pagination') || p.includes('pagination'))) {{ 
                                try {{ b.click(); clicked = true; }} catch(e){{}} 
                            }}
                        }});
                        // Fallback: try data-test-id based selectors for HB load more buttons
                        if(!clicked) {{
                            var loadMoreBtn = document.querySelector('[data-test-id*="show-more"], [data-test-id*="load-more"], [class*="showMore"], [class*="load-more"], [class*="loadMore"]');
                            if(loadMoreBtn && loadMoreBtn.offsetParent !== null) {{
                                try {{ loadMoreBtn.click(); }} catch(e){{}}
                            }}
                        }}
                    """
                    try:
                        driver.execute_script(js_click)
                    except:
                        pass

                    time.sleep(1.5)

                    # The True Vision Extractor — with Trendyol-specific selectors
                    js_extractor = """
                        var res = [];
                        var domain = window.location.hostname;
                        var isTrendyol = domain.includes('trendyol.com');
                        var isReviewPage = window.location.href.includes('yorumlar') || window.location.href.includes('-yorumlari');

                        if (isTrendyol) {
                            // Trendyol-specific: target comment text containers directly
                            var trendyolSelectors = [
                                '.comment-text',
                                '.comment-content', 
                                '[class*="comment-text"]',
                                '[class*="comment-content"]',
                                '[class*="CommentText"]',
                                '[itemprop="reviewBody"]',
                                '[itemprop="description"]'
                            ];
                            var foundViaSelectors = false;
                            trendyolSelectors.forEach(sel => {
                                document.querySelectorAll(sel).forEach(el => {
                                    if (el.offsetParent !== null) {
                                        var txt = el.innerText.trim();
                                        if (txt.length > 15 && txt.length < 2000) {
                                            foundViaSelectors = true;
                                            res.push(txt);
                                        }
                                    }
                                });
                            });
                            // Fallback: walk through all visible text blocks on the review page
                            if (!foundViaSelectors && isReviewPage) {
                                document.querySelectorAll('div, p, span').forEach(el => {
                                    if (el.offsetParent !== null && el.children.length <= 2) {
                                        var txt = el.innerText.trim();
                                        // Filter for likely review text: moderate length, not UI chrome
                                        if (txt.length > 25 && txt.length < 1500 && !txt.includes('Satıcı:') &&
                                            !txt.includes('Beğen') && !txt.includes('Şikayet Et') &&
                                            !/^\d+ kişi/.test(txt) && !/^[A-ZÇĞİÖŞÜ]\*\*/.test(txt)) {
                                            res.push(txt);
                                        }
                                    }
                                });
                            }
                        } else if (isReviewPage) {
                            document.querySelectorAll('p, span, div.comment-text, div[itemprop="reviewBody"]').forEach(el => {
                                if (el.offsetParent !== null) { 
                                    var txt = el.innerText.trim();
                                    if(txt.length > 25 && txt.length < 2000) {
                                        res.push(txt);
                                    }
                                }
                            });
                        } else {
                            var strictNodes = document.querySelectorAll('.comment-text, .rnr-com-tx, [itemprop="reviewBody"], div[class*="ReviewCard"] p, div[class*="ReviewCard"] span, div[class*="hermes-"] p, div[class*="hermes-"] span, #reviewsTabContent p, #reviewsTabContent span, [data-test-id*="review"] p, [data-test-id*="review"] span, [class*="review-text"], [class*="ReviewText"], [class*="review-body"], [class*="ReviewBody"], [class*="comment-content"], [class*="CommentContent"], [class*="rnr-"] p, [class*="rnr-"] span, div[class*="review"] p, div[class*="review"] span');
                            strictNodes.forEach(el => {
                                if (el.offsetParent !== null) {
                                    var txt = el.innerText.trim();
                                    if(txt.length > 25 && txt.length < 2000) {
                                        res.push(txt);
                                    }
                                }
                            });
                        }
                        return res;
                    """
                    try:
                        page_texts = driver.execute_script(js_extractor)
                        if page_texts:
                            for text in page_texts:
                                clean_text = " ".join(text.split())
                                if len(clean_text.split()) >= 5 and clean_text not in raw_data_set:
                                    if not any(banned in clean_text.lower() for banned in BANNED_UI_PHRASES):
                                        raw_data_set.add(clean_text)
                    except:
                        pass

            # Fallback for HepsiBurada Reviews using Cloudscraper
            if is_hepsiburada and hata_durumu == "BLOCKED":
                try:
                    import cloudscraper
                    from bs4 import BeautifulSoup
                    import json
                    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': True, 'platform': 'android'})
                    mobile_headers = {
                        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
                        "Accept-Language": "tr-TR,tr;q=0.9"
                    }
                    # Try to hit the reviews endpoint directly by modifying URL
                    base_url = url.split('?')[0].replace("-pm-", "-p-").replace("-c-", "-p-")
                    review_url = base_url + "-yorumlari" if not url.endswith("-yorumlari") else url
                    
                    resp = scraper.get(review_url, headers=mobile_headers, timeout=20)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, 'lxml')
                        
                        # Try to get product name
                        for script in soup.find_all('script', {'id': '__NEXT_DATA__'}):
                            try:
                                state = json.loads(script.string)
                                prod = state.get('props', {}).get('pageProps', {}).get('product', {})
                                if prod:
                                    if spesifik_urun_ismi == "İsim Bulunamadı":
                                        spesifik_urun_ismi = prod.get("name", spesifik_urun_ismi)
                                        hata_durumu = "OK"  # Partially unblocked!
                            except: pass
                            
                        # Try to get reviews from HTML elements directly
                        for p in soup.find_all(['p', 'span', 'div']):
                            try:
                                if p.has_attr('itemprop') and p['itemprop'] == 'reviewBody':
                                    txt = p.get_text(strip=True)
                                    if len(txt) > 25 and len(txt) < 2000:
                                        raw_data_set.add(txt)
                                        hata_durumu = "OK"
                                elif p.has_attr('class') and any('ReviewCard' in c for c in p['class']):
                                    # Very heuristic for mobile view
                                    if len(p.get_text(strip=True)) > 25:
                                        raw_data_set.add(p.get_text(strip=True))
                                        hata_durumu = "OK"
                            except: pass
                except Exception as e:
                    print("HB Review Cloudscraper Fallback error:", e)

            raw_data = list(raw_data_set)

            if raw_data and len(raw_data) > 0 and hata_durumu == "OK":
                bad_reviews, good_reviews = [], []
                negWords = ['kırık', 'kötü', 'iade', 'eksik', 'defolu', 'yırtık', 'koptu', 'kalitesiz', 'çöp', 'berbat',
                            'maalesef', 'sorun', 'sıkıntı', 'tavsiye', 'farklı', 'zarar', 'plastik', 'yamuk', 'bozuldu',
                            'çizik']

                for r in raw_data:
                    if any(w in r.lower() for w in negWords):
                        bad_reviews.append(r)
                    else:
                        good_reviews.append(r)

                # V84 BALANCE RESTORED
                final_bad = bad_reviews[:20]
                needed = 40 - len(final_bad)
                final_good = good_reviews[:needed]

                sayfa_metinleri = final_bad + final_good
                olumsuz_sayisi = len(final_bad)
                olumlu_sayisi = len(final_good)
                incelenen_yorum_sayisi = len(sayfa_metinleri)
            else:
                sayfa_metinleri, olumsuz_sayisi, olumlu_sayisi, incelenen_yorum_sayisi = [], 0, 0, 0

            # AI GATEKEEPER
            if hata_durumu == "BLOCKED":
                spesifik_urun_ismi = "Erişim Reddedildi (Güvenlik Duvarı)"
                yorum_ozeti = "<div style='color:#ef4444; padding:20px; text-align:center;'><b>⛔ Güvenlik Duvarı Engeli</b><br>Bot koruması sayfayı engelledi. Daha sonra tekrar deneyin.</div>"
            elif incelenen_yorum_sayisi > 0:
                if client:
                    durum_etiketi.config(text=f"Status: Generating Groq Sentiment Analysis...", fg="#9b59b6")
                    pencere.update()

                    temiz_metinler = [r.replace('"', "'").replace('\n', ' ') for r in sayfa_metinleri]

                    prompt = f"""Aşağıdaki metinler e-ticaret müşteri yorumlarıdır. Arayüz yazılarını yoksay.
                    ÇOK ÖNEMLİ KATI KURALLAR:
                    1. "basarili" anahtarına SADECE ürünün iyi yönlerini yaz. Her cümle farklı bir olumlu özelliği ele alsın (örn: kalite, kullanım kolaylığı, fiyat-performans, tasarım). En fazla 2-3 cümle kullan. Dizi/Liste (Array) KULLANMA.
                    2. "sikayet" anahtarına SADECE şikayetleri yaz. Şikayetleri gizleme AMA HİÇBİR KONUYU TEKRAR ETME — her cümle farklı bir sorun kategorisini kapsasın (örn: kargo hasarı, malzeme kalitesi, eksik parça, dayanıklılık). En fazla 3-4 cümle kullan. Dizi/Liste (Array) KULLANMA.
                    3. "genel" anahtarına genel müşteri memnuniyetini 1-2 cümle ile özetle; hem olumlu hem olumsuz dengeyi yansıt.
                    4. TEKRAR YASAĞI: Aynı konuyu veya benzer ifadeyi birden fazla yerde ASLA tekrarlama. Her cümle benzersiz bir bilgi versin.

                    JSON formatında yanıtla: {{"gercek_yorum_var_mi": true/false, "basarili": "Özetlenmiş olumlu özellikler", "sikayet": "Özetlenmiş ve gruplanmış şikayetler", "genel": "Kısa genel özet"}}
                    Metinler: {' | '.join(temiz_metinler)}"""

                    try:
                        response = client.chat.completions.create(
                            messages=[{"role": "user", "content": prompt}],
                            model="llama-3.3-70b-versatile",
                            temperature=0.1,
                            max_tokens=1500,
                            response_format={"type": "json_object"}
                        )
                        ai_data = json.loads(response.choices[0].message.content)

                        if ai_data.get("gercek_yorum_var_mi") is False:
                            incelenen_yorum_sayisi, olumlu_sayisi, olumsuz_sayisi = 0, 0, 0
                            yorum_ozeti = "<div style='color:#a1a1aa; text-align:center; padding:30px;'>📭 Henüz Yorum Yok<br><span style='font-size:12px;'>Bu ürün için yapılmış gerçek müşteri değerlendirmesi bulunamadı.</span></div>"
                        else:
                            total_sent = olumlu_sayisi + olumsuz_sayisi
                            olumlu_yuzde = (olumlu_sayisi / total_sent) * 100 if total_sent else 0
                            olumsuz_yuzde = (olumsuz_sayisi / total_sent) * 100 if total_sent else 0

                            def format_ai_text(val):
                                if isinstance(val, list):
                                    return ", ".join(str(v) for v in val)
                                return str(val)

                            basarili_txt = format_ai_text(ai_data.get('basarili', ''))
                            sikayet_txt = format_ai_text(ai_data.get('sikayet', ''))
                            genel_txt = format_ai_text(ai_data.get('genel', ''))

                            prog = f"""
                            <div style="margin-top:25px;">
                                <div style="display:flex; justify-content:space-between; font-size:12px; font-weight:bold; margin-bottom:8px;">
                                    <span style="color:#34d399;">{olumlu_sayisi} OLUMLU</span><span style="color:#ef4444;">{olumsuz_sayisi} OLUMSUZ</span>
                                </div>
                                <div style="width:100%; height:8px; background:#27272a; border-radius:4px; display:flex; overflow:hidden;">
                                    <div style="background:#34d399; width:{olumlu_yuzde}%;"></div><div style="background:#ef4444; width:{olumsuz_yuzde}%;"></div>
                                </div>
                            </div>"""
                            yorum_ozeti = f"<div style='margin-bottom:12px;'><b>🌟 Başarılı Yönler:</b> {basarili_txt}</div><div style='margin-bottom:12px;'><b>⚠️ Kritik Şikayetler:</b> {sikayet_txt}</div><div><b>📊 Genel Kanı:</b> {genel_txt}</div>{prog}"
                    except Exception as e:
                        yorum_ozeti = f"⚠️ AI JSON Format Hatası. (Çok fazla veri veya limit aşıldı)"
                else:
                    yorum_ozeti = "⚠️ GEÇERLİ BİR GROQ API KEY GİRİNİZ."
            else:
                yorum_ozeti = "<div style='color:#a1a1aa; text-align:center; padding:30px;'>📭 Henüz Yorum Yok<br><span style='font-size:12px;'>Bu ürün için yapılmış gerçek müşteri değerlendirmesi bulunamadı.</span></div>"

            logo_url = f"https://icon.horse/icon/{domain}" if domain else ""
            sonuclar.append({
                "Platform": platform_name,
                "Logo": logo_url,
                "Satici": satici_ismi,
                "UrunAdi": spesifik_urun_ismi,
                "URL": original_url,
                "Analiz": yorum_ozeti,
                "Sayi": incelenen_yorum_sayisi
            })

        except Exception as e:
            # We log specific errors now instead of crashing the whole program silently
            sonuclar.append({
                "Platform": platform_name,
                "Logo": "",
                "Satici": "Bilinmiyor",
                "UrunAdi": "Hata / Zaman Aşımı",
                "URL": original_url,
                "Analiz": f"<div style='color:#ef4444; padding:20px; text-align:center;'><b>⛔ Sayfa Yüklenemedi</b><br>Sistem bu ürünün verilerini çekerken bir hata ile karşılaştı.</div>",
                "Sayi": 0
            })
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass

    if sonuclar:
        durum_etiketi.config(text="Status: Generating Review Dashboard...", fg="#e67e22")
        pencere.update()

        total_products = len(sonuclar)
        total_reviews = sum(s['Sayi'] for s in sonuclar)

        html_satirlar = ""
        for s in sonuclar:
            bg_renk = "rgba(16, 185, 129, 0.1)" if s['Sayi'] > 0 else "rgba(148, 163, 184, 0.1)"
            border_renk = "rgba(16, 185, 129, 0.2)" if s['Sayi'] > 0 else "rgba(148, 163, 184, 0.2)"
            logo_html = f"<img src='{s['Logo']}' style='width:20px; height:20px; margin-right:8px; border-radius:4px;' onerror=\"this.style.display='none'\">" if \
            s['Logo'] else ""

            html_satirlar += f"""
            <div style="background: rgba(24, 24, 27, 0.55); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; display: flex; margin-bottom: 25px; box-shadow: 0 4px 20px rgba(0,0,0,0.2);">
                <div style="padding: 30px; border-right: 1px solid rgba(255,255,255,0.08); width: 250px; flex-shrink: 0; display:flex; flex-direction:column;">
                    <div style="display:flex; align-items:center; font-weight: 800; font-size: 20px; color: #fff;">{logo_html}{s['Platform']}</div>

                    <div style="font-size: 14px; color: #a1a1aa; margin-top: 8px;">Satıcı: {s['Satici']}</div>
                    <div style="font-size: 13px; color: #d1d5db; margin-top: 4px; margin-bottom: 20px; line-height: 1.4;">{s['UrunAdi']}</div>

                    <div style="background: {bg_renk}; border: 1px solid {border_renk}; padding: 8px 12px; border-radius: 6px; font-size: 13px; font-weight: bold; color: {'#34d399' if s['Sayi'] > 0 else '#94a3b8'}; margin-bottom:15px; text-align:center;">{s['Sayi']} Yorum Analizi</div>
                    <a href="{s['URL']}" target="_blank" style="margin-top:auto; text-align:center; padding: 10px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color:#fff; text-decoration:none; border-radius:6px; font-weight:bold; font-size:13px; transition:0.3s;">Ürüne Git ➔</a>
                </div>
                <div style="padding: 30px; flex-grow: 1; font-size: 15px; color: #e4e4e7; line-height: 1.6; display:flex; flex-direction:column; justify-content:center;">
                    {s['Analiz']}
                </div>
            </div>
            """

        current_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        html_icerik = f"""
        <html>
        <head>
            <meta charset="utf-8">
            <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;700;800&display=swap" rel="stylesheet">
            <style>
                body {{ font-family: 'Plus Jakarta Sans', sans-serif; background: #09090b; color: #f8fafc; padding: 40px; margin:0; }} 
                .container {{ max-width: 1100px; margin: auto; }}
            </style>
        </head>
        <body>
        <div class="container">

        <div style="color:#818cf8; font-weight:bold; letter-spacing:2px; font-size:13px; margin-bottom:10px; text-transform:uppercase;">BMK Veri Odaklı Danışmanlık Hizmeti</div>
        <h1 style="margin:0 0 40px 0; font-size:38px; font-weight:800;">E-Ticaret Yorum Analiz Raporu</h1>

        <div style="display: flex; gap: 24px; margin-bottom: 50px;">
            <div style="background: rgba(24, 24, 27, 0.55); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 25px; flex: 1;">
                <div style="font-size: 32px; font-weight: 800; color: #ffffff; margin-bottom: 5px;">{total_products}</div>
                <div style="font-size: 12px; color: #a1a1aa; font-weight: 600; text-transform: uppercase; letter-spacing:1px;">Analiz Edilen Ürün</div>
            </div>
            <div style="background: rgba(24, 24, 27, 0.55); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 25px; flex: 1;">
                <div style="font-size: 32px; font-weight: 800; color: #34d399; margin-bottom: 5px;">{total_reviews}</div>
                <div style="font-size: 12px; color: #a1a1aa; font-weight: 600; text-transform: uppercase; letter-spacing:1px;">İncelenen Yorum Hacmi</div>
            </div>
            <div style="background: rgba(24, 24, 27, 0.55); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 25px; flex: 1;">
                <div style="font-size: 32px; font-weight: 800; color: #818cf8; margin-bottom: 5px;">Aktif</div>
                <div style="font-size: 12px; color: #a1a1aa; font-weight: 600; text-transform: uppercase; letter-spacing:1px;">Llama-3 Nöral JSON Motoru</div>
            </div>
        </div>

        {html_satirlar}

        <div style="text-align:center; margin-top:50px; padding-top:20px; border-top:1px solid rgba(255,255,255,0.05); color:#52525b; font-size:14px;">
            🚀 Bu rapor <b>BMK Veri Odaklı Danışmanlık Hizmeti</b> tarafından üretilmiştir. Rapor Tarihi: {current_time}
        </div>
        </div>
        </body>
        </html>
        """
        rapor_yolu = os.path.abspath("BMK_Reputation_Report.html")
        with open(rapor_yolu, "w", encoding="utf-8") as f:
            f.write(html_icerik)

        ac_rapor_tarayicida(rapor_yolu)
        durum_etiketi.config(text="Status: Success! Review Dashboard Opened.", fg="green")
    else:
        durum_etiketi.config(text="Status: Error! No data.", fg="red")


# =========================================================================
# 🚀 MODULE 3: COMBINED PRICE + REVIEW RADAR
# =========================================================================
def run_combined_radar(urls, api_key, durum_etiketi, pencere):
    durum_etiketi.config(text="Status: Initiating Combined Radar...", fg="#e67e22")
    pencere.update()

    client = Groq(api_key=api_key) if len(api_key) > 15 else None
    price_results = []
    review_results = []
    referans_url = urls[0]

    for idx, original_url in enumerate(urls):
        url = original_url.strip()
        if not url:
            continue

        platform_name = marka_adi_bul(url)
        domain = get_domain(url)
        is_trendyol = "trendyol.com" in url
        is_hepsiburada = "hepsiburada.com" in url

        durum_etiketi.config(text=f"Status: [{idx+1}/{len(urls)}] {platform_name} — Scraping Price & Reviews...", fg="#3498db")
        pencere.update()

        driver = None
        fiyat_bulundu = "Bulunamadı"
        spesifik_urun_ismi = "İsim Bulunamadı"
        satici_ismi = "Platform Satıcısı"
        hata_durumu = "OK"

        try:
            uc = _get_uc()
            options = uc.ChromeOptions()
            options.add_argument("--disable-notifications")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-gpu")
            driver = uc.Chrome(options=options)
            driver.set_page_load_timeout(120)

            try:
                driver.get(url)
            except Exception as e:
                if "timeout" in str(e).lower():
                    driver.execute_script("window.stop();")

            time.sleep(6)

            # --- SHIELD BREAKER ---
            for _ in range(2):
                hata_durumu = driver.execute_script("""
                    var t = document.title.toLowerCase().trim();
                    var b = document.body.innerText.toLowerCase();
                    if (t.includes('robot') || t.includes('captcha') || b.includes('robot musunuz') || t === 'hepsiburada.com') {
                        if (!document.querySelector('#product-name') && !document.querySelector('.product-name')) return 'BLOCKED';
                    }
                    if (document.querySelector('h1') === null && document.querySelector('img') === null) return 'NOT_LOADED';
                    return 'OK';
                """)
                if hata_durumu in ('BLOCKED', 'NOT_LOADED'):
                    time.sleep(3)
                    driver.refresh()
                    time.sleep(6)
                else:
                    break

            if hata_durumu == "OK":
                try:
                    driver.execute_script("document.querySelectorAll('.modal, .popup, [id*=\"onetrust\"]').forEach(el => el.style.display='none');")
                except:
                    pass

                # --- EXTRACT PRODUCT NAME ---
                raw_isim = driver.execute_script("""
                    var domain = window.location.hostname;
                    if (domain.includes('trendyol.com')) { var el = document.querySelector('.pr-new-br h1, h1.product-name'); if (el) return el.innerText; }
                    if (domain.includes('hepsiburada.com')) { var el = document.querySelector('#product-name, h1[itemprop=\"name\"]'); if (el) return el.innerText; }
                    return document.title.split('|')[0].split('-')[0].trim();
                """)
                spesifik_urun_ismi = urun_ismi_temizle(raw_isim)

                # --- EXTRACT SELLER ---
                try:
                    satici_ismi = driver.execute_script("""
                        var s = '';
                        var ty = document.querySelector('.merchant-box a, .seller-store a, .merchant-text, .seller-name, [data-testid=\"merchant-name\"]');
                        if(ty) s = ty.innerText.trim();
                        if(!s) { var hb = document.querySelector('.merchantLink, a[href*=\"/magaza/\"]'); if(hb) s = hb.innerText.trim(); }
                        if(!s || s.length < 2) {
                            var brand = document.querySelector('.pr-new-br a, .pr-new-br span, .brand-name, .product-brand');
                            if(brand && brand.innerText.trim().length > 1) { s = \"Marka: \" + brand.innerText.trim(); }
                        }
                        if (s) { s = s.split('\\n')[0].replace(/[0-9]+,[0-9]+.*/g, '').replace(/Takip et/gi, '').replace(/Satıcıya sor/gi, '').replace(/Değerlendirme/gi, '').trim(); }
                        return (s && s.length > 1) ? s : 'Platform Satıcısı';
                    """)
                except:
                    satici_ismi = "Platform Satıcısı"

                # --- PHASE 1: EXTRACT PRICE ---
                fiyat_bulundu = driver.execute_script("""
                    var domain = window.location.hostname;
                    if (domain.includes('trendyol.com')) {
                        // 1) İndirimli Sepet Fiyatı / Trendyol Plus Fiyatı ("Sepette X TL" - bu en gerçekçi fiyattır)
                        var basketPrice = document.querySelector('.product-price-container .basket-discount, .pr-bx-w .basket-discount, .basket-price, .product-price-container .discounted-price, [data-testid="basket-price"]');
                        if (basketPrice && basketPrice.offsetParent !== null) { 
                            var t = basketPrice.innerText.replace(/Sepette/i, '').trim().split('\\n')[0].trim(); 
                            if(t && /\\d/.test(t)) return t; 
                        }

                        // 2) Normal indirimli fiyat (Üzeri çizilmiş fiyatın altındaki normal fiyat)
                        var el = document.querySelector('.prc-dsc, span.prc-dsc, .product-price-container .prc-dsc');
                        if (el && el.offsetParent !== null) { 
                            var t = el.innerText.trim().split('\\n')[0].trim(); 
                            if(t && /\\d/.test(t)) return t; 
                        }

                        // 3) Lowest price block (Eski fiyat üstte, yeni fiyat alttadır)
                        var lpBtn = document.querySelector('button.lowest-price, .lowest-price');
                        if (lpBtn && lpBtn.offsetParent !== null) { 
                            var spans = lpBtn.querySelectorAll('span'); 
                            if (spans.length > 0) { 
                                var t = spans[spans.length-1].innerText.trim(); 
                                if (t && /\\d/.test(t)) return t; 
                            } 
                        }
                    }

                    if (domain.includes('hepsiburada.com')) {
                        // Sadece ana ürün bölümündeki (soldaki büyük alan) fiyatları ara. Sağdaki "Diğer satıcılar" (other sellers) kısmını yoksay.
                        // 1) Sepet Fiyatı (Eğer varsa en ucuzudur)
                        var basketPrice = document.querySelector('#product-price .basket-price, .product-price-wrapper .basket-price, [data-test-id="price-basket-price"]');
                        if (basketPrice && basketPrice.offsetParent !== null) {
                            var t = basketPrice.innerText.replace(/Sepette/i, '').trim().split('\\n')[0].trim();
                             if(t && /\\d/.test(t)) return t;
                        }

                        // 2) Ana Fiyat
                        var mainPrice = document.querySelector('[data-test-id="price-current-price"], #offering-price');
                        if (mainPrice && mainPrice.offsetParent !== null) { 
                            var t = mainPrice.innerText.trim().split('\\n')[0].trim(); 
                            if(t && /\\d/.test(t)) return t; 
                        }
                    }

                    if (domain.includes('amazon.')) {
                        var el = document.querySelector('#corePriceDisplay_desktop_feature_div .a-price .a-offscreen, #corePrice_desktop .a-price .a-offscreen');
                        if (el && el.offsetParent !== null) { var t = el.innerText.trim().split('\\n')[0].trim(); if(t) return t; }
                    }
                    var max_size = 0; var best_price = 'Bulunamadı';
                    document.querySelectorAll('*').forEach(el => {
                        if (el.offsetParent !== null && el.children.length === 0) {
                            var txt = (el.innerText || el.textContent || '').trim();
                            if (txt && txt.length < 30 && /\\d/.test(txt) && (txt.includes('TL') || txt.includes('₺'))) {
                                var style = window.getComputedStyle(el);
                                if (style.textDecorationLine !== 'line-through') {
                                    var size = parseFloat(style.fontSize);
                                    if (size > max_size) { max_size = size; best_price = txt; }
                                }
                            }
                        }
                    });
                    return best_price;
                """)

            price_results.append({
                "Platform": platform_name,
                "UrunAdi": spesifik_urun_ismi,
                "RawFiyat": fiyat_bulundu.strip() if fiyat_bulundu else "Bulunamadı",
                "CleanFiyat": fiyati_temizle(fiyat_bulundu) if fiyat_bulundu else 0.0,
                "URL": url,
                "Durum": hata_durumu
            })

            # --- PHASE 2: EXTRACT REVIEWS (Trendyol & HB only) ---
            if (is_trendyol or is_hepsiburada) and hata_durumu == "OK":
                durum_etiketi.config(text=f"Status: [{idx+1}/{len(urls)}] {platform_name} — Extracting Reviews...", fg="#9b59b6")
                pencere.update()

                raw_data_set = set()
                incelenen_yorum_sayisi = 0
                olumlu_sayisi = 0
                olumsuz_sayisi = 0
                yorum_ozeti = ""

                # Navigate to reviews page
                if is_trendyol:
                    if "/yorumlar" not in driver.current_url:
                        try:
                            driver.get(driver.current_url.split('?')[0] + "/yorumlar")
                            time.sleep(6)
                            # Initial scroll to trigger lazy-loaded reviews
                            driver.execute_script("window.scrollBy(0, 2000);")
                            time.sleep(2)
                        except:
                            pass
                elif is_hepsiburada:
                    try:
                        driver.execute_script("""
                            var reviewSection = document.querySelector('[id*="review"], [id*="yorum"], [data-test-id*="review"], [class*="rnr-"]');
                            if (reviewSection) { reviewSection.scrollIntoView({behavior: 'smooth', block: 'start'}); }
                            else { window.scrollBy(0, 3500); }
                        """)
                        time.sleep(2)
                        driver.execute_script("""
                            var clicked = false;
                            document.querySelectorAll('a, button, div[role="tab"], li[role="tab"]').forEach(el => {
                                if(clicked) return; if(el.offsetParent === null) return;
                                var txt = (el.innerText || "").toLowerCase().trim();
                                if(txt.length < 60 && !txt.includes('yap') && !txt.includes('soru') &&
                                   (txt.includes('değerlendirme') || txt.includes('yorumlar'))) {
                                    try { el.scrollIntoView({block:'center'}); el.click(); clicked = true; } catch(e){}
                                }
                            });
                        """)
                        time.sleep(3)
                    except:
                        pass
                    if "-yorumlari" not in driver.current_url and "yorum" not in driver.current_url.lower():
                        base_url = driver.current_url.split('?')[0].replace("-pm-", "-p-").replace("-c-", "-p-")
                        try:
                            driver.get(base_url + "-yorumlari")
                            time.sleep(5)
                        except:
                            pass

                if "giris" in driver.current_url.lower() or "login" in driver.current_url.lower():
                    try:
                        driver.get(url)
                        time.sleep(5)
                        driver.execute_script("window.scrollBy(0, 4000);")
                        time.sleep(2)
                    except:
                        pass

                # Review extraction loop
                for step in range(25):
                    if step == 5 and len(raw_data_set) == 0 and is_trendyol and "/yorumlar" in driver.current_url:
                        try:
                            driver.get(url)
                            time.sleep(5)
                            driver.execute_script("window.scrollBy(0, 3000);")
                            time.sleep(2)
                        except:
                            pass

                    # Trendyol uses infinite scroll; scroll to bottom aggressively
                    if is_trendyol:
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    else:
                        driver.execute_script("window.scrollBy(0, 1500);")

                    # Trendyol: Expand truncated reviews by clicking "Devamını Oku" links
                    if is_trendyol:
                        try:
                            driver.execute_script("""
                                document.querySelectorAll('a, button, span').forEach(el => {
                                    var txt = (el.innerText || "").toLowerCase().trim();
                                    if (txt === 'devamını oku' || txt === 'devamini oku' || txt === 'daha fazla' ||
                                        (el.className && el.className.toLowerCase().includes('read-more'))) {
                                        try { el.click(); } catch(e) {}
                                    }
                                });
                            """)
                        except:
                            pass

                    js_click = f"""
                        var targetPage = '{(step + 2)}'; var clicked = false;
                        document.querySelectorAll('button, a, div, span, li').forEach(b => {{
                            if(clicked) return; if(b.offsetParent === null) return;
                            var t = (b.innerText || "").toLowerCase().trim();
                            var c = (b.className || "").toLowerCase();
                            var p = (b.parentElement?.className || "").toLowerCase();
                            if(t.includes('yap') || t.includes('soru') || t.includes('giriş') || t.includes('kayıt')) return;
                            if(t.includes('daha fazla') || t === 'tüm yorumları gör' || t === 'sonraki' || t === 'devamını gör' || t === 'ileri' || t === '>' || t === 'load more') {{
                                try {{ b.click(); clicked = true; }} catch(e){{}}
                            }}
                            else if (t === targetPage && (c.includes('page') || c.includes('pagination') || p.includes('pagination'))) {{
                                try {{ b.click(); clicked = true; }} catch(e){{}}
                            }}
                        }});
                        if(!clicked) {{
                            var lmb = document.querySelector('[data-test-id*="show-more"], [data-test-id*="load-more"], [class*="showMore"], [class*="load-more"]');
                            if(lmb && lmb.offsetParent !== null) {{ try {{ lmb.click(); }} catch(e){{}} }}
                        }}
                    """
                    try:
                        driver.execute_script(js_click)
                    except:
                        pass
                    time.sleep(1.5)

                    js_extractor = """
                        var res = [];
                        var domain = window.location.hostname;
                        var isTrendyol = domain.includes('trendyol.com');
                        var isReviewPage = window.location.href.includes('yorumlar') || window.location.href.includes('-yorumlari');

                        if (isTrendyol) {
                            var trendyolSelectors = [
                                '.comment-text', '.comment-content',
                                '[class*="comment-text"]', '[class*="comment-content"]',
                                '[class*="CommentText"]',
                                '[itemprop="reviewBody"]', '[itemprop="description"]'
                            ];
                            var foundViaSelectors = false;
                            trendyolSelectors.forEach(sel => {
                                document.querySelectorAll(sel).forEach(el => {
                                    if (el.offsetParent !== null) {
                                        var txt = el.innerText.trim();
                                        if (txt.length > 15 && txt.length < 2000) {
                                            foundViaSelectors = true;
                                            res.push(txt);
                                        }
                                    }
                                });
                            });
                            if (!foundViaSelectors && isReviewPage) {
                                document.querySelectorAll('div, p, span').forEach(el => {
                                    if (el.offsetParent !== null && el.children.length <= 2) {
                                        var txt = el.innerText.trim();
                                        if (txt.length > 25 && txt.length < 1500 && !txt.includes('Satıcı:') &&
                                            !txt.includes('Beğen') && !txt.includes('Şikayet Et') &&
                                            !/^\d+ kişi/.test(txt) && !/^[A-ZÇĞİÖŞÜ]\*\*/.test(txt)) {
                                            res.push(txt);
                                        }
                                    }
                                });
                            }
                        } else if (isReviewPage) {
                            document.querySelectorAll('p, span, div.comment-text, div[itemprop="reviewBody"]').forEach(el => {
                                if (el.offsetParent !== null) { var txt = el.innerText.trim(); if(txt.length > 25 && txt.length < 2000) res.push(txt); }
                            });
                        } else {
                            var strictNodes = document.querySelectorAll('.comment-text, .rnr-com-tx, [itemprop="reviewBody"], div[class*="ReviewCard"] p, div[class*="ReviewCard"] span, div[class*="hermes-"] p, div[class*="hermes-"] span, #reviewsTabContent p, #reviewsTabContent span, [data-test-id*="review"] p, [data-test-id*="review"] span, [class*="review-text"], [class*="ReviewText"], [class*="review-body"], [class*="ReviewBody"], [class*="rnr-"] p, [class*="rnr-"] span, div[class*="review"] p, div[class*="review"] span');
                            strictNodes.forEach(el => {
                                if (el.offsetParent !== null) { var txt = el.innerText.trim(); if(txt.length > 25 && txt.length < 2000) res.push(txt); }
                            });
                        }
                        return res;
                    """
                    try:
                        page_texts = driver.execute_script(js_extractor)
                        if page_texts:
                            for text in page_texts:
                                clean_text = " ".join(text.split())
                                if len(clean_text.split()) >= 5 and clean_text not in raw_data_set:
                                    if not any(banned in clean_text.lower() for banned in BANNED_UI_PHRASES):
                                        raw_data_set.add(clean_text)
                    except:
                        pass

                raw_data = list(raw_data_set)

                if raw_data and hata_durumu == "OK":
                    bad_reviews, good_reviews = [], []
                    negWords = ['kırık', 'kötü', 'iade', 'eksik', 'defolu', 'yırtık', 'koptu', 'kalitesiz', 'çöp', 'berbat',
                                'maalesef', 'sorun', 'sıkıntı', 'tavsiye', 'farklı', 'zarar', 'plastik', 'yamuk', 'bozuldu', 'çizik']
                    for r in raw_data:
                        if any(w in r.lower() for w in negWords):
                            bad_reviews.append(r)
                        else:
                            good_reviews.append(r)
                    final_bad = bad_reviews[:20]
                    needed = 40 - len(final_bad)
                    final_good = good_reviews[:needed]
                    sayfa_metinleri = final_bad + final_good
                    olumsuz_sayisi = len(final_bad)
                    olumlu_sayisi = len(final_good)
                    incelenen_yorum_sayisi = len(sayfa_metinleri)
                else:
                    sayfa_metinleri = []

                if hata_durumu == "BLOCKED":
                    yorum_ozeti = "<div style='color:#ef4444; padding:20px; text-align:center;'><b>⛔ Güvenlik Duvarı Engeli</b></div>"
                elif incelenen_yorum_sayisi > 0 and client:
                    durum_etiketi.config(text=f"Status: [{idx+1}/{len(urls)}] AI Sentiment Analysis...", fg="#9b59b6")
                    pencere.update()
                    temiz_metinler = [r.replace('"', "'").replace('\n', ' ') for r in sayfa_metinleri]
                    prompt = f"""Aşağıdaki metinler e-ticaret müşteri yorumlarıdır.
                    KATI KURALLAR:
                    1. "basarili": SADECE ürünün iyi yönleri. Her cümle farklı bir olumlu özelliği kapsasın (kalite, kullanım, fiyat-performans vb.). En fazla 2-3 cümle. Dizi KULLANMA.
                    2. "sikayet": SADECE şikayetler. Her cümle farklı bir sorun kategorisini ele alsın, aynı konuyu tekrarlama. En fazla 3-4 cümle. Dizi KULLANMA.
                    3. "genel": 1-2 cümle genel özet, hem olumlu hem olumsuz dengeyi yansıt.
                    4. TEKRAR YASAĞI: Hiçbir konuyu birden fazla yerde tekrarlama, her cümle benzersiz bilgi versin.
                    JSON: {{"gercek_yorum_var_mi": true/false, "basarili": "...", "sikayet": "...", "genel": "..."}}
                    Metinler: {' | '.join(temiz_metinler)}"""
                    try:
                        response = client.chat.completions.create(
                            messages=[{"role": "user", "content": prompt}],
                            model="llama-3.3-70b-versatile", temperature=0.1, max_tokens=1500,
                            response_format={"type": "json_object"}
                        )
                        ai_data = json.loads(response.choices[0].message.content)
                        if ai_data.get("gercek_yorum_var_mi") is False:
                            incelenen_yorum_sayisi, olumlu_sayisi, olumsuz_sayisi = 0, 0, 0
                            yorum_ozeti = "<div style='color:#a1a1aa; text-align:center; padding:30px;'>📭 Henüz Yorum Yok</div>"
                        else:
                            total_sent = olumlu_sayisi + olumsuz_sayisi
                            olumlu_yuzde = (olumlu_sayisi / total_sent) * 100 if total_sent else 0
                            olumsuz_yuzde = (olumsuz_sayisi / total_sent) * 100 if total_sent else 0
                            def fmt(v):
                                return ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
                            b_txt = fmt(ai_data.get('basarili', ''))
                            s_txt = fmt(ai_data.get('sikayet', ''))
                            g_txt = fmt(ai_data.get('genel', ''))
                            prog = f"""<div style="margin-top:25px;">
                                <div style="display:flex; justify-content:space-between; font-size:12px; font-weight:bold; margin-bottom:8px;">
                                    <span style="color:#34d399;">{olumlu_sayisi} OLUMLU</span><span style="color:#ef4444;">{olumsuz_sayisi} OLUMSUZ</span>
                                </div>
                                <div style="width:100%; height:8px; background:#27272a; border-radius:4px; display:flex; overflow:hidden;">
                                    <div style="background:#34d399; width:{olumlu_yuzde}%;"></div><div style="background:#ef4444; width:{olumsuz_yuzde}%;"></div>
                                </div>
                            </div>"""
                            yorum_ozeti = f"<div style='margin-bottom:12px;'><b>🌟 Başarılı Yönler:</b> {b_txt}</div><div style='margin-bottom:12px;'><b>⚠️ Kritik Şikayetler:</b> {s_txt}</div><div><b>📊 Genel Kanı:</b> {g_txt}</div>{prog}"
                    except:
                        yorum_ozeti = "⚠️ AI Analizi Başarısız."
                elif incelenen_yorum_sayisi > 0:
                    yorum_ozeti = "⚠️ GEÇERLİ BİR GROQ API KEY GİRİNİZ."
                else:
                    yorum_ozeti = "<div style='color:#a1a1aa; text-align:center; padding:30px;'>📭 Henüz Yorum Yok</div>"

                logo_url = f"https://icon.horse/icon/{domain}" if domain else ""
                review_results.append({
                    "Platform": platform_name, "Logo": logo_url, "Satici": satici_ismi,
                    "UrunAdi": spesifik_urun_ismi, "URL": original_url,
                    "Analiz": yorum_ozeti, "Sayi": incelenen_yorum_sayisi
                })

        except Exception as e:
            price_results.append({
                "Platform": platform_name, "UrunAdi": "Hata", "RawFiyat": "Hata",
                "CleanFiyat": 0.0, "URL": url, "Durum": "ERROR"
            })
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass

    # --- AI PRICE STRATEGY ---
    ai_fiyat_ozet = ""
    analiz_gerekenler = []
    if price_results:
        for s in price_results:
            if s["URL"] == referans_url and s["Durum"] == "OK":
                analiz_gerekenler.append(f"🎯 REFERANS ({s['Platform']}): {s['CleanFiyat']} TL - {s['UrunAdi']}")
                break
        for s in price_results:
            if s["URL"] != referans_url and s["Durum"] == "OK":
                analiz_gerekenler.append(f"🔗 COMPETITOR ({s['Platform']}): {s['CleanFiyat']} TL - {s['UrunAdi']}")

    if client and analiz_gerekenler:
        durum_etiketi.config(text="Status: AI Price Strategy...", fg="#9b59b6")
        pencere.update()
        try:
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": f"Analyze pricing. Verify if products match via titles. Write 2-3 sentence Turkish executive summary.\nData: {' | '.join(analiz_gerekenler)}"}],
                model="llama-3.3-70b-versatile", temperature=0.3, max_tokens=300
            )
            ai_fiyat_ozet = response.choices[0].message.content.strip()
        except:
            ai_fiyat_ozet = "⚠️ AI Analizi Başarısız."

    # --- GENERATE COMBINED TABBED HTML ---
    if price_results or review_results:
        durum_etiketi.config(text="Status: Generating Combined Dashboard...", fg="#e67e22")
        pencere.update()

        resmi_fiyat = 0.0
        for s in price_results:
            if s["URL"] == referans_url and s["Durum"] == "OK":
                resmi_fiyat = s["CleanFiyat"]
                break

        # --- BUILD PRICE CARDS ---
        price_cards = ""
        for s in price_results:
            gf = s["CleanFiyat"]
            fp = standard_fiyat_formati(gf)
            durum_ui = "✅ EŞİT"
            renk = "#3b82f6"
            if s["Durum"] in ("BLOCKED", "ERROR"):
                durum_ui = "⛔ ENGEL"; renk = "#ef4444"; fp = "Erişim Reddedildi"
            elif s["Durum"] == "NOT_LOADED" or s["RawFiyat"] == "Bulunamadı":
                durum_ui = "⚠️ OKUNAMADI"; renk = "#a1a1aa"; fp = "Bulunamadı"
            elif s["URL"] != referans_url:
                if gf <= 0:
                    durum_ui = "⚠️ OKUNAMADI"; renk = "#a1a1aa"
                elif gf < resmi_fiyat:
                    durum_ui = f"🔻 DÜŞÜK (-{standard_fiyat_formati(resmi_fiyat - gf)})"; renk = "#ef4444"
                elif gf > resmi_fiyat:
                    durum_ui = f"🔺 YÜKSEK (+{standard_fiyat_formati(gf - resmi_fiyat)})"; renk = "#34d399"

            price_cards += f"""
            <div style="background: rgba(24,24,27,0.55); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 20px; margin-bottom: 15px; display: grid; grid-template-columns: 1fr 200px 220px; gap: 20px; align-items: center; border-left: 4px solid {renk if s['URL'] != referans_url else '#818cf8'};">
                <div style="display:flex; flex-direction:column; gap:6px;">
                    <div style="font-weight:800; font-size:19px; color:{'#818cf8' if s['URL'] == referans_url else '#fff'};">
                        🎯 {s['Platform']} {'<span style="font-size:11px; color:#a1a1aa;">(BAZ ALINAN)</span>' if s['URL'] == referans_url else ''}
                    </div>
                    <a href='{s['URL']}' target='_blank' style='background:rgba(255,255,255,0.05); padding:6px 12px; border-radius:6px; color:#fff; text-decoration:none; font-size:12px; font-weight:bold; width:max-content;'>Ürüne Git ➔</a>
                    <div style="font-size:13px; color:#d1d5db; line-height:1.4;">{s['UrunAdi']}</div>
                </div>
                <div style="font-size:24px; font-weight:800; color:#fff; text-align:left;">{fp}</div>
                <div style="text-align:right;">
                    <span style="font-size:13px; font-weight:bold; color:{renk if s['URL'] != referans_url else '#818cf8'}; background:{renk if s['URL'] != referans_url else '#818cf8'}15; padding:6px 12px; border-radius:6px;">{durum_ui}</span>
                </div>
            </div>"""

        # --- BUILD REVIEW CARDS ---
        review_cards = ""
        total_reviews = sum(s['Sayi'] for s in review_results) if review_results else 0
        for s in review_results:
            bg_r = "rgba(16, 185, 129, 0.1)" if s['Sayi'] > 0 else "rgba(148, 163, 184, 0.1)"
            bd_r = "rgba(16, 185, 129, 0.2)" if s['Sayi'] > 0 else "rgba(148, 163, 184, 0.2)"
            logo_h = f"<img src='{s['Logo']}' style='width:20px; height:20px; margin-right:8px; border-radius:4px;' onerror=\"this.style.display='none'\">" if s['Logo'] else ""
            review_cards += f"""
            <div style="background: rgba(24, 24, 27, 0.55); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; display: flex; margin-bottom: 25px; box-shadow: 0 4px 20px rgba(0,0,0,0.2);">
                <div style="padding: 30px; border-right: 1px solid rgba(255,255,255,0.08); width: 250px; flex-shrink: 0; display:flex; flex-direction:column;">
                    <div style="display:flex; align-items:center; font-weight: 800; font-size: 20px; color: #fff;">{logo_h}{s['Platform']}</div>
                    <div style="font-size: 14px; color: #a1a1aa; margin-top: 8px;">Satıcı: {s['Satici']}</div>
                    <div style="font-size: 13px; color: #d1d5db; margin-top: 4px; margin-bottom: 20px; line-height: 1.4;">{s['UrunAdi']}</div>
                    <div style="background: {bg_r}; border: 1px solid {bd_r}; padding: 8px 12px; border-radius: 6px; font-size: 13px; font-weight: bold; color: {'#34d399' if s['Sayi'] > 0 else '#94a3b8'}; margin-bottom:15px; text-align:center;">{s['Sayi']} Yorum Analizi</div>
                    <a href="{s['URL']}" target="_blank" style="margin-top:auto; text-align:center; padding: 10px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color:#fff; text-decoration:none; border-radius:6px; font-weight:bold; font-size:13px;">Ürüne Git ➔</a>
                </div>
                <div style="padding: 30px; flex-grow: 1; font-size: 15px; color: #e4e4e7; line-height: 1.6; display:flex; flex-direction:column; justify-content:center;">
                    {s['Analiz']}
                </div>
            </div>"""

        ai_price_html = ""
        if ai_fiyat_ozet:
            ai_price_html = f"""
            <div style='background:rgba(129,140,248,0.05); border:1px solid rgba(129,140,248,0.2); border-left:4px solid #818cf8; border-radius:12px; padding:25px; margin-bottom:30px; color:#e4e4e7; line-height:1.6;'>
                <b style='color:#818cf8;'>🤖 AI Strateji:</b><br>{ai_fiyat_ozet}
            </div>"""

        no_review_msg = ""
        if not review_results:
            no_review_msg = "<div style='color:#a1a1aa; text-align:center; padding:40px; font-size:15px;'>📭 Yorum analizi için Trendyol veya Hepsiburada linkleri gereklidir.</div>"

        current_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        html_icerik = f"""
        <html>
        <head>
            <meta charset="utf-8">
            <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;700;800&display=swap" rel="stylesheet">
            <style>
                * {{ box-sizing: border-box; }}
                body {{ font-family: 'Plus Jakarta Sans', sans-serif; background: #09090b; color: #f8fafc; padding: 40px; margin:0; }}
                .container {{ max-width: 1100px; margin: auto; }}
                .tabs {{ display: flex; gap: 0; margin-bottom: 40px; border-bottom: 2px solid rgba(255,255,255,0.06); }}
                .tab-btn {{
                    padding: 16px 36px; background: transparent; border: none; color: #71717a; cursor: pointer;
                    font-family: 'Plus Jakarta Sans', sans-serif; font-size: 16px; font-weight: 700;
                    border-bottom: 3px solid transparent; transition: all 0.3s ease; letter-spacing: 0.5px;
                }}
                .tab-btn:hover {{ color: #a1a1aa; }}
                .tab-btn.active {{ color: #818cf8; border-bottom-color: #818cf8; }}
                .tab-content {{ display: none; animation: fadeIn 0.4s ease; }}
                .tab-content.active {{ display: block; }}
                @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
                .stat-grid {{ display: flex; gap: 24px; margin-bottom: 40px; }}
                .stat-card {{ background: rgba(24, 24, 27, 0.55); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 25px; flex: 1; }}
                .stat-num {{ font-size: 32px; font-weight: 800; margin-bottom: 5px; }}
                .stat-label {{ font-size: 12px; color: #a1a1aa; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div style="color:#818cf8; font-weight:bold; letter-spacing:2px; font-size:13px; margin-bottom:5px; text-transform:uppercase;">BMK Veri Odaklı Danışmanlık Hizmeti</div>
                <h1 style="margin:0 0 30px 0; font-size:36px; font-weight:800;">Komple E-Ticaret Analiz Raporu</h1>

                <div class="tabs">
                    <button class="tab-btn active" onclick="switchTab('price')">💰 Fiyat Radarı</button>
                    <button class="tab-btn" onclick="switchTab('review')">🗣️ Yorum Radarı</button>
                </div>

                <div class="tab-content active" id="content-price">
                    <div class="stat-grid">
                        <div class="stat-card"><div class="stat-num" style="color:#fff;">{len(price_results)}</div><div class="stat-label">Karşılaştırılan Platform</div></div>
                        <div class="stat-card"><div class="stat-num" style="color:#818cf8;">{standard_fiyat_formati(resmi_fiyat)}</div><div class="stat-label">Referans Fiyat</div></div>
                    </div>
                    {ai_price_html}
                    {price_cards}
                </div>

                <div class="tab-content" id="content-review">
                    <div class="stat-grid">
                        <div class="stat-card"><div class="stat-num" style="color:#fff;">{len(review_results)}</div><div class="stat-label">Analiz Edilen Ürün</div></div>
                        <div class="stat-card"><div class="stat-num" style="color:#34d399;">{total_reviews}</div><div class="stat-label">İncelenen Yorum Hacmi</div></div>
                        <div class="stat-card"><div class="stat-num" style="color:#818cf8;">Aktif</div><div class="stat-label">Llama-3 Nöral JSON Motoru</div></div>
                    </div>
                    {review_cards}
                    {no_review_msg}
                </div>

                <div style="text-align:center; margin-top:50px; padding-top:20px; border-top:1px solid rgba(255,255,255,0.05); color:#52525b; font-size:14px;">
                    🚀 Bu rapor <b>BMK Veri Odaklı Danışmanlık Hizmeti</b> tarafından üretilmiştir. Rapor Tarihi: {current_time}
                </div>
            </div>

            <script>
                function switchTab(name) {{
                    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                    document.getElementById('content-' + name).classList.add('active');
                    event.target.classList.add('active');
                }}
            </script>
        </body>
        </html>"""

        rapor_yolu = os.path.abspath("BMK_Combined_Report.html")
        with open(rapor_yolu, "w", encoding="utf-8") as f:
            f.write(html_icerik)

        ac_rapor_tarayicida(rapor_yolu)
        durum_etiketi.config(text="Status: Success! Combined Dashboard Opened.", fg="green")
    else:
        durum_etiketi.config(text="Status: Error! No data.", fg="red")


# =========================================================================
# 🏛️ MODULE 3: THE BMK EXECUTIVE PORTAL (OOP GUI)
# =========================================================================
class BMKEnterpriseApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BMK Veri Odaklı Danışmanlık Hizmeti")
        self.geometry("800x720")
        self.configure(bg="#09090b")

        self.container = tk.Frame(self, bg="#09090b")
        self.container.pack(fill="both", expand=True)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        for F in (LandingPage, PricePage, ReviewPage, CombinedPage):
            page_name = F.__name__
            frame = F(parent=self.container, controller=self)
            self.frames[page_name] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        self.show_frame("LandingPage")

    def show_frame(self, page_name):
        frame = self.frames[page_name]
        frame.tkraise()


class LandingPage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg="#09090b")
        self.controller = controller

        center_frame = tk.Frame(self, bg="#09090b")
        center_frame.place(relx=0.5, rely=0.5, anchor="center")

        # --- HEADER ---
        tk.Label(center_frame, text="━━━ BMK VERİ ODAKLI DANIŞMANLIK HİZMETİ ━━━", font=("Helvetica", 10, "bold"),
                 fg="#818cf8", bg="#09090b").pack(pady=(0, 8))
        tk.Label(center_frame, text="Kurumsal E-Ticaret Zeka Platformu", font=("Helvetica", 26, "bold"), fg="#ffffff",
                 bg="#09090b").pack(pady=(0, 6))
        tk.Label(center_frame, text="Yapay Zeka Destekli  •  Gerçek Zamanlı  •  Çoklu Platform",
                 font=("Helvetica", 11), fg="#52525b", bg="#09090b").pack(pady=(0, 25))

        sep = tk.Frame(center_frame, bg="#27272a", height=1)
        sep.pack(fill="x", padx=40, pady=(0, 25))

        mission_text = "Rakiplerinizin fiyat stratejilerini anlık takip edin, müşteri yorumlarını\nyapay zeka ile analiz edin ve pazar avantajı yakalayın."
        tk.Label(center_frame, text=mission_text, font=("Helvetica", 12), fg="#a1a1aa", bg="#09090b",
                 justify="center").pack(pady=(0, 30))

        # --- FEATURE BUTTONS WITH DESCRIPTIONS ---
        btn_frame = tk.Frame(center_frame, bg="#09090b")
        btn_frame.pack()

        # Card 1: Price
        card1 = tk.Frame(btn_frame, bg="#18181b", padx=15, pady=10, highlightbackground="#27272a", highlightthickness=1)
        card1.grid(row=0, column=0, padx=8, sticky="nsew")
        tk.Button(card1, text="💰 Fiyat & Rekabet Radarı", font=("Helvetica", 13, "bold"), fg="black",
                  padx=15, pady=12, width=24, command=lambda: controller.show_frame("PricePage")).pack()
        tk.Label(card1, text="Birden fazla platformda anlık\nfiyat karşılaştırması yapın", font=("Helvetica", 9),
                 fg="#71717a", bg="#18181b", justify="center").pack(pady=(6, 2))

        # Card 2: Review
        card2 = tk.Frame(btn_frame, bg="#18181b", padx=15, pady=10, highlightbackground="#27272a", highlightthickness=1)
        card2.grid(row=0, column=1, padx=8, sticky="nsew")
        tk.Button(card2, text="🗣️ Müşteri İtibar Radarı", font=("Helvetica", 13, "bold"), fg="black",
                  padx=15, pady=12, width=24, command=lambda: controller.show_frame("ReviewPage")).pack()
        tk.Label(card2, text="Müşteri yorumlarını AI ile\nanaliz edin ve özetleyin", font=("Helvetica", 9),
                 fg="#71717a", bg="#18181b", justify="center").pack(pady=(6, 2))

        # Card 3: Combined (full width, highlighted border)
        card3 = tk.Frame(btn_frame, bg="#18181b", padx=15, pady=10, highlightbackground="#818cf8", highlightthickness=1)
        card3.grid(row=1, column=0, columnspan=2, padx=8, pady=(12, 0), sticky="nsew")
        tk.Button(card3, text="🚀 Komple Analiz  —  Fiyat + Yorum Tek Seferde", font=("Helvetica", 13, "bold"), fg="black",
                  padx=15, pady=12, command=lambda: controller.show_frame("CombinedPage")).pack(fill="x")
        tk.Label(card3, text="Her iki analizi tek oturumda çalıştırın  •  Sekmeli HTML rapor oluşturulur",
                 font=("Helvetica", 9), fg="#818cf8", bg="#18181b", justify="center").pack(pady=(6, 2))

        # --- FOOTER ---
        footer = tk.Frame(center_frame, bg="#09090b")
        footer.pack(pady=(30, 0))
        tk.Label(footer, text="Powered by Llama-3 (Groq)  •  v2.0", font=("Helvetica", 9),
                 fg="#3f3f46", bg="#09090b").pack()


class PricePage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg="#09090b")
        self.controller = controller

        header_frame = tk.Frame(self, bg="#09090b")
        header_frame.pack(fill="x", pady=20, padx=20)

        tk.Button(header_frame, text="⬅ Back", font=("Helvetica", 10, "bold"), fg="black",
                  command=lambda: controller.show_frame("LandingPage")).pack(side="left")
        tk.Label(header_frame, text="Fiyat & Rekabet Radarı", font=("Helvetica", 18, "bold"), fg="#f8fafc",
                 bg="#09090b").pack(side="right")

        content = tk.Frame(self, bg="#09090b", padx=20)
        content.pack(fill="both", expand=True)

        frame_api = tk.LabelFrame(content, text="🔑 Groq (Llama-3) API Anahtarı", font=("Helvetica", 10, "bold"),
                                  fg="#94a3b8", bg="#09090b", padx=10, pady=10)
        frame_api.pack(fill="x", pady=5)
        self.api_entry = tk.Entry(frame_api, show="*", font=("Helvetica", 10), bg="#18181b", fg="#f8fafc",
                                  insertbackground="white", relief="solid")
        self.api_entry.pack(expand=True, fill="x")

        frame_links = tk.LabelFrame(content, text="Analiz Edilecek Linkler", font=("Helvetica", 10, "bold"),
                                    fg="#94a3b8", bg="#09090b", padx=10, pady=10)
        frame_links.pack(fill="x", pady=(10, 5))
        tk.Label(frame_links,
                 text="💡 KURAL: İlk satır REFERANS (Baz Alınan) üründür. Rakip linkleri alt satırlara ekleyin.",
                 font=("Helvetica", 10, "bold"), fg="#34d399", bg="#09090b").pack(fill="x", pady=(0, 10))
        self.text_linkler = tk.Text(frame_links, height=10, font=("Helvetica", 9), bg="#18181b", fg="#f8fafc",
                                    insertbackground="white", relief="solid")
        self.text_linkler.pack(fill="x", pady=5)

        self.lbl_status = tk.Label(content, text="Status: Bekleniyor...", font=("Helvetica", 10, "italic"),
                                   fg="#52525b", bg="#09090b")
        self.lbl_status.pack(pady=10)

        tk.Button(content, text="⚡ FİYAT RADARINI BAŞLAT", font=("Helvetica", 12, "bold"), fg="black", pady=8,
                  command=self.baslat).pack(fill="x")

    def baslat(self):
        api = self.api_entry.get().strip()
        raw_lines = self.text_linkler.get("1.0", tk.END).split('\n')
        links = [u.strip() for u in raw_lines if u.strip().startswith('http')]

        if not links:
            return messagebox.showwarning("Uyarı", "Lütfen geçerli bir link giriniz!")
        run_price_radar(links, api, self.lbl_status, self)


class ReviewPage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg="#09090b")
        self.controller = controller

        header_frame = tk.Frame(self, bg="#09090b")
        header_frame.pack(fill="x", pady=20, padx=20)

        tk.Button(header_frame, text="⬅ Back", font=("Helvetica", 10, "bold"), fg="black",
                  command=lambda: controller.show_frame("LandingPage")).pack(side="left")
        tk.Label(header_frame, text="Müşteri İtibar Radarı", font=("Helvetica", 18, "bold"), fg="#f8fafc",
                 bg="#09090b").pack(side="right")

        content = tk.Frame(self, bg="#09090b", padx=20)
        content.pack(fill="both", expand=True)

        frame_api = tk.LabelFrame(content, text="🔑 Groq (Llama-3) API Anahtarı", font=("Helvetica", 10, "bold"),
                                  fg="#94a3b8", bg="#09090b", padx=10, pady=10)
        frame_api.pack(fill="x", pady=5)
        self.api_entry = tk.Entry(frame_api, show="*", font=("Helvetica", 10), bg="#18181b", fg="#f8fafc",
                                  insertbackground="white", relief="solid")
        self.api_entry.pack(expand=True, fill="x")

        frame_links = tk.LabelFrame(content, text="Analiz Edilecek Linkler", font=("Helvetica", 10, "bold"),
                                    fg="#94a3b8", bg="#09090b", padx=10, pady=10)
        frame_links.pack(fill="x", pady=(10, 5))
        tk.Label(frame_links, text="💡 KURAL: Trendyol veya Hepsiburada ürün linklerini alt alta yapıştırın.",
                 font=("Helvetica", 10, "bold"), fg="#34d399", bg="#09090b").pack(fill="x", pady=(0, 10))
        self.text_linkler = tk.Text(frame_links, height=10, font=("Helvetica", 9), bg="#18181b", fg="#f8fafc",
                                    insertbackground="white", relief="solid")
        self.text_linkler.pack(fill="x", pady=5)

        self.lbl_status = tk.Label(content, text="Status: Bekleniyor...", font=("Helvetica", 10, "italic"),
                                   fg="#52525b", bg="#09090b")
        self.lbl_status.pack(pady=10)

        tk.Button(content, text="⚡ YORUM RADARINI BAŞLAT", font=("Helvetica", 12, "bold"), fg="black", pady=8,
                  command=self.baslat).pack(fill="x")

    def baslat(self):
        api = self.api_entry.get().strip()
        raw_lines = self.text_linkler.get("1.0", tk.END).split('\n')
        links = [u.strip() for u in raw_lines if u.strip().startswith('http')]

        if not links:
            return messagebox.showwarning("Uyarı", "Lütfen geçerli bir link giriniz!")
        run_review_radar(links, api, self.lbl_status, self)


class CombinedPage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg="#09090b")
        self.controller = controller

        header_frame = tk.Frame(self, bg="#09090b")
        header_frame.pack(fill="x", pady=20, padx=20)

        tk.Button(header_frame, text="⬅ Back", font=("Helvetica", 10, "bold"), fg="black",
                  command=lambda: controller.show_frame("LandingPage")).pack(side="left")
        tk.Label(header_frame, text="🚀 Komple Analiz (Fiyat + Yorum)", font=("Helvetica", 18, "bold"), fg="#f8fafc",
                 bg="#09090b").pack(side="right")

        content = tk.Frame(self, bg="#09090b", padx=20)
        content.pack(fill="both", expand=True)

        frame_api = tk.LabelFrame(content, text="🔑 Groq (Llama-3) API Anahtarı", font=("Helvetica", 10, "bold"),
                                  fg="#94a3b8", bg="#09090b", padx=10, pady=10)
        frame_api.pack(fill="x", pady=5)
        self.api_entry = tk.Entry(frame_api, show="*", font=("Helvetica", 10), bg="#18181b", fg="#f8fafc",
                                  insertbackground="white", relief="solid")
        self.api_entry.pack(expand=True, fill="x")

        frame_links = tk.LabelFrame(content, text="Analiz Edilecek Linkler", font=("Helvetica", 10, "bold"),
                                    fg="#94a3b8", bg="#09090b", padx=10, pady=10)
        frame_links.pack(fill="x", pady=(10, 5))
        tk.Label(frame_links,
                 text="💡 İlk satır REFERANS üründür. Trendyol/HB linkleri için yorum da analiz edilir.",
                 font=("Helvetica", 10, "bold"), fg="#34d399", bg="#09090b").pack(fill="x", pady=(0, 10))
        self.text_linkler = tk.Text(frame_links, height=10, font=("Helvetica", 9), bg="#18181b", fg="#f8fafc",
                                    insertbackground="white", relief="solid")
        self.text_linkler.pack(fill="x", pady=5)

        self.lbl_status = tk.Label(content, text="Status: Bekleniyor...", font=("Helvetica", 10, "italic"),
                                   fg="#52525b", bg="#09090b")
        self.lbl_status.pack(pady=10)

        tk.Button(content, text="⚡ KOMPLE ANALİZİ BAŞLAT", font=("Helvetica", 12, "bold"), fg="black", pady=8,
                  command=self.baslat).pack(fill="x")

    def baslat(self):
        api = self.api_entry.get().strip()
        raw_lines = self.text_linkler.get("1.0", tk.END).split('\n')
        links = [u.strip() for u in raw_lines if u.strip().startswith('http')]

        if not links:
            return messagebox.showwarning("Uyarı", "Lütfen geçerli bir link giriniz!")
        run_combined_radar(links, api, self.lbl_status, self)


# =========================================================================
# 🏁 THE FINAL TRIGGER
# =========================================================================
if __name__ == "__main__":
    app = BMKEnterpriseApp()
    app.mainloop()
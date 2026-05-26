"""
services/scraping/parsers.py — Scraper paylaşımlı parser/yardımcı fonksiyonları.

Bu modül, daha önce bmk_suite.py (eski tkinter desktop sürümü) içinde duran
yardımcıları içerir. worker.py tek tüketicidir.

İçindekiler:
    fiyati_temizle(s)            — "1.299,90 TL" → 1299.9 float
    standard_fiyat_formati(n)    — 1299.9 → "1.299,90 TL"
    urun_ismi_temizle(s)         — "X fiyatları" gibi UI gürültüsünü ayıklar
    marka_adi_bul(url)           — URL'den kanonik pazaryeri ismi
    get_domain(url)              — URL'den netloc (www. çıkarılmış)
    BANNED_UI_PHRASES            — Yorumlardan elenecek UI sentence'leri
"""
import re
from urllib.parse import urlparse


def fiyati_temizle(fiyat_str):
    """'1.299,90 TL' / '$1,299.90' / '5000,99' gibi rakamları float'a çevir.

    Türkçe format öncelikli: nokta = binlik ayracı, virgül = ondalık.
    Hiçbir şey bulunamazsa 0.0 döner (asla raise etmez — scraper akışı bozulmasın).
    """
    try:
        if not fiyat_str:
            return 0.0

        # Türkçe format: 26.499,24 | 1.350,00 | 135,13 | 829,99 | 5000,99
        tr_match = re.search(r'(\d+(?:\.\d{3})*,\d{2})', fiyat_str)
        if tr_match:
            price_str = tr_match.group(1).replace('.', '').replace(',', '.')
            return float(price_str)

        # Fallback: standart olmayan formatlar
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
    except Exception:
        return 0.0


def standard_fiyat_formati(sayi_float):
    """Float'ı Türkçe formatta string'e çevir. 1299.9 → '1.299,90 TL'."""
    if sayi_float <= 0:
        return "Fiyat Bilgisi Yok"
    try:
        formatted_str = "{:,.2f}".format(sayi_float)
        # virgül↔nokta swap (en-US → tr-TR)
        table = str.maketrans({',': '.', '.': ','})
        return f"{formatted_str.translate(table)} TL"
    except Exception:
        return "Hata"


def urun_ismi_temizle(isim):
    """Ürün başlığından SEO/UI gürültüsünü ayıkla.

    'Bosch Süpürge fiyatları' → 'Bosch Süpürge'
    """
    if not isim:
        return "İsim Bulunamadı"
    junk_words = [
        r"\bfiyatı\b", r"\bfiyatları\b",
        r"\byorumları\b", r"\byorum\b",
        r"\bözellikleri\b", r"\bsatın al\b",
    ]
    for j in junk_words:
        isim = re.sub(j, "", isim, flags=re.IGNORECASE)
    return isim.strip(' ,-|')


def marka_adi_bul(url):
    """URL'den kanonik pazaryeri ismi.

    Bilinen pazaryerleri sabit eşleme; geri kalan için domain capitalize.
    """
    try:
        domain = urlparse(url).netloc.replace("www.", "").lower()
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
    except Exception:
        return "Bilinmeyen Site"


def get_domain(url):
    """URL → 'trendyol.com' (www. çıkarılmış)."""
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


# Yorum analizinde elenecek pazaryeri UI cümleleri (gerçek müşteri yorumu değil)
BANNED_UI_PHRASES = [
    "yayımı reddedilebilmekte", "sağlık beyanı", "tıbbi öneri", "cayma hakkı",
    "sözleşmenin feshi", "deneyimlerini paylaşabilecekleri", "gecikmeksizin bildirilecektir",
    "hukuka aykırı", "kriterlere aykırı", "değerlendirme yap", "ilk değerlendiren",
    "değerlendirme bulunmuyor", "satıcıya sor", "iade koşulları", "ürün açıklaması",
    "taksit seçenekleri", "sponsorlu", "sipariş iptali", "taksit", "kargo",
    "satın aldıkları ürünlere", "sepete ekle", "tümünü gör", "ücretsiz kargo",
    "yarın kapında", "ortalama puan", "veri işleme", "aydınlatma metni", "kapat",
    "favorilere ekle", "ürün özellikleri", "ürün değerlendirmeleri",
    "yardımcı oldu mu", "evet", "hayır", "yanıtla",
    "tükendi", "tükenmek üzere", "ödeme koruma", "gelince haber ver",
    "satıcıya git", "teslimat adresi", "satın al",
]

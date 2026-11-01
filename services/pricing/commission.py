"""
services/pricing/commission.py — Maliyet/Başabaş Hesaplayıcı (Mod 2).

Satıcı ürünü X'e alır, Y'ye satar — ama komisyon + KDV + kargoyu unutup aslında
zarar edebilir. Bu modül "gerçek maliyeti / başabaş satış fiyatını" hesaplar.

MANTIK (brüt/basit yaklaşım — açıkça "tahmini" etiketlenir):
    kesinti% = komisyon% + (KDV% eğer dahilse)
    başabaş   = (alış + kargo) / (1 − kesinti%/100)
Yani: "zarar etmemek için en az bu fiyata satmalısın."
Komisyon SATIŞ fiyatı üzerinden alındığı için formül orantısaldır.

Oran tablosu DÜZENLENEBİLİR VARSAYILANDIR — kullanıcı kendi sözleşmesindeki oranla
değiştirebilir. Bu yüzden tam güncel olmaları şart değil; başlangıç noktasıdırlar.
Son güncelleme (varsayılanlar): 2026-11 — kullanıcı kendi oranını teyit etmeli.
"""

# key: {label, kdv, ty (Trendyol komisyon %), hb (Hepsiburada komisyon %)}
# Oranlar TAHMİNİ VARSAYILAN; kategoriye göre tipik aralıklar. Kullanıcı düzenler.
CATEGORY_RATES = [
    {"key": "elektronik",   "label": "Elektronik",                 "kdv": 20, "ty": 11, "hb": 12},
    {"key": "telefon",      "label": "Telefon & Aksesuar",         "kdv": 20, "ty": 11, "hb": 13},
    {"key": "bilgisayar",   "label": "Bilgisayar & Tablet",        "kdv": 20, "ty": 10, "hb": 11},
    {"key": "ev_yasam",     "label": "Ev & Yaşam",                 "kdv": 20, "ty": 16, "hb": 18},
    {"key": "mobilya",      "label": "Mobilya",                    "kdv": 20, "ty": 15, "hb": 17},
    {"key": "mutfak",       "label": "Mutfak Gereçleri",           "kdv": 20, "ty": 16, "hb": 18},
    {"key": "moda",         "label": "Moda & Giyim",               "kdv": 10, "ty": 21, "hb": 22},
    {"key": "ayakkabi",     "label": "Ayakkabı & Çanta",           "kdv": 10, "ty": 20, "hb": 21},
    {"key": "kozmetik",     "label": "Kozmetik & Kişisel Bakım",   "kdv": 20, "ty": 17, "hb": 19},
    {"key": "anne_bebek",   "label": "Anne & Bebek",               "kdv": 10, "ty": 15, "hb": 17},
    {"key": "petshop",      "label": "Evcil Hayvan (Pet Shop)",    "kdv": 20, "ty": 15, "hb": 16},
    {"key": "spor",         "label": "Spor & Outdoor",             "kdv": 20, "ty": 16, "hb": 18},
    {"key": "oyuncak",      "label": "Oyuncak & Hobi",             "kdv": 20, "ty": 15, "hb": 16},
    {"key": "kitap_kirtasiye","label": "Kitap & Kırtasiye",        "kdv": 10, "ty": 13, "hb": 14},
    {"key": "otomotiv",     "label": "Otomotiv & Yedek Parça",     "kdv": 20, "ty": 13, "hb": 15},
    {"key": "yapi_bahce",   "label": "Yapı Market & Bahçe",        "kdv": 20, "ty": 14, "hb": 16},
    {"key": "taki_saat",    "label": "Takı & Saat",                "kdv": 20, "ty": 20, "hb": 21},
    {"key": "supermarket",  "label": "Süpermarket & Gıda",         "kdv": 10, "ty": 10, "hb": 11},
    {"key": "diger",        "label": "Diğer / Genel",              "kdv": 20, "ty": 15, "hb": 17},
]

_BY_KEY = {c["key"]: c for c in CATEGORY_RATES}


def get_category(key):
    return _BY_KEY.get(key)


def resolve_commission(category_key, channel):
    """Kategori + kanala göre komisyon %'si döndürür.

    channel: 'trendyol' | 'hepsiburada' | '' (belirsiz → İKİSİNDEN YÜKSEK OLANI).
    Dönüş: (commission_pct: float, kdv_pct: float, note: str)
    """
    cat = _BY_KEY.get(category_key) or _BY_KEY["diger"]
    ty, hb, kdv = float(cat["ty"]), float(cat["hb"]), float(cat["kdv"])
    ch = (channel or "").strip().lower()
    if ch == "trendyol":
        return ty, kdv, "Trendyol komisyonu"
    if ch == "hepsiburada":
        return hb, kdv, "Hepsiburada komisyonu"
    # Belirsiz → temkinli: yüksek olanı
    hi = max(ty, hb)
    return hi, kdv, "Kanal belirsiz — yüksek komisyon (temkinli)"


def compute_breakeven(purchase, category_key, channel="", shipping=0.0,
                      vat_included=True, commission_override=None):
    """Başabaş satış fiyatını hesaplar.

    Args:
        purchase: alış/tedarik fiyatı (₺) — zorunlu, > 0
        category_key: CATEGORY_RATES key'i
        channel: '' | 'trendyol' | 'hepsiburada'
        shipping: opsiyonel kargo maliyeti (₺)
        vat_included: KDV'yi kesintiye kat (True) / katma (False)
        commission_override: kullanıcı komisyon %'sini elle girdiyse

    Returns: dict (hata varsa {"error": ...})
    """
    try:
        purchase = float(purchase)
    except (TypeError, ValueError):
        return {"error": "Geçerli bir alış fiyatı girin."}
    if purchase <= 0:
        return {"error": "Alış fiyatı 0'dan büyük olmalı."}
    try:
        shipping = max(0.0, float(shipping or 0))
    except (TypeError, ValueError):
        shipping = 0.0

    comm_pct, kdv_pct, note = resolve_commission(category_key, channel)
    if commission_override is not None:
        try:
            ov = float(commission_override)
            if 0 <= ov <= 90:
                comm_pct, note = ov, "Kullanıcı komisyon oranı"
        except (TypeError, ValueError):
            pass

    kdv_applied = kdv_pct if vat_included else 0.0
    deduction = comm_pct + kdv_applied  # % toplam orantısal kesinti
    if deduction >= 95:
        return {"error": "Komisyon + KDV toplamı çok yüksek (≥%95) — hesaplanamıyor."}

    breakeven = (purchase + shipping) / (1 - deduction / 100.0)
    return {
        "breakeven": round(breakeven, 2),
        "purchase": round(purchase, 2),
        "shipping": round(shipping, 2),
        "commission_pct": round(comm_pct, 2),
        "kdv_pct": round(kdv_pct, 2),
        "kdv_included": bool(vat_included),
        "deduction_pct": round(deduction, 2),
        "note": note,
    }

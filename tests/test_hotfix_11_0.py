"""
tests/test_hotfix_11_0.py — HOTFIX 11.0: Hepsiburada satıcı + yorum + SKU onarımı.

Canlı test (Nobera Kedi Köpek Tüy Toplayıcı) ile tespit edilen 4 bug:
  1. SKU regex -p-([A-Z0-9]+) → -pm- formatlı URL'leri kaçırıyordu
  2. __NEXT_DATA__ HB'de kaldırıldı; satıcı artık SADECE ld+json offers.seller'da
     ama _parse_hb_html bu alanı hiç okumuyordu → "Satıcı: Bulunamadı"
  3. Yorumlar artık Product İÇİNDE değil, ayrı @type=Review ld+json blokları;
     kod sadece Product.review okuyordu → "0 Yorum"
  4. Eski review API host'ları (user-content-gw-api, hermes) DNS-dead

Test stratejisi: curl_cffi.requests.get mock'lanır, gerçek HB ld+json yapısını
taklit eden fixture HTML beslenir. Ağ çağrısı yapılmaz — izole, deterministik.
"""
import re

import pytest

import worker


# ─────────────────────────────────────────────────────────────────────────────
# 1) SKU regex — -pm- formatı
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://www.hepsiburada.com/x-pm-HBC0000AS54Y9", "HBC0000AS54Y9"),
    ("https://www.hepsiburada.com/y-p-HBV00000ABCDE", "HBV00000ABCDE"),
    ("https://www.hepsiburada.com/urun-adi-pm-HB0001XYZ", "HB0001XYZ"),
])
def test_sku_regex_handles_both_p_and_pm(url, expected):
    """HOTFIX 11.0: hem -p- hem -pm- SKU formatı yakalanmalı."""
    m = re.search(r'-pm?-([A-Z0-9]+)', url)
    assert m is not None, f"SKU çıkmadı: {url}"
    assert m.group(1) == expected


def test_extract_hepsiburada_product_id_pm_format():
    """_extract_hepsiburada_product_id -pm- formatını çözmeli."""
    sku = worker._extract_hepsiburada_product_id(
        "https://www.hepsiburada.com/kedi-tuy-toplayici-pm-HBC0000AS54Y9"
    )
    assert sku == "HBC0000AS54Y9"


# ─────────────────────────────────────────────────────────────────────────────
# 2+3) Satıcı + standalone Review — ld+json parse (curl_cffi mock'lu)
# ─────────────────────────────────────────────────────────────────────────────

# Gerçek HB sayfasının ld+json yapısını taklit eden minimal fixture.
# Önemli: satıcı offers.seller.name'de, yorumlar AYRI @type=Review bloklarında.
_HB_FIXTURE_HTML = """<!doctype html><html><head>
<script type="application/ld+json">
{
  "@type": "Product",
  "name": "Nobera Kedi Köpek Tüy Toplayıcı",
  "offers": {
    "@type": "Offer",
    "price": "195.00",
    "seller": {"@type": "Organization", "name": "Nobera"}
  },
  "aggregateRating": {"@type": "AggregateRating", "ratingValue": 4.6, "ratingCount": 184}
}
</script>
<script type="application/ld+json">
[
  {"@type": "Review", "reviewBody": "Anlatıldığı gibi ürünü beğendim, işini yapıyor çıkan tüylere şaşırdık."},
  {"@type": "Review", "reviewBody": "Muhteşem bir ürünmüş, gerçekten harika, kedim koltuklarda yattıktan sonra."},
  {"@type": "Review", "reviewBody": "Koltukta oturmak işkenceye dönüşmüştü, bununla daha pratik oldu."}
]
</script>
</head><body></body></html>"""


class _FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def json(self):
        import json as _j
        return _j.loads(self.text)


@pytest.fixture
def _mock_cffi(monkeypatch):
    """curl_cffi.requests.get → ana sayfa fixture'ı döndürür, API/yorum
    endpoint'leri 404 (canlıda da ölü olduklarını kanıtladık)."""
    from curl_cffi import requests as cffi_requests

    def fake_get(url, *args, **kwargs):
        # Ana ürün sayfası ve -yorumlari sayfası fixture HTML döner
        if "hepsiburada.com" in url and "/api/" not in url and "/reviews" not in url:
            return _FakeResp(_HB_FIXTURE_HTML, 200)
        # Ölü API host'ları / review API'ları
        return _FakeResp("not found", 404)

    monkeypatch.setattr(cffi_requests, "get", fake_get)
    return fake_get


def test_hb_seller_extracted_from_ldjson(_mock_cffi):
    """HOTFIX 11.0 Bug 2: satıcı ld+json offers.seller.name'den gelmeli."""
    url = "https://www.hepsiburada.com/nobera-tuy-toplayici-pm-HBC0000AS54Y9"
    result = worker._scrape_hepsiburada_cffi(url, fetch_reviews=True)
    assert result is not None
    assert result.get("seller") == "Nobera", (
        f"Satıcı çıkmadı: {result.get('seller')!r} (eskiden 'Bulunamadı' oluyordu)"
    )


def test_hb_standalone_reviews_extracted(_mock_cffi):
    """HOTFIX 11.0 Bug 3: ayrı @type=Review blokları yorum olarak çekilmeli."""
    url = "https://www.hepsiburada.com/nobera-tuy-toplayici-pm-HBC0000AS54Y9"
    result = worker._scrape_hepsiburada_cffi(url, fetch_reviews=True)
    assert result is not None
    reviews = result.get("reviews", [])
    assert len(reviews) >= 3, f"Yorum çekilmedi (len={len(reviews)}), eskiden 0'dı"
    assert any("beğendim" in r for r in reviews)


def test_hb_price_and_rating_still_work(_mock_cffi):
    """Regresyon: fiyat + rating + review_count bozulmadan gelmeli."""
    url = "https://www.hepsiburada.com/nobera-tuy-toplayici-pm-HBC0000AS54Y9"
    result = worker._scrape_hepsiburada_cffi(url, fetch_reviews=True)
    assert result is not None
    assert str(result.get("price")) == "195.00"
    assert result.get("rating") == 4.6
    assert result.get("review_count") == 184


def test_hb_result_dict_has_seller_key():
    """result/parse dict'i her zaman 'seller' anahtarını içermeli (KeyError önler)."""
    # Boş HTML → None dönebilir ama dict şeması bozulmamalı.
    # Doğrudan parse fonksiyonu nested olduğu için public fonksiyonun
    # sözleşmesini mock ile doğruluyoruz: seller anahtarı var.
    from curl_cffi import requests as cffi_requests

    class _R:
        status_code = 200
        text = '<html><script type="application/ld+json">{"@type":"Product","name":"X","offers":{"price":"10"}}</script></html>'
        def json(self): return {}

    import pytest as _pt
    with _pt.MonkeyPatch.context() as mp:
        mp.setattr(cffi_requests, "get", lambda *a, **k: _R())
        result = worker._scrape_hepsiburada_cffi(
            "https://www.hepsiburada.com/x-pm-HBC123", fetch_reviews=False
        )
        assert result is not None
        assert "seller" in result  # anahtar her durumda var
        assert result["seller"] is None  # bu fixture'da satıcı yok

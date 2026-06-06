"""
tests/test_hotfix_11_1.py — HOTFIX 11.1: Hepsiburada SEO arama sırası takibi.

HOTFIX 1.23'te HB SEO "DataDome proxy gerekir" diye kapatılıp sentinel (-1,-1)
döndürüyordu. Canlı test (2026-06) bunu çürüttü: HB arama sayfaları curl_cffi
ile sorunsuz çekiliyor. Bu testler curl_cffi'yi mock'layarak _track_keyword_
hepsiburada'nın rank hesabını izole doğrular (ağ çağrısı yok).
"""
import pytest

import worker


def _search_html(skus):
    """Verilen SKU listesinden sahte HB arama sonuç HTML'i üret (document order)."""
    links = "".join(
        f'<a href="/bir-urun-adi-pm-{sku}">Ürün {i}</a>' for i, sku in enumerate(skus)
    )
    return f"<!doctype html><html><body><div class='products'>{links}</div></body></html>"


class _FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


@pytest.fixture
def _mock_search(monkeypatch):
    """curl_cffi.requests.get → sayfa numarasına göre farklı SKU seti döndürür.
    page1: 5 ürün (hedef yok), page2: hedef 3. sırada."""
    from curl_cffi import requests as cffi_requests

    PAGE1 = ["HBC00000001", "HBC00000002", "HBC00000003", "HBC00000004", "HBC00000005"]
    PAGE2 = ["HBC00000010", "HBC00000011", "HBC0000AS54Y9", "HBC00000012"]  # hedef 3.

    def fake_get(url, *args, **kwargs):
        if "sayfa=2" in url:
            return _FakeResp(_search_html(PAGE2))
        return _FakeResp(_search_html(PAGE1))

    monkeypatch.setattr(cffi_requests, "get", fake_get)
    return fake_get


def test_hb_seo_finds_target_with_pagination(_mock_search):
    """Hedef SKU 2. sayfada 3. sırada → (2, 3) dönmeli."""
    url = "https://www.hepsiburada.com/nobera-pm-HBC0000AS54Y9"
    page, rank = worker._track_keyword_hepsiburada("kedi tüy toplayıcı", url, max_pages=5)
    assert (page, rank) == (2, 3), f"Beklenen (2,3), gelen ({page},{rank})"


def test_hb_seo_rank_on_first_page(monkeypatch):
    """Hedef 1. sayfada 1. sırada → (1, 1)."""
    from curl_cffi import requests as cffi_requests
    html = _search_html(["HBC0000AS54Y9", "HBC00000002", "HBC00000003"])
    monkeypatch.setattr(cffi_requests, "get", lambda *a, **k: _FakeResp(html))
    url = "https://www.hepsiburada.com/nobera-pm-HBC0000AS54Y9"
    page, rank = worker._track_keyword_hepsiburada("kedi tüy toplayıcı", url, max_pages=5)
    assert (page, rank) == (1, 1)


def test_hb_seo_not_found_returns_zero(monkeypatch):
    """Hedef hiçbir sayfada yoksa (0, 0) — sentinel (-1,-1) DEĞİL."""
    from curl_cffi import requests as cffi_requests
    html = _search_html(["HBC00000001", "HBC00000002"])
    monkeypatch.setattr(cffi_requests, "get", lambda *a, **k: _FakeResp(html))
    url = "https://www.hepsiburada.com/nobera-pm-HBC0000AS54Y9"
    page, rank = worker._track_keyword_hepsiburada("alakasız kelime", url, max_pages=3)
    assert (page, rank) == (0, 0)


def test_hb_seo_no_sentinel_minus_one():
    """HOTFIX 11.1: fonksiyon ARTIK (-1,-1) döndürmemeli (modül aktif).
    Geçersiz URL bile (0,0) döner, sentinel değil."""
    page, rank = worker._track_keyword_hepsiburada("kelime", "https://x.com/no-sku", max_pages=1)
    assert (page, rank) == (0, 0)
    assert (page, rank) != (-1, -1)


def test_hb_seo_bot_detection_graceful(monkeypatch):
    """DataDome/captcha tespit edilirse zarif çıkış (0,0), patlamaz."""
    from curl_cffi import requests as cffi_requests
    monkeypatch.setattr(cffi_requests, "get",
                        lambda *a, **k: _FakeResp("<html>datadome captcha</html>"))
    url = "https://www.hepsiburada.com/nobera-pm-HBC0000AS54Y9"
    page, rank = worker._track_keyword_hepsiburada("kelime", url, max_pages=3)
    assert (page, rank) == (0, 0)

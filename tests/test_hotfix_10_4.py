"""
tests/test_hotfix_10_4.py — HOTFIX 10.4: Grafik lejant izole/kıyasla davranışı.

Helper static/js/isolate-legend.js'i yükleyen ve uygulayan template
entegrasyonunu doğrular. JS mantığı (legendClick algoritması) saf JavaScript
olduğu için pytest scope'unda doğrulanmıyor — onun için manuel/integration
test gerekir. Burada doğrulanan: script dosyası mevcut, doğru yerden include
ediliyor, applyIsolateLegendBehavior çağrısı template'te bulunuyor.
"""
import os
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Static asset varlığı
# ─────────────────────────────────────────────────────────────────────────────

def test_isolate_legend_js_file_exists():
    """static/js/isolate-legend.js dosyası mevcut ve okunabilir."""
    path = Path(__file__).resolve().parent.parent / 'static' / 'js' / 'isolate-legend.js'
    assert path.exists(), f"Helper bulunamadı: {path}"
    content = path.read_text(encoding='utf-8')
    # Helper fonksiyonu globalde yayınlanmalı
    assert 'window.applyIsolateLegendBehavior' in content
    # 3 ana durumu temsil eden kod blokları
    assert 'hideSeries' in content
    assert 'showSeries' in content
    assert 'collapsedSeriesIndices' in content


def test_isolate_legend_js_served_by_flask(client):
    """Flask static endpoint script'i 200 ile dönmeli."""
    r = client.get('/static/js/isolate-legend.js')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'applyIsolateLegendBehavior' in body


# ─────────────────────────────────────────────────────────────────────────────
# Template entegrasyonu
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATES = Path(__file__).resolve().parent.parent / 'templates'


def _read_template(name):
    """Template dosyasının ham içeriğini disk'ten oku (Jinja render etmeden).
    Render edilmiş HTML'de ürün yoksa chart döngüsü çalışmaz → çağrıları
    göremeyiz. Source'a bakmak daha güvenilir."""
    return (TEMPLATES / name).read_text(encoding='utf-8')


def test_tracked_products_template_uses_isolate_helper():
    """tracked_products.html source'unda helper include + çağrı VAR."""
    src = _read_template('tracked_products.html')

    # Helper script tag'i ApexCharts'tan SONRA include edilmiş
    apex_pos = src.find('cdn.jsdelivr.net/npm/apexcharts')
    helper_pos = src.find("filename='js/isolate-legend.js'")
    assert apex_pos > 0, "ApexCharts CDN include yok"
    assert helper_pos > 0, "isolate-legend.js include yok"
    assert apex_pos < helper_pos, "Helper, ApexCharts'tan ÖNCE yükleniyor"

    # Helper çağrısı chart.render() öncesinde
    call_pos = src.find('applyIsolateLegendBehavior(options)')
    render_pos = src.find('chart.render()')
    assert call_pos > 0, "applyIsolateLegendBehavior çağrısı yok"
    assert render_pos > 0
    assert call_pos < render_pos, "Helper, chart.render() SONRASINDA çağrılıyor (çok geç)"


def test_seo_graph_template_uses_isolate_helper():
    """seo_graph.html source'unda helper include + çağrı VAR."""
    src = _read_template('seo_graph.html')

    apex_pos = src.find('cdn.jsdelivr.net/npm/apexcharts')
    helper_pos = src.find("filename='js/isolate-legend.js'")
    assert apex_pos > 0
    assert helper_pos > 0
    assert apex_pos < helper_pos

    call_pos = src.find('applyIsolateLegendBehavior(options)')
    render_pos = src.find('chart.render()')
    assert call_pos > 0
    assert call_pos < render_pos


def test_tracked_products_html_serves_helper_script_tag(auth_client):
    """Rendered HTML'de helper script tag'i mevcut (Jinja url_for çözüldü)."""
    r = auth_client.get('/tracked-products')
    assert r.status_code == 200
    assert b'/static/js/isolate-legend.js' in r.data


def test_seo_graph_html_serves_helper_script_tag(auth_client):
    """Rendered HTML'de helper script tag'i mevcut."""
    r = auth_client.get('/seo-graph')
    assert r.status_code == 200
    assert b'/static/js/isolate-legend.js' in r.data

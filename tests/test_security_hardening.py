"""
tests/test_security_hardening.py — Launch öncesi güvenlik sertleştirmesi.

Kapsam:
  • Her yanıtta güvenlik başlıkları (nosniff, X-Frame-Options, Referrer-Policy)
  • ProductionConfig güvenli çerez ayarları
  • Özel hata sayfaları (404 → error.html)
"""


def test_security_headers_present(client):
    """Her yanıt temel güvenlik başlıklarını taşımalı."""
    r = client.get('/healthz')
    assert r.headers.get('X-Content-Type-Options') == 'nosniff'
    assert r.headers.get('X-Frame-Options') == 'SAMEORIGIN'
    assert r.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'
    assert 'Permissions-Policy' in r.headers


def test_hsts_not_set_on_plain_http(client):
    """Dev (http) isteklerinde HSTS EKLENMEMELİ — aksi halde lokal kilitlenir."""
    r = client.get('/healthz')  # test client http
    assert 'Strict-Transport-Security' not in r.headers


def test_404_renders_custom_error_page(client):
    """Olmayan sayfa → özel error.html (çıplak Flask 404 değil)."""
    r = client.get('/boyle-bir-sayfa-yok-12345')
    assert r.status_code == 404
    body = r.data.decode('utf-8')
    assert 'bulunamadı' in body.lower() or '404' in body


def test_production_config_secure_cookies():
    """ProductionConfig güvenli çerez + debug kapalı olmalı."""
    from config import ProductionConfig
    assert ProductionConfig.DEBUG is False
    assert ProductionConfig.SESSION_COOKIE_SECURE is True
    assert ProductionConfig.SESSION_COOKIE_HTTPONLY is True
    assert ProductionConfig.SESSION_COOKIE_SAMESITE == 'Lax'
    assert ProductionConfig.PREFERRED_URL_SCHEME == 'https'


def test_production_config_inherits_base():
    """ProductionConfig, Config'in temel ayarlarını miras almalı (broker vb.)."""
    from config import ProductionConfig, Config
    assert issubclass(ProductionConfig, Config)
    assert ProductionConfig.SQLALCHEMY_TRACK_MODIFICATIONS is False

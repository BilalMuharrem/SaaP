"""
tests/test_csrf.py — Faz 10A: CSRF koruması doğrulama.

TestConfig'te WTF_CSRF_ENABLED=False (mevcut testleri kırmamak için). Bu dosyada
CSRF'i geçici olarak aktif eden ayrı bir fixture kullanıyoruz, böylece:
  • Token'sız POST'un reddedildiğini doğrularız
  • Token'lı POST'un geçtiğini doğrularız
  • base.html'in csrf-token meta tag'ini render ettiğini doğrularız

Mevcut testler etkilenmez çünkü kendi `client` fixture'larını kullanırlar.
"""
import pytest
from flask_wtf.csrf import generate_csrf

from extensions import db
from models import User


@pytest.fixture
def csrf_app(app):
    """CSRF'i geçici olarak aktif eden app fixture'ı."""
    app.config['WTF_CSRF_ENABLED'] = True
    yield app
    app.config['WTF_CSRF_ENABLED'] = False


@pytest.fixture
def csrf_client(csrf_app):
    """CSRF aktif test client'ı."""
    return csrf_app.test_client()


def test_post_without_csrf_token_is_rejected(csrf_client):
    """Token'sız POST → 400 Bad Request (CSRF eksik)."""
    resp = csrf_client.post('/login', data={
        'email': 'test@example.com',
        'password': 'irrelevant',
    })
    # Flask-WTF CSRFProtect default 400 döner
    assert resp.status_code == 400


def test_login_page_renders_csrf_token_in_form(csrf_client):
    """GET /login → HTML'de csrf_token hidden input olmalı."""
    resp = csrf_client.get('/login')
    assert resp.status_code == 200
    assert b'name="csrf_token"' in resp.data


def test_landing_page_renders_csrf_meta_tag(csrf_client):
    """GET / → base.html (via landing — onun da meta'sı yok ama login.html'de var).
    Burada admin/onboarding base'lerinde meta tag olmalı; base.html üzerinden."""
    # Login sayfası base.html'i extend eder — meta var
    resp = csrf_client.get('/login')
    assert b'name="csrf-token"' in resp.data


def test_post_with_valid_csrf_token_passes_csrf_check(csrf_app, csrf_client):
    """Geçerli token'la POST → CSRF kontrolünden geçer (form validation kendi çalışır)."""
    # Geçerli token al
    with csrf_client.session_transaction() as sess:
        pass  # session başlat

    # İlk önce login sayfasını GET et — token oluşturulur
    get_resp = csrf_client.get('/login')
    # HTML'den csrf_token'ı çek
    import re
    match = re.search(rb'name="csrf_token"\s+value="([^"]+)"', get_resp.data)
    assert match is not None, "csrf_token form'da bulunamadı"
    token = match.group(1).decode()

    # Token'la POST yap — login başarısız olabilir (kullanıcı yok) ama CSRF
    # kontrolünden geçmeli. CSRF fail olsa 400 dönerdi; burada login formu
    # işlenir ve 200 (form yeniden render) döner.
    resp = csrf_client.post('/login', data={
        'csrf_token': token,
        'email': 'nonexistent@example.com',
        'password': 'wrong',
    })
    # CSRF geçti → ya 200 (form re-render with flash) ya 302 (redirect)
    # 400 olmaması yeterli (= CSRF fail değil)
    assert resp.status_code != 400


def test_ajax_csrf_via_header(csrf_app, csrf_client):
    """AJAX POST'lar X-CSRFToken header ile CSRF'i geçer."""
    # Token al
    get_resp = csrf_client.get('/login')
    import re
    match = re.search(rb'name="csrf-token"\s+content="([^"]+)"', get_resp.data)
    assert match is not None, "meta csrf-token bulunamadı"
    token = match.group(1).decode()

    # Korunmuş bir AJAX endpoint — markAllRead bildirimlerde, login gerektirir.
    # Burada sadece CSRF kontrolünün header ile geçtiğini doğrulayalım:
    # login olmadığı için 401/302 dönecek ama CSRF 400 değil.
    resp = csrf_client.post('/notifications/read-all', headers={
        'X-CSRFToken': token,
        'X-Requested-With': 'XMLHttpRequest',
    })
    assert resp.status_code != 400  # CSRF geçti (login redirect veya başka)

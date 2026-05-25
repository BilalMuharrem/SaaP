"""tests/test_auth.py — Giriş, kayıt, çıkış, rate limit."""
import pytest

from models import User
from extensions import db


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────────────────────────────────

def test_login_with_valid_credentials(client, app, starter_user):
    with app.app_context():
        # starter_user fixture password 'testpass123' ile oluşturuldu
        # Fixture içinde User session'a bağlı, email'i alalım
        email = User.query.filter_by(id=starter_user.id).first().email

    r = client.post('/login', data={'email': email, 'password': 'testpass123'},
                    follow_redirects=False)
    # Başarılı login → /dashboard (veya admin → /admin)
    assert r.status_code == 302
    assert '/dashboard' in r.headers['Location'] or '/admin' in r.headers['Location']


def test_login_with_wrong_password(client, app, starter_user):
    with app.app_context():
        email = User.query.filter_by(id=starter_user.id).first().email

    r = client.post('/login', data={'email': email, 'password': 'wrongpass'})
    assert r.status_code == 200  # Login formuna geri döner
    assert b'Ge\xc3\xa7ersiz' in r.data or 'Geçersiz' in r.data.decode('utf-8', errors='ignore')


def test_login_with_unknown_email(client):
    r = client.post('/login', data={'email': 'noone@nowhere.test', 'password': 'x'})
    assert r.status_code == 200
    assert 'Geçersiz' in r.data.decode('utf-8', errors='ignore')


def test_login_pending_user_blocked(client, app, pending_user):
    """is_approved=False kullanıcı login etse de uyarı alır."""
    with app.app_context():
        email = User.query.filter_by(id=pending_user.id).first().email

    r = client.post('/login', data={'email': email, 'password': 'testpass123'})
    assert r.status_code == 200
    assert 'onaylanmadı' in r.data.decode('utf-8', errors='ignore')


def test_login_authenticated_redirects(auth_client):
    """Zaten login olmuş kullanıcı /login GET → /"""
    r = auth_client.get('/login', follow_redirects=False)
    assert r.status_code == 302


# ─────────────────────────────────────────────────────────────────────────────
# REGISTER
# ─────────────────────────────────────────────────────────────────────────────

def test_register_success(client, app):
    r = client.post('/register', data={
        'email': 'newuser@example.com',
        'password': 'secret1234',
        'confirm_password': 'secret1234',
        'full_name': 'Yeni Kullanıcı',
        'company': 'Test Co',
        'phone': '5550001122',
    }, follow_redirects=False)
    # Başarılıysa /login'e yönlendirir
    assert r.status_code == 302
    assert '/login' in r.headers['Location']

    with app.app_context():
        assert User.query.filter_by(email='newuser@example.com').first() is not None


def test_register_password_mismatch(client):
    r = client.post('/register', data={
        'email': 'mismatch@test.local',
        'password': 'abc1234',
        'confirm_password': 'wrong1234',
        'full_name': 'X',
    })
    assert r.status_code == 200
    assert 'eşleşmiyor' in r.data.decode('utf-8', errors='ignore')


def test_register_short_password(client):
    r = client.post('/register', data={
        'email': 'short@test.local',
        'password': 'abc',
        'confirm_password': 'abc',
        'full_name': 'X',
    })
    assert r.status_code == 200
    assert 'en az 6' in r.data.decode('utf-8', errors='ignore')


def test_register_invalid_email(client):
    r = client.post('/register', data={
        'email': 'not-an-email',
        'password': 'abcdef1',
        'confirm_password': 'abcdef1',
        'full_name': 'X',
    })
    assert r.status_code == 200
    assert 'e-posta' in r.data.decode('utf-8', errors='ignore').lower()


def test_register_duplicate_email(client, app, starter_user):
    with app.app_context():
        existing_email = User.query.filter_by(id=starter_user.id).first().email

    r = client.post('/register', data={
        'email': existing_email,
        'password': 'abcdef1',
        'confirm_password': 'abcdef1',
        'full_name': 'X',
    })
    assert r.status_code == 200
    assert 'zaten kayıtlı' in r.data.decode('utf-8', errors='ignore')


# ─────────────────────────────────────────────────────────────────────────────
# LOGOUT
# ─────────────────────────────────────────────────────────────────────────────

def test_logout_redirects_to_login(auth_client):
    r = auth_client.get('/logout', follow_redirects=False)
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_logout_requires_auth(client):
    """Anonim /logout → /login redirect."""
    r = client.get('/logout', follow_redirects=False)
    assert r.status_code == 302


# ─────────────────────────────────────────────────────────────────────────────
# RATE LIMIT (login POST 10/dk)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.rate_limit
def test_login_rate_limit_triggers(client):
    """11. ve 12. POST 429 dönmeli."""
    codes = []
    for _ in range(12):
        codes.append(client.post('/login', data={'email': 'x@x', 'password': 'x'}).status_code)
    assert 429 in codes
    assert codes.count(429) >= 2

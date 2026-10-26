"""
tests/test_legal.py — Yasal sayfalar + kayıt onay zorunluluğu (launch öncesi).
"""
import pytest


@pytest.mark.parametrize("path", [
    '/kullanim-kosullari',
    '/gizlilik-politikasi',
    '/kvkk-aydinlatma',
    '/cerez-politikasi',
])
def test_legal_pages_render_public(client, path):
    """Yasal sayfalar auth'suz erişilebilir ve 200 döner."""
    r = client.get(path)
    assert r.status_code == 200


def test_legal_pages_have_crosslinks(client):
    """Her yasal sayfa diğerlerine link vermeli (footer navigasyonu)."""
    r = client.get('/kvkk-aydinlatma')
    body = r.data.decode('utf-8')
    assert '/gizlilik-politikasi' in body
    assert '/kullanim-kosullari' in body


def test_register_requires_terms_acceptance(client):
    """accept_terms olmadan kayıt REDDEDİLMELİ (backend güvencesi)."""
    r = client.post('/register', data={
        'email': 'noterms@example.com',
        'password': 'secret1234',
        'confirm_password': 'secret1234',
        'full_name': 'Onaysız Kullanıcı',
        # accept_terms YOK
    }, follow_redirects=True)
    body = r.data.decode('utf-8')
    assert 'onayla' in body.lower() or 'koşul' in body.lower()
    # Kullanıcı oluşturulMAMALI
    from models import User
    assert User.query.filter_by(email='noterms@example.com').first() is None


def test_register_succeeds_with_terms(client):
    """accept_terms=1 ile kayıt başarılı (302 → login)."""
    r = client.post('/register', data={
        'email': 'withterms@example.com',
        'password': 'secret1234',
        'confirm_password': 'secret1234',
        'full_name': 'Onaylı Kullanıcı',
        'accept_terms': '1',
    }, follow_redirects=False)
    assert r.status_code == 302

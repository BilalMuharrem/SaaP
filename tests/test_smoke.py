"""Genel smoke testleri — uygulama açılıyor, temel rotalar yanıt veriyor."""


def test_app_creates(app):
    """Test app örneği üretiliyor + temel endpointler kayıtlı."""
    endpoints = {r.endpoint for r in app.url_map.iter_rules()}
    assert 'auth.login' in endpoints
    assert 'dashboard.dashboard' in endpoints
    assert 'tracked.tracked_products' in endpoints
    assert 'admin.admin_dashboard' in endpoints


def test_landing_renders(client):
    r = client.get('/')
    assert r.status_code == 200
    assert b'BMK' in r.data


def test_login_page_renders(client):
    r = client.get('/login')
    assert r.status_code == 200


def test_register_page_renders(client):
    r = client.get('/register')
    assert r.status_code == 200


def test_dashboard_requires_auth(client):
    """Anonim kullanıcı /dashboard → /login redirect."""
    r = client.get('/dashboard')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_dashboard_renders_for_auth_user(auth_client):
    r = auth_client.get('/dashboard')
    assert r.status_code == 200


def test_404_renders_friendly_page(client):
    r = client.get('/this-does-not-exist')
    assert r.status_code == 404
    # error.html "404" başlığı içermeli
    assert b'404' in r.data or 'bulunamadı' in r.data.decode('utf-8', errors='ignore').lower()

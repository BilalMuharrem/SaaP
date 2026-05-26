"""Faz 7D: /demo public sayfası testleri."""


def test_demo_public_no_auth_required(client):
    """/demo anonim erişime açık — auth/login redirect olmamalı."""
    r = client.get('/demo', follow_redirects=False)
    assert r.status_code == 200
    # Hiçbir 302 redirect olmamalı
    assert 'Location' not in r.headers


def test_demo_has_four_module_sections(client):
    """4 ana bölüm DOM'da: fiyat, seo, yz, bildirim."""
    r = client.get('/demo')
    body = r.data.decode('utf-8')
    assert 'id="fiyat"' in body
    assert 'id="seo"' in body
    assert 'id="yz"' in body
    assert 'id="bildirim"' in body


def test_demo_banner_visible(client):
    """En üstte DEMO MODU banner'ı."""
    r = client.get('/demo')
    body = r.data.decode('utf-8')
    assert 'DEMO MODU' in body
    assert 'demo-banner' in body


def test_demo_cta_points_to_register(client):
    """Beta'ya katıl CTA'ları /register'a gider."""
    r = client.get('/demo')
    body = r.data.decode('utf-8')
    assert '/register' in body
    assert 'Beta' in body  # Beta'ya katıl metni


def test_demo_no_real_backend_data(client):
    """Demo veriler MOCK — gerçek DB ürünleri SQLite'ta yok."""
    r = client.get('/demo')
    body = r.data.decode('utf-8')
    # Mock ürün isimleri görünmeli
    assert 'Sizin Ürününüz' in body or 'Rakip' in body


def test_landing_hero_links_to_demo(client):
    """Landing hero "Demoyu İncele" CTA → /demo."""
    r = client.get('/')
    body = r.data.decode('utf-8')
    # Hero CTA href="/demo"
    assert '/demo' in body
    # Eski #features link'i kaldırıldı (Demo CTA için)
    assert 'href="#features" class="btn btn-outline' not in body


def test_landing_no_fake_testimonials(client):
    """Faz 7D temizliği: uydurma Mert K / Ayşe T / Cem B kalmadı."""
    r = client.get('/')
    body = r.data.decode('utf-8')
    assert 'Mert K.' not in body
    assert 'Ayşe T.' not in body
    assert 'Cem B.' not in body
    # Bunun yerine dürüst mesaj
    assert 'Yorumlar ve referanslar' in body or 'Henüz erken günler' in body

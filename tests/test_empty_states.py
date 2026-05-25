"""Faz 5B: Empty state — first-time banner ve boş liste CTA'ları."""
from extensions import db
from models import TrackedProduct


def test_dashboard_shows_first_time_banner_for_empty_user(auth_client, starter_user):
    """Hiç ürün/job yokken dashboard'da 'Hoşgeldin' banner'ı var."""
    r = auth_client.get('/dashboard')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'Hoşgeldin' in body
    assert 'İlk Ürünü Ekle' in body or 'İlk ürünü' in body or 'İlk rakibini' in body


def test_dashboard_hides_banner_when_user_has_products(auth_client, starter_user):
    """Bir tracked product eklenince banner kaybolur."""
    db.session.add(TrackedProduct(
        user_id=starter_user.id,
        url='https://www.trendyol.com/x-p-1',
        is_base_product=True,
        is_price_tracked=True,
        tracking_type='price',
    ))
    db.session.commit()

    r = auth_client.get('/dashboard')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    # Banner'ın kendi CTA metni yok
    assert 'İlk rakibini eklemen 30 saniye sürer' not in body


def test_tracked_products_shows_empty_cta(auth_client):
    """Hiç ürün yokken /tracked-products → 'İlk ürününü takibe al' CTA."""
    r = auth_client.get('/tracked-products')
    assert r.status_code == 200
    body = r.data.decode('utf-8')
    assert 'İlk ürününü takibe al' in body
    assert 'Yukarıdaki Formdan Ekle' in body or 'Formdan Ekle' in body

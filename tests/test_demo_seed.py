"""Faz 7C: Demo/sample data seed davranışı."""
from extensions import db
from models import TrackedProduct, User


def test_seed_demo_creates_products(app, starter_user):
    """seed_demo_products() çağrılınca DEMO_PRODUCTS sayısı kadar tracked ekler."""
    from services.demo_data import seed_demo_products, DEMO_PRODUCTS

    n = seed_demo_products(starter_user.id)
    assert n == len(DEMO_PRODUCTS)

    rows = TrackedProduct.query.filter_by(
        user_id=starter_user.id, is_demo=True
    ).all()
    assert len(rows) == len(DEMO_PRODUCTS)
    # İlk ürün base
    base = next(r for r in rows if r.is_base_product)
    assert base.group_label == 'Örnek Ürünler'


def test_seed_demo_idempotent(starter_user):
    """Aynı kullanıcıya tekrar seed → URL'ler zaten varsa atlanır."""
    from services.demo_data import seed_demo_products

    seed_demo_products(starter_user.id)
    count1 = TrackedProduct.query.filter_by(user_id=starter_user.id).count()

    n2 = seed_demo_products(starter_user.id)
    count2 = TrackedProduct.query.filter_by(user_id=starter_user.id).count()

    # Aynı URL'ler tekrar eklenmedi
    assert count1 == count2
    assert n2 == 0


def test_register_seeds_demo_products(client):
    """Kayıt başarıyla bitince demo ürünleri DB'ye eklenmiş olmalı."""
    import uuid
    email = f'demo-test-{uuid.uuid4().hex[:6]}@test.local'
    r = client.post('/register', data={
        'email': email,
        'password': 'testpass123',
        'confirm_password': 'testpass123',
        'full_name': 'Demo Test',
        'company': '',
        'phone': '',
    }, follow_redirects=False)

    assert r.status_code == 302  # → /login

    user = User.query.filter_by(email=email).first()
    assert user is not None

    demos = TrackedProduct.query.filter_by(user_id=user.id, is_demo=True).all()
    assert len(demos) >= 1, "Kayıt sonrası en az bir demo ürün eklenmiş olmalı"


def test_dashboard_shows_demo_only_banner(auth_client, starter_user):
    """Kullanıcının sadece demo ürünü varsa dashboard'da farklı banner gösterilir."""
    from services.demo_data import seed_demo_products

    seed_demo_products(starter_user.id)

    r = auth_client.get('/dashboard')
    body = r.data.decode('utf-8')
    assert 'Sistem nasıl çalışır görebilirsin' in body or 'Örnek Ürünler' in body
    assert 'Kendi Ürününü Ekle' in body


def test_dashboard_first_time_banner_hidden_when_demos_exist(auth_client, starter_user):
    """Demo varsa first-time banner gözükmesin (has_only_demo banner'ı devreye girer)."""
    from services.demo_data import seed_demo_products

    seed_demo_products(starter_user.id)

    r = auth_client.get('/dashboard')
    body = r.data.decode('utf-8')
    # First-time banner'a özel cümle
    assert 'İlk rakibini eklemen 30 saniye sürer' not in body


def test_dashboard_no_banner_when_user_has_own_products(auth_client, starter_user):
    """Demo + kendi ürünü olan kullanıcı hiç banner görmemeli."""
    from services.demo_data import seed_demo_products
    seed_demo_products(starter_user.id)

    db.session.add(TrackedProduct(
        user_id=starter_user.id,
        url='https://www.trendyol.com/own-product-p-1',
        is_base_product=True,
        is_price_tracked=True,
        tracking_type='price',
        is_demo=False,
    ))
    db.session.commit()

    r = auth_client.get('/dashboard')
    body = r.data.decode('utf-8')
    assert 'Sistem nasıl çalışır görebilirsin' not in body
    assert 'İlk rakibini eklemen' not in body


def test_tracked_products_demo_badge_visible(auth_client, starter_user):
    """tracked-products sayfasında demo grubunda ÖRNEK badge'i görünür."""
    from services.demo_data import seed_demo_products
    seed_demo_products(starter_user.id)

    r = auth_client.get('/tracked-products')
    body = r.data.decode('utf-8')
    assert 'Örnek' in body or 'ÖRNEK' in body


# ── FAZ 10C: Broker-down dayanıklılığı ──────────────────────────────────────

def test_demo_seed_succeeds_even_when_broker_down(starter_user, monkeypatch):
    """Redis kapalı olsa bile demo ürünler eklenmeli — scan başlamaz ama kayıt korunur."""
    from services import demo_data as dd

    # Broker probe'unu zorla False döndür
    monkeypatch.setattr(dd, '_broker_alive', lambda *_, **__: False)

    n = dd.seed_demo_products(starter_user.id)
    assert n == len(dd.DEMO_PRODUCTS)
    # Ürünler DB'de
    rows = TrackedProduct.query.filter_by(
        user_id=starter_user.id, is_demo=True
    ).all()
    assert len(rows) == len(dd.DEMO_PRODUCTS)


def test_broker_alive_probe_returns_bool():
    """_broker_alive sadece True/False döner, exception fırlatmaz."""
    from services.demo_data import _broker_alive
    result = _broker_alive(timeout_seconds=1)
    assert isinstance(result, bool)

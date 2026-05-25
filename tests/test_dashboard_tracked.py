"""tests/test_dashboard_tracked.py — Dashboard, history, tracked products."""
from models import TrackedProduct, PriceAlert
from extensions import db


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

def test_dashboard_renders(auth_client):
    r = auth_client.get('/dashboard')
    assert r.status_code == 200


def test_dashboard_anon_redirects(client):
    r = client.get('/dashboard', follow_redirects=False)
    assert r.status_code == 302


# ─────────────────────────────────────────────────────────────────────────────
# HISTORY
# ─────────────────────────────────────────────────────────────────────────────

def test_history_renders_for_normal_user(auth_client):
    r = auth_client.get('/history')
    assert r.status_code == 200


def test_history_redirects_admin_to_admin_jobs(admin_client):
    r = admin_client.get('/history', follow_redirects=False)
    assert r.status_code == 302
    assert '/admin/jobs' in r.headers['Location']


# ─────────────────────────────────────────────────────────────────────────────
# TRACKED PRODUCTS
# ─────────────────────────────────────────────────────────────────────────────

def test_tracked_products_renders(auth_client):
    r = auth_client.get('/tracked-products')
    assert r.status_code == 200


def test_tracked_products_anon_redirects(client):
    r = client.get('/tracked-products', follow_redirects=False)
    assert r.status_code == 302


def test_add_tracked_product(auth_client, app, starter_user):
    """POST ile ürün ekle, DB'de oluştuğunu doğrula."""
    url = 'https://www.trendyol.com/test/urun-p-12345'
    r = auth_client.post('/tracked-products', data={'urls': url},
                         follow_redirects=False)
    assert r.status_code == 302

    with app.app_context():
        tp = TrackedProduct.query.filter_by(user_id=starter_user.id, url=url).first()
        assert tp is not None
        assert tp.is_price_tracked is True
        assert tp.is_base_product is True


def test_add_tracked_product_invalid_url_rejected(auth_client, app, starter_user):
    r = auth_client.post('/tracked-products', data={'urls': 'not-a-url'},
                         follow_redirects=False)
    assert r.status_code == 302  # geri yönlendirir

    with app.app_context():
        cnt = TrackedProduct.query.filter_by(user_id=starter_user.id).count()
        assert cnt == 0


def test_delete_tracked_product(auth_client, app, starter_user):
    # Önce ekle
    with app.app_context():
        tp = TrackedProduct(
            user_id=starter_user.id,
            url='https://www.trendyol.com/test/p-1',
            is_active=True, is_price_tracked=True, is_base_product=True,
            tracking_type='price', is_radar_tracked=False,
        )
        db.session.add(tp)
        db.session.commit()
        tp_id = tp.id

    r = auth_client.post(f'/tracked-products/{tp_id}/delete', follow_redirects=False)
    assert r.status_code == 302

    with app.app_context():
        assert TrackedProduct.query.get(tp_id) is None


def test_export_excel_returns_csv(auth_client):
    r = auth_client.get('/tracked-products/export-excel')
    assert r.status_code == 200
    assert r.mimetype == 'text/csv'
    assert b'BMK' in r.data[:200] or b'Urun' in r.data[:200] or b'\xc3\x9c' in r.data[:50]


def test_export_pdf_returns_html(auth_client):
    r = auth_client.get('/tracked-products/export-pdf')
    assert r.status_code == 200
    assert b'BMK' in r.data or b'Fiyat' in r.data


# ─────────────────────────────────────────────────────────────────────────────
# NEW REQUEST
# ─────────────────────────────────────────────────────────────────────────────

def test_new_request_renders(auth_client):
    r = auth_client.get('/new-request')
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# PRICE ALERT
# ─────────────────────────────────────────────────────────────────────────────

def test_add_price_alert(auth_client, app, starter_user):
    """Kullanıcı kendi ürününe alarm kurar."""
    with app.app_context():
        tp = TrackedProduct(
            user_id=starter_user.id,
            url='https://www.trendyol.com/alarm/p-1',
            current_price=100.0,
            is_active=True, is_price_tracked=True, is_base_product=True,
            tracking_type='price', is_radar_tracked=False,
        )
        db.session.add(tp)
        db.session.commit()
        tp_id = tp.id

    r = auth_client.post('/tracked-products/alert/add', data={
        'tracked_product_id': tp_id, 'price_below': '80', 'price_above': '120',
    }, follow_redirects=False)
    assert r.status_code == 302

    with app.app_context():
        alert = PriceAlert.query.filter_by(
            user_id=starter_user.id, tracked_product_id=tp_id, is_active=True
        ).first()
        assert alert is not None
        assert alert.price_below == 80.0
        assert alert.price_above == 120.0


def test_add_price_alert_requires_threshold(auth_client, app, starter_user):
    """En az bir eşik (below veya above) zorunlu."""
    with app.app_context():
        tp = TrackedProduct(
            user_id=starter_user.id,
            url='https://www.trendyol.com/alarm/p-2',
            is_active=True, is_price_tracked=True, is_base_product=True,
            tracking_type='price', is_radar_tracked=False,
        )
        db.session.add(tp)
        db.session.commit()
        tp_id = tp.id

    r = auth_client.post('/tracked-products/alert/add', data={
        'tracked_product_id': tp_id,
    }, follow_redirects=False)
    assert r.status_code == 302  # geri yönlendirir hata flash'ı ile


def test_add_price_alert_only_owns_products(auth_client, app, admin_user):
    """Başka kullanıcının ürününe alarm kurma engellenir."""
    with app.app_context():
        tp = TrackedProduct(
            user_id=admin_user.id,  # admin'in
            url='https://www.trendyol.com/admin/p-1',
            is_active=True, is_price_tracked=True, is_base_product=True,
            tracking_type='price', is_radar_tracked=False,
        )
        db.session.add(tp)
        db.session.commit()
        admin_tp_id = tp.id

    # starter_user olarak admin'in ürününe alarm kurmaya çalış
    r = auth_client.post('/tracked-products/alert/add', data={
        'tracked_product_id': admin_tp_id,
        'price_below': '50',
    }, follow_redirects=False)
    assert r.status_code == 302  # reddedildi flash ile geri

    with app.app_context():
        cnt = PriceAlert.query.filter_by(tracked_product_id=admin_tp_id).count()
        assert cnt == 0

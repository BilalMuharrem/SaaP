"""
tests/test_group_pause.py — Grup bazlı tarama duraklat/sürdür (is_paused).

Kullanıcı isteği: her kategori (grup) için ayrı durdur/devam butonu. Duraklatınca
worker o grubu atlar (yeni tarama yok) ama geçmiş veri/grafik korunur.
Toggle hem fiyat (TrackedProduct) hem SEO (KeywordTracker) kayıtlarını etkiler.
"""
from extensions import db
from models import TrackedProduct, KeywordTracker


def _make_group(user, gid):
    tp = TrackedProduct(user_id=user.id, url='https://www.trendyol.com/x-p-111',
                        is_price_tracked=True, group_id=gid, is_base_product=True)
    kt = KeywordTracker(user_id=user.id, platform='Trendyol', keyword='test kelime',
                        target_url='https://www.trendyol.com/x-p-111',
                        is_active=True, group_id=gid)
    db.session.add_all([tp, kt])
    db.session.commit()
    return tp.id, kt.id


def test_toggle_requires_login(client):
    r = client.post('/group/grp-x/toggle-pause', follow_redirects=False)
    assert r.status_code == 302  # login'e yönlendirir


def test_toggle_pauses_then_resumes(auth_client, starter_user, app):
    with app.app_context():
        tp_id, kt_id = _make_group(starter_user, 'grp-A')

    # 1. toggle → DURAKLAT
    auth_client.post('/group/grp-A/toggle-pause', follow_redirects=True)
    with app.app_context():
        assert db.session.get(TrackedProduct, tp_id).is_paused is True
        assert db.session.get(KeywordTracker, kt_id).is_paused is True

    # 2. toggle → SÜRDÜR
    auth_client.post('/group/grp-A/toggle-pause', follow_redirects=True)
    with app.app_context():
        assert db.session.get(TrackedProduct, tp_id).is_paused is False
        assert db.session.get(KeywordTracker, kt_id).is_paused is False


def test_paused_excluded_from_price_scan_query(app, starter_user):
    """Paused ürün, fiyat tarama SEÇİM sorgusuna girmemeli (worker atlar)."""
    with app.app_context():
        tp_id, _ = _make_group(starter_user, 'grp-B')
        db.session.get(TrackedProduct, tp_id).is_paused = True
        db.session.commit()
        picked = TrackedProduct.query.filter(
            TrackedProduct.is_active == True,
            TrackedProduct.is_price_tracked == True,
            TrackedProduct.is_paused == False,
        ).all()
        assert tp_id not in [p.id for p in picked]


def test_paused_excluded_from_seo_scan_query(app, starter_user):
    """Paused SEO takibi, tarama seçim sorgusuna girmemeli."""
    with app.app_context():
        _, kt_id = _make_group(starter_user, 'grp-C')
        db.session.get(KeywordTracker, kt_id).is_paused = True
        db.session.commit()
        picked = KeywordTracker.query.filter(
            KeywordTracker.is_active == True,
            KeywordTracker.is_paused == False,
        ).all()
        assert kt_id not in [k.id for k in picked]


def test_other_user_group_untouched(auth_client, starter_user, enterprise_user, app):
    """Aynı group_id başka kullanıcıda olsa bile etkilenmemeli (user scoping)."""
    with app.app_context():
        other = TrackedProduct(user_id=enterprise_user.id,
                               url='https://www.trendyol.com/y-p-222',
                               is_price_tracked=True, group_id='grp-A')
        db.session.add(other)
        db.session.commit()
        other_id = other.id
        _make_group(starter_user, 'grp-A')

    auth_client.post('/group/grp-A/toggle-pause', follow_redirects=True)
    with app.app_context():
        # enterprise_user'ın ürünü dokunulmamış olmalı
        assert db.session.get(TrackedProduct, other_id).is_paused is False

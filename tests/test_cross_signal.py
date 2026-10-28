"""
tests/test_cross_signal.py — Çapraz-sinyal uyarıları (özgünlük #3).

Fiyat trendi ↔ SEO sıra trendi ilişkisinden "neden" içeren bildirim üretilir.
Yalnızca sıra belirgin değiştiğinde (>=2 basamak) tetiklenir; dedup 20 saat.
"""
from datetime import timedelta

from extensions import db
from models import (TrackedProduct, KeywordTracker, PriceHistory, SEOHistory,
                    Notification, get_tr_now)
import worker


URL = 'https://www.trendyol.com/x-p-555'


def _setup(user, price_series, rank_series, gid='cs-grp'):
    """user için eşleşen ürün+tracker + fiyat/sıra geçmişi kurar."""
    tp = TrackedProduct(user_id=user.id, url=URL, is_price_tracked=True,
                        group_id=gid, product_name='Test Ürünü')
    kt = KeywordTracker(user_id=user.id, platform='Trendyol', keyword='test kelime',
                        target_url=URL, is_active=True, group_id=gid)
    db.session.add_all([tp, kt])
    db.session.flush()
    base = get_tr_now() - timedelta(days=3)
    for i, p in enumerate(price_series):
        db.session.add(PriceHistory(product_id=tp.id, price=p,
                                    timestamp=base + timedelta(hours=i * 6)))
    for i, r in enumerate(rank_series):
        db.session.add(SEOHistory(keyword_tracker_id=kt.id, page=1, rank=r,
                                  overall_rank=r, timestamp=base + timedelta(hours=i * 6)))
    db.session.commit()
    return tp.id, kt.id


def test_rank_worsened_price_stable_creates_alert(app, starter_user):
    """Fiyat sabit + sıra geriledi → uyarı bildirimi (neden içeren)."""
    with app.app_context():
        _setup(starter_user, price_series=[100.0, 100.0], rank_series=[3, 9])
        worker.cross_signal_analysis(app)
        n = Notification.query.filter_by(
            user_id=starter_user.id, category='cross_signal').first()
        assert n is not None
        assert 'GERİLEDİ' in n.message
        assert 'sabit' in n.message.lower()


def test_rank_improved_price_dropped_creates_positive(app, starter_user):
    """Fiyat düştü + sıra yükseldi → olumlu bildirim ('işe yaramış')."""
    with app.app_context():
        _setup(starter_user, price_series=[100.0, 80.0], rank_series=[9, 2])
        worker.cross_signal_analysis(app)
        n = Notification.query.filter_by(
            user_id=starter_user.id, category='cross_signal').first()
        assert n is not None
        assert 'YÜKSELDİ' in n.message


def test_stable_rank_no_alert(app, starter_user):
    """Sıra belirgin değişmediyse (<2 basamak) bildirim ÜRETİLMEZ (gürültü yok)."""
    with app.app_context():
        _setup(starter_user, price_series=[100.0, 90.0], rank_series=[5, 6])
        worker.cross_signal_analysis(app)
        assert Notification.query.filter_by(
            user_id=starter_user.id, category='cross_signal').count() == 0


def test_dedup_within_window(app, starter_user):
    """Aynı ürüne 20 saat içinde ikinci çalıştırma yeni bildirim üretmez."""
    with app.app_context():
        _setup(starter_user, price_series=[100.0, 100.0], rank_series=[3, 9])
        worker.cross_signal_analysis(app)
        worker.cross_signal_analysis(app)  # ikinci kez
        assert Notification.query.filter_by(
            user_id=starter_user.id, category='cross_signal').count() == 1


def test_no_matching_product_no_alert(app, starter_user):
    """SEO takibi var ama eşleşen fiyat-takip ürünü yoksa bildirim yok."""
    with app.app_context():
        kt = KeywordTracker(user_id=starter_user.id, platform='Trendyol',
                            keyword='k', target_url=URL, is_active=True)
        db.session.add(kt)
        db.session.flush()
        base = get_tr_now() - timedelta(days=2)
        for i, r in enumerate([3, 9]):
            db.session.add(SEOHistory(keyword_tracker_id=kt.id, page=1, rank=r,
                                      overall_rank=r, timestamp=base + timedelta(hours=i)))
        db.session.commit()
        worker.cross_signal_analysis(app)
        assert Notification.query.filter_by(
            user_id=starter_user.id, category='cross_signal').count() == 0

"""
tests/test_global_history.py — HOTFIX 10.1: Shared Global Price History.

Yeni kullanıcı, sistemde başka kullanıcılar tarafından zaten takip edilen
bir URL'i takibe aldığında, o ürünün tüm tarihsel fiyat geçmişini ANINDA
grafikte görmeli (eskiden sadece kendi katıldığı andan sonrası vardı).

Mimari hatırlatması (HOTFIX 1.91):
  • GlobalProduct — URL bazlı tekil ürün kaydı (paylaşımlı)
  • TrackedProduct — her kullanıcı için ayrı (global_product_id FK ile GP'ye bağlı)
  • PriceHistory.product_id → TrackedProduct.id (worker her TP'ye ayrı yazıyor)

Çözüm okuma katmanında: get_global_price_history(tp) → aynı GP'ye bağlı
tüm TP'lerin satırlarını birleştir, (timestamp, price) dedupe et.
"""
from datetime import timedelta

import pytest

from extensions import db
from models import (
    TrackedProduct, GlobalProduct, PriceHistory, get_tr_now,
    get_global_price_history, attach_tracked_product_to_global,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def user_a(app):
    """İlk kullanıcı — bir hafta önce takibe başlamış."""
    from tests.conftest import _make_user
    with app.app_context():
        yield _make_user('alice', plan_name='starter')


@pytest.fixture
def user_b(app):
    """İkinci kullanıcı — bugün katılıyor."""
    from tests.conftest import _make_user
    with app.app_context():
        yield _make_user('bob', plan_name='starter')


def _add_history(tp, days_ago, price):
    """tp için (now - days_ago) timestamp'inde fiyat satırı ekle."""
    h = PriceHistory(
        product_id=tp.id,
        price=price,
        timestamp=get_tr_now() - timedelta(days=days_ago),
    )
    db.session.add(h)
    return h


def _make_tracked(user, url='https://www.trendyol.com/test-urun-p-12345'):
    """user için verilen URL'i takibe ekle (GP'ye attach)."""
    tp = TrackedProduct(
        user_id=user.id,
        url=url,
        is_base_product=True,
        is_price_tracked=True,
        is_radar_tracked=False,
        tracking_type='price',
    )
    db.session.add(tp)
    db.session.flush()
    attach_tracked_product_to_global(tp)
    db.session.commit()
    return tp


# ─────────────────────────────────────────────────────────────────────────────
# Testler
# ─────────────────────────────────────────────────────────────────────────────

def test_new_user_sees_existing_history_from_other_users(user_a, user_b):
    """Kullanıcı A 7 gün takip ediyor, kullanıcı B aynı URL'i bugün ekliyor.
    B'nin grafiği A'nın 7 günlük geçmişini içermeli."""
    url = 'https://www.trendyol.com/test-shared-urun-p-1'

    # A bir hafta önce takibe başladı, 7 satır history birikmiş
    tp_a = _make_tracked(user_a, url)
    for d in range(7, 0, -1):
        _add_history(tp_a, days_ago=d, price=100.0 + d)
    db.session.commit()

    # B bugün aynı URL'i takibe alıyor
    tp_b = _make_tracked(user_b, url)
    # B'nin kendi history'si henüz yok (worker ilk taramayı yapmadı)
    assert PriceHistory.query.filter_by(product_id=tp_b.id).count() == 0

    # Ama B grafik için sorguladığında A'nın geçmişini görmeli
    history = get_global_price_history(tp_b)
    assert len(history) == 7, (
        f"B, A'nın 7 günlük geçmişini görmeli, len={len(history)}"
    )
    # Fiyatlar A'nın yazdığıyla eşleşmeli
    prices = sorted([p.price for p in history])
    assert prices == [101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]


def test_same_timestamp_same_price_is_deduped(user_a, user_b):
    """A ve B aynı saniyede aynı fiyat yazarsa, grafik tek satır görmeli."""
    url = 'https://www.trendyol.com/test-dedupe-p-2'
    tp_a = _make_tracked(user_a, url)
    tp_b = _make_tracked(user_b, url)

    ts = get_tr_now() - timedelta(hours=1)
    db.session.add(PriceHistory(product_id=tp_a.id, price=199.0, timestamp=ts))
    db.session.add(PriceHistory(product_id=tp_b.id, price=199.0, timestamp=ts))
    db.session.commit()

    history = get_global_price_history(tp_a)
    assert len(history) == 1
    assert history[0].price == 199.0


def test_different_prices_at_same_time_both_kept(user_a, user_b):
    """Aynı timestamp'te farklı fiyat → ikisi de korunur (dedupe (ts, price) çiftinde)."""
    url = 'https://www.trendyol.com/test-multi-price-p-3'
    tp_a = _make_tracked(user_a, url)
    tp_b = _make_tracked(user_b, url)

    ts = get_tr_now() - timedelta(hours=2)
    db.session.add(PriceHistory(product_id=tp_a.id, price=150.0, timestamp=ts))
    db.session.add(PriceHistory(product_id=tp_b.id, price=160.0, timestamp=ts))
    db.session.commit()

    history = get_global_price_history(tp_a)
    assert len(history) == 2
    assert {p.price for p in history} == {150.0, 160.0}


def test_history_sorted_by_timestamp_ascending(user_a, user_b):
    """Birleşik geçmiş timestamp'e göre artan sıralı dönmeli."""
    url = 'https://www.trendyol.com/test-sort-p-4'
    tp_a = _make_tracked(user_a, url)
    tp_b = _make_tracked(user_b, url)

    # Karışık sırada ekle
    _add_history(tp_a, days_ago=5, price=50.0)
    _add_history(tp_b, days_ago=2, price=60.0)
    _add_history(tp_a, days_ago=8, price=40.0)
    _add_history(tp_b, days_ago=1, price=70.0)
    db.session.commit()

    history = get_global_price_history(tp_a)
    assert len(history) == 4
    timestamps = [p.timestamp for p in history]
    assert timestamps == sorted(timestamps)
    # En eski (8 gün önce) ilk, en yeni (1 gün önce) son
    assert history[0].price == 40.0
    assert history[-1].price == 70.0


def test_legacy_tp_without_global_product_id_still_works(user_a):
    """Eski (orphan) TP — global_product_id=NULL → sadece kendi history'sini döner.
    Geriye uyumluluk testi."""
    url = 'https://www.trendyol.com/test-orphan-p-5'

    # GP attach YAPMADAN ekle (eski kayıt simülasyonu)
    tp = TrackedProduct(
        user_id=user_a.id,
        url=url,
        is_base_product=True,
        is_price_tracked=True,
    )
    db.session.add(tp)
    db.session.flush()
    # attach_tracked_product_to_global ÇAĞIRMA — global_product_id=None kalsın

    _add_history(tp, days_ago=3, price=42.0)
    _add_history(tp, days_ago=1, price=44.0)
    db.session.commit()

    assert tp.global_product_id is None
    history = get_global_price_history(tp)
    assert len(history) == 2
    assert {p.price for p in history} == {42.0, 44.0}


def test_empty_history_returns_empty_list(user_a):
    """Hiç PriceHistory yoksa boş liste döner, hata değil."""
    url = 'https://www.trendyol.com/test-empty-p-6'
    tp = _make_tracked(user_a, url)
    assert get_global_price_history(tp) == []


def test_none_input_returns_empty_list():
    """None argümanı → boş liste, exception fırlatma."""
    assert get_global_price_history(None) == []

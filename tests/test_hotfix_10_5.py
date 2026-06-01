"""
tests/test_hotfix_10_5.py — HOTFIX 10.5: SEO sıralama değişim bildirimleri.

Önceki bug: worker.check_keyword_trackers fonksiyonu Notification ve
TrackedProduct'ı import etmemişti → bildirim oluşturmaya çalışırken
NameError. Hata sessizce yutuluyordu (log.info), kullanıcı paneline
SEO bildirimleri hiç düşmüyordu.

Testler:
  • End-to-end: worker fonksiyonunu mock'lu scraper ile çağır, rank değişimi
    simüle et, Notification(category='seo') DB'de oluştu mu doğrula
  • Frontend: SEO bildirim ekledikten sonra /notifications?cat=seo render
    edildiğinde listede görünüyor mu
  • Sentinel davranış: ilk ölçüm (prev=0) bildirim üretmemeli
"""
from datetime import timedelta

import pytest

from extensions import db
from models import (
    Notification, KeywordTracker, TrackedProduct, get_tr_now,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: KeywordTracker + (opsiyonel) TrackedProduct
# ─────────────────────────────────────────────────────────────────────────────

def _make_kt(user_id, keyword='test kelimesi', prev_page=2, prev_rank=10,
             target_url='https://www.trendyol.com/x-p-555'):
    """Önceden ölçülmüş bir KeywordTracker yarat."""
    kt = KeywordTracker(
        user_id=user_id,
        platform='Trendyol',
        keyword=keyword,
        target_url=target_url,
        current_page=prev_page,
        current_rank=prev_rank,
        previous_page=prev_page,
        previous_rank=prev_rank,
        last_checked=get_tr_now() - timedelta(hours=2),
        is_active=True,
    )
    db.session.add(kt)
    db.session.commit()
    return kt


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end: worker → Notification
# ─────────────────────────────────────────────────────────────────────────────

def test_seo_rank_change_creates_notification(app, starter_user, monkeypatch):
    """Rank YÜKSELDİĞİNDE Notification(category='seo') oluşturulmalı."""
    with app.app_context():
        kt = _make_kt(starter_user.id, prev_page=2, prev_rank=10)

        # Worker'ın scraper fonksiyonunu mock'la — yeni sıra: sayfa 1, rank 3
        import worker as worker_mod
        monkeypatch.setattr(worker_mod, '_track_keyword_trendyol',
                            lambda kw, url, max_pages=5: (1, 3))

        # check_keyword_trackers'ı bu tracker için çağır
        worker_mod.check_keyword_trackers(app, tracker_ids=[kt.id])

        # Notification oluştu mu?
        notifs = Notification.query.filter_by(
            user_id=starter_user.id, category='seo'
        ).all()
        assert len(notifs) == 1, (
            f"SEO bildirimi oluşturulmadı. Toplam SEO notif: {len(notifs)}"
        )
        msg = notifs[0].message
        # "yükseldi" emoji + metin
        assert ('📈' in msg) or ('🏆' in msg), f"Yükseldi emojisi yok: {msg!r}"
        assert 'yükseldi' in msg.lower()
        assert kt.keyword in msg


def test_seo_rank_drop_creates_notification_with_down_emoji(app, starter_user, monkeypatch):
    """Rank DÜŞTÜĞÜNDE 📉 emojili Notification oluşturulmalı."""
    with app.app_context():
        kt = _make_kt(starter_user.id, prev_page=1, prev_rank=5)

        # Yeni sıra: sayfa 2, rank 20 (düştü)
        import worker as worker_mod
        monkeypatch.setattr(worker_mod, '_track_keyword_trendyol',
                            lambda kw, url, max_pages=5: (2, 20))

        worker_mod.check_keyword_trackers(app, tracker_ids=[kt.id])

        notifs = Notification.query.filter_by(
            user_id=starter_user.id, category='seo'
        ).all()
        assert len(notifs) == 1
        msg = notifs[0].message
        assert '📉' in msg
        assert 'düştü' in msg.lower()


def test_seo_first_measurement_does_not_create_notification(app, starter_user, monkeypatch):
    """İlk ölçümde (prev=0) bildirim üretilmemeli — kullanıcı zaten ekledi."""
    with app.app_context():
        # prev_page=0, prev_rank=0 → ilk ölçüm senaryosu
        kt = _make_kt(starter_user.id, prev_page=0, prev_rank=0)

        import worker as worker_mod
        monkeypatch.setattr(worker_mod, '_track_keyword_trendyol',
                            lambda kw, url, max_pages=5: (1, 7))

        worker_mod.check_keyword_trackers(app, tracker_ids=[kt.id])

        notifs = Notification.query.filter_by(
            user_id=starter_user.id, category='seo'
        ).all()
        assert len(notifs) == 0, "İlk ölçümde bildirim oluşturuldu — bu hatalı"


def test_seo_no_change_no_notification(app, starter_user, monkeypatch):
    """Sıra değişmediyse bildirim üretilmemeli."""
    with app.app_context():
        kt = _make_kt(starter_user.id, prev_page=2, prev_rank=15)

        # Aynı sıra
        import worker as worker_mod
        monkeypatch.setattr(worker_mod, '_track_keyword_trendyol',
                            lambda kw, url, max_pages=5: (2, 15))

        worker_mod.check_keyword_trackers(app, tracker_ids=[kt.id])

        notifs = Notification.query.filter_by(
            user_id=starter_user.id, category='seo'
        ).all()
        assert len(notifs) == 0


def test_seo_lost_from_top5_pages_creates_warning(app, starter_user, monkeypatch):
    """Önceden sırada olan ürün ilk 5 sayfadan kaybolursa ⚠️ bildirim."""
    with app.app_context():
        kt = _make_kt(starter_user.id, prev_page=3, prev_rank=22)

        # Yeni: bulunamadı (0, 0)
        import worker as worker_mod
        monkeypatch.setattr(worker_mod, '_track_keyword_trendyol',
                            lambda kw, url, max_pages=5: (0, 0))

        worker_mod.check_keyword_trackers(app, tracker_ids=[kt.id])

        notifs = Notification.query.filter_by(
            user_id=starter_user.id, category='seo'
        ).all()
        assert len(notifs) == 1
        msg = notifs[0].message
        assert '⚠️' in msg
        assert 'bulunamadı' in msg.lower()


def test_seo_notification_uses_product_name_when_available(app, starter_user, monkeypatch):
    """Bildirim mesajında TrackedProduct.product_name kullanılmalı (varsa)."""
    with app.app_context():
        url = 'https://www.trendyol.com/markax-p-99'
        # TP ekle
        tp = TrackedProduct(
            user_id=starter_user.id,
            url=url,
            product_name='Markax Süper Şarjlı Ürün Adı Uzun',
            is_price_tracked=True,
        )
        db.session.add(tp)
        db.session.commit()

        kt = _make_kt(starter_user.id, prev_page=1, prev_rank=8, target_url=url)

        import worker as worker_mod
        monkeypatch.setattr(worker_mod, '_track_keyword_trendyol',
                            lambda kw, url, max_pages=5: (1, 4))

        worker_mod.check_keyword_trackers(app, tracker_ids=[kt.id])

        notifs = Notification.query.filter_by(
            user_id=starter_user.id, category='seo'
        ).all()
        assert len(notifs) == 1
        # İlk 5 kelime: "Markax Süper Şarjlı Ürün Adı"
        assert 'Markax' in notifs[0].message


# ─────────────────────────────────────────────────────────────────────────────
# Frontend: /notifications?cat=seo
# ─────────────────────────────────────────────────────────────────────────────

def test_notifications_page_seo_tab_shows_seo_notifications(auth_client, starter_user):
    """SEO kategorili bildirim manuel eklenip /notifications?cat=seo açılınca
    listede görünmeli."""
    db.session.add(Notification(
        user_id=starter_user.id,
        message='📈 SEO Sıralaması Değişti: Test Ürünü, "test kelimesi" '
                'aramasında Sayfa 1, Sıra 3 oldu (yükseldi)!',
        category='seo',
        link='https://www.trendyol.com/x-p-555',
        internal_link='/seo-graph',
    ))
    db.session.commit()

    r = auth_client.get('/notifications?cat=seo')
    assert r.status_code == 200
    body = r.data.decode('utf-8')

    # Bildirim mesajı render edildi mi?
    assert 'SEO Sıralaması Değişti' in body
    assert 'test kelimesi' in body


def test_notifications_seo_count_in_sidebar_badge(auth_client, starter_user):
    """SEO kategorisinde okunmamış bildirim sayısı sekme badge'inde görünmeli."""
    # 3 SEO bildirimi ekle, hepsi okunmamış
    for i in range(3):
        db.session.add(Notification(
            user_id=starter_user.id,
            message=f'📉 SEO test bildirim {i}',
            category='seo',
            is_read=False,
        ))
    db.session.commit()

    r = auth_client.get('/notifications')
    body = r.data.decode('utf-8')
    # SEO sekmesi badge'inde "3" görünmeli (data-count="3")
    assert 'data-count="3"' in body


def test_notifications_all_tab_includes_seo(auth_client, starter_user):
    """'all' filtresinde SEO bildirimleri de görünmeli."""
    db.session.add(Notification(
        user_id=starter_user.id,
        message='📈 SEO Sıralaması Test',
        category='seo',
    ))
    db.session.commit()

    r = auth_client.get('/notifications?cat=all')
    assert r.status_code == 200
    assert b'SEO S\xc4\xb1ralamas\xc4\xb1 Test' in r.data

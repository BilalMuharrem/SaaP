"""
tests/test_hotfix_10_3.py — HOTFIX 10.3 regresyon korumaları.

3 ayrı UX/UI bug için test:
  1. SEO grup adı UUID yerine kullanışlı fallback (keyword/product_name)
  2. Rename endpoint TP olmayan (sadece SEO) gruplarda da çalışır
  3. Tüm sidebar'lar Bildirimler sekmesini içerir
"""
import re
import uuid

import pytest

from extensions import db
from models import (
    TrackedProduct, KeywordTracker, attach_keyword_tracker_to_pool, get_tr_now,
)


# ─────────────────────────────────────────────────────────────────────────────
# Bug 2: UUID fallback artık yok
# ─────────────────────────────────────────────────────────────────────────────

def test_seo_tracker_label_uses_keyword_when_no_tracked_product(auth_client, starter_user):
    """Sadece SEO takibi olan grupta (TP yok), grup adı KT.keyword olmalı,
    'Grup <uuid>' başlığı DEĞİL.

    NOT: UUID HTML'de attribute (data-group-id, vb.) olarak görünebilir —
    bu normal, rename modal trigger için gerekli. Asıl yasak olan:
    görsel başlık olarak 'Grup <uuid prefix>...' metni.
    """
    gid = str(uuid.uuid4())
    kt = KeywordTracker(
        user_id=starter_user.id,
        platform='Trendyol',
        keyword='şarjlı tüy toplayıcı',
        target_url='https://www.trendyol.com/x-p-1',
        group_id=gid,
        is_active=True,
    )
    db.session.add(kt)
    db.session.commit()

    r = auth_client.get('/seo-tracker')
    body = r.data.decode('utf-8')

    # "Grup <uuid prefix>" başlığı görünmemeli (eski fallback pattern'i)
    assert f'Grup {gid[:10]}' not in body, (
        f"Eski UUID fallback pattern hâlâ render ediliyor: 'Grup {gid[:10]}'"
    )
    assert f'Grup {gid[:14]}' not in body
    # Keyword fallback olarak görünmeli
    assert 'şarjlı tüy toplayıcı' in body


def test_seo_graph_label_uses_keyword_when_no_tracked_product(auth_client, starter_user):
    """SEO grafik sayfasında da aynı fallback geçerli."""
    gid = str(uuid.uuid4())
    kt = KeywordTracker(
        user_id=starter_user.id,
        platform='Trendyol',
        keyword='premium kedi maması',
        target_url='https://www.trendyol.com/y-p-2',
        group_id=gid,
        is_active=True,
    )
    db.session.add(kt)
    db.session.commit()

    r = auth_client.get('/seo-graph')
    body = r.data.decode('utf-8')

    # Eski UUID fallback pattern'i görünmemeli
    assert f'Grup {gid[:10]}' not in body
    assert f'Grup {gid[:14]}' not in body
    # Keyword fallback olarak görünmeli (başlık alanında)
    assert 'premium kedi maması' in body


def test_seo_label_prefers_user_label_over_keyword(auth_client, starter_user):
    """Kullanıcının verdiği group_label, keyword fallback'ten ÖNCEDIR."""
    gid = str(uuid.uuid4())
    # Önce bir TP yarat, group_label ile
    tp = TrackedProduct(
        user_id=starter_user.id,
        url='https://www.trendyol.com/z-p-3',
        group_id=gid,
        is_base_product=True,
        is_price_tracked=True,
        group_label='Yaz Kampanyası 2026',
    )
    db.session.add(tp)
    # SEO takip de aynı group_id ile
    kt = KeywordTracker(
        user_id=starter_user.id,
        platform='Trendyol',
        keyword='farklı bir keyword',
        target_url='https://www.trendyol.com/z-p-3',
        group_id=gid,
        is_active=True,
    )
    db.session.add(kt)
    db.session.commit()

    r = auth_client.get('/seo-tracker')
    body = r.data.decode('utf-8')
    assert 'Yaz Kampanyası 2026' in body


# ─────────────────────────────────────────────────────────────────────────────
# Bug 1: Rename endpoint TP-yok senaryosunda da çalışır
# ─────────────────────────────────────────────────────────────────────────────

def test_rename_seo_only_group_does_not_404(auth_client, starter_user):
    """Sadece KT olan grupta /rename POST → 'Grup bulunamadı' DEĞİL.
    Endpoint başarılı redirect dönmeli."""
    gid = str(uuid.uuid4())
    kt = KeywordTracker(
        user_id=starter_user.id,
        platform='Trendyol',
        keyword='eski isim',
        target_url='https://www.trendyol.com/seo-only-p-4',
        group_id=gid,
        is_active=True,
    )
    db.session.add(kt)
    db.session.commit()

    # Rename POST
    r = auth_client.post(
        f'/tracked-products/group/{gid}/rename',
        data={'group_name': 'Yeni Stratejik İsim'},
        follow_redirects=False,
    )
    # 302 redirect (success). 404 veya "Grup bulunamadı" değil.
    assert r.status_code == 302

    # KT.keyword senkronize oldu mu?
    db.session.refresh(kt)
    assert kt.keyword == 'Yeni Stratejik İsim', (
        f"KT.keyword senkronize edilmedi: {kt.keyword!r}"
    )


def test_rename_unknown_group_still_404s(auth_client):
    """Gerçekten var olmayan group_id → 'Grup bulunamadı' flash + redirect."""
    fake_gid = str(uuid.uuid4())
    r = auth_client.post(
        f'/tracked-products/group/{fake_gid}/rename',
        data={'group_name': 'X'},
        follow_redirects=True,
    )
    body = r.data.decode('utf-8')
    assert 'bulunamadı' in body.lower() or 'bulunamadi' in body.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Bug 3: Tüm sidebar'lar Bildirimler içerir
# ─────────────────────────────────────────────────────────────────────────────

# Her authenticated sayfanın sidebar'ında Bildirimler menü öğesi olmalı.
# Eğer kaybolursa kullanıcı bir sayfaya geçtiğinde navigation kaybediyor.

SIDEBAR_PAGES = [
    '/dashboard',
    '/tracked-products',
    '/seo-tracker',
    '/seo-graph',
    '/ai-consultant',
    '/history',
    '/notifications',
]


@pytest.mark.parametrize('path', SIDEBAR_PAGES)
def test_sidebar_contains_notifications_link(auth_client, path):
    """Sidebar her sayfada Bildirimler linkini içermeli (HOTFIX 10.3)."""
    r = auth_client.get(path)
    assert r.status_code == 200, f"GET {path} → {r.status_code}"
    body = r.data.decode('utf-8')
    # nav-link içinde "Bildirimler" metni geçmeli
    assert 'Bildirimler' in body, (
        f"'{path}' sayfasında sidebar'da 'Bildirimler' linki yok"
    )
    # URL referansı da olmalı (HTML escape edilmiş hali kontrol edilir)
    assert '/notifications' in body, f"'{path}' sayfasında /notifications href yok"

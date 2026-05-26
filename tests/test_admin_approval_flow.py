"""Faz 6: Manuel onay akışı — pending sayacı + mesajlar."""
import uuid

from extensions import db
from models import User, Plan, get_tr_now


def _make_pending_user():
    plan = Plan.query.filter_by(name='trial').first()
    u = User(
        email=f'pending-{uuid.uuid4().hex[:6]}@test.local',
        full_name='Pending Test',
        is_admin=False, is_active=False, is_approved=False,
        plan_id=plan.id if plan else None,
        trial_start=get_tr_now(), trial_days=14,
    )
    u.set_password('testpass123')
    db.session.add(u)
    db.session.commit()
    return u


def test_api_pending_count_requires_admin(auth_client):
    """Non-admin kullanıcı /admin/api/pending-count → 403."""
    r = auth_client.get('/admin/api/pending-count')
    assert r.status_code == 403


def test_api_pending_count_returns_zero_when_empty(admin_client):
    r = admin_client.get('/admin/api/pending-count')
    assert r.status_code == 200
    data = r.get_json()
    assert data == {'pending_count': 0}


def test_api_pending_count_counts_pending(admin_client):
    _make_pending_user()
    _make_pending_user()
    r = admin_client.get('/admin/api/pending-count')
    assert r.get_json() == {'pending_count': 2}


def test_pending_count_in_admin_context_processor(admin_client):
    """Admin herhangi bir sayfaya girdiğinde sidebar badge görünür."""
    _make_pending_user()
    r = admin_client.get('/admin')
    body = r.data.decode('utf-8')
    # Badge DOM elementi: <span ... id="sidebar-pending-badge">1</span>
    assert 'id="sidebar-pending-badge"' in body
    assert '>1</span>' in body


def test_pending_count_zero_no_badge(admin_client):
    """Pending yokken badge DOM'da render edilmez (JS string referansları sayılmaz)."""
    r = admin_client.get('/admin')
    body = r.data.decode('utf-8')
    # JS kodu 'sidebar-pending-badge' string'ini içeriyor — onu değil,
    # gerçek HTML span elementini ara.
    assert '<span' not in body.split('id="sidebar-pending-badge"')[0][-100:] \
        if 'id="sidebar-pending-badge"' in body else True
    # Daha güvenilir: id atamasıyla birlikte gelen span başlangıcını ara
    import re
    has_badge_element = re.search(r'<span[^>]*id="sidebar-pending-badge"', body)
    assert has_badge_element is None


def test_pending_count_not_in_regular_user_context(auth_client):
    """Admin olmayan kullanıcının context'inde pending_count yoktur (her zaman 0)."""
    _make_pending_user()
    r = auth_client.get('/dashboard')
    # auth_client.dashboard'da admin sidebar yok ki badge görmesin
    assert b'sidebar-pending-badge' not in r.data


def test_registration_message_mentions_24_hours():
    """Faz 6C: Kayıt başarı mesajında 24 saat sözü kaynak kodda olmalı.
    Bu test rate-limit kümülatif sorununa düşmesin diye flow yerine
    kaynaktaki mesaj string'ini doğrular.
    """
    import inspect
    from blueprints import auth as auth_module
    src = inspect.getsource(auth_module)
    assert '24 saat' in src, "register success flash'ında '24 saat' sözü olmalı"
    assert 'Kaydınız alındı' in src or 'inceleyip' in src

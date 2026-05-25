"""tests/test_admin.py — Admin paneli yetkilendirme + render."""
import pytest

ADMIN_ROUTES = [
    '/admin',
    '/admin/customers',
    '/admin/jobs',
    '/admin/tracking',
    '/admin/plans',
    '/admin/settings',
]


@pytest.mark.parametrize('url', ADMIN_ROUTES)
def test_admin_routes_render_for_admin(admin_client, url):
    r = admin_client.get(url)
    assert r.status_code == 200, f'Beklenen 200, alınan {r.status_code} for {url}'


@pytest.mark.parametrize('url', ADMIN_ROUTES)
def test_admin_routes_forbid_normal_user(auth_client, url):
    r = auth_client.get(url)
    assert r.status_code == 403, f'Beklenen 403, alınan {r.status_code} for {url}'


@pytest.mark.parametrize('url', ADMIN_ROUTES)
def test_admin_routes_anon_redirects_to_login(client, url):
    r = client.get(url, follow_redirects=False)
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


# ─────────────────────────────────────────────────────────────────────────────
# Admin actions
# ─────────────────────────────────────────────────────────────────────────────

def test_admin_approves_pending_user(admin_client, app, pending_user):
    from models import User
    r = admin_client.post(f'/admin/customers/{pending_user.id}/approve',
                          follow_redirects=False)
    assert r.status_code == 302

    with app.app_context():
        u = User.query.get(pending_user.id)
        assert u.is_approved is True
        assert u.is_active is True


def test_admin_toggles_user_active(admin_client, app, starter_user):
    from models import User
    with app.app_context():
        was_active = User.query.get(starter_user.id).is_active

    r = admin_client.post(f'/admin/customers/{starter_user.id}/toggle',
                          follow_redirects=False)
    assert r.status_code == 302

    with app.app_context():
        new = User.query.get(starter_user.id).is_active
        assert new != was_active


def test_admin_changes_plan(admin_client, app, starter_user):
    from models import User, Plan
    with app.app_context():
        enterprise = Plan.query.filter_by(name='enterprise').first()
        ent_id = enterprise.id

    r = admin_client.post(f'/admin/customers/{starter_user.id}/plan',
                          data={'plan_id': ent_id}, follow_redirects=False)
    assert r.status_code == 302

    with app.app_context():
        u = User.query.get(starter_user.id)
        assert u.plan_id == ent_id


def test_admin_edit_plan(admin_client, app):
    from models import Plan
    with app.app_context():
        starter = Plan.query.filter_by(name='starter').first()
        plan_id = starter.id

    r = admin_client.post(f'/admin/plans/{plan_id}/edit', data={
        'display_name': 'Yeni Başlangıç',
        'max_requests': 20,
        'max_tracked_products': 60,
        'period_type': 'monthly',
        'price_monthly': 599,
    }, follow_redirects=False)
    assert r.status_code == 302

    with app.app_context():
        p = Plan.query.get(plan_id)
        assert p.display_name == 'Yeni Başlangıç'
        assert p.max_requests == 20
        assert p.price_monthly == 599


def test_admin_settings_update(admin_client, app):
    from models import Setting
    r = admin_client.post('/admin/settings', data={
        'approval_mode': 'auto',
        'groq_api_key': 'gsk_test_dummy_key_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
        'free_trial_days': '7',
    }, follow_redirects=False)
    assert r.status_code == 302

    with app.app_context():
        assert Setting.get('approval_mode') == 'auto'
        assert Setting.get('free_trial_days') == '7'

"""
tests/conftest.py — pytest fixture'ları.

Her test izole bir SQLite in-memory DB + fresh app context kullanır.
"""
import os
import sys
import uuid

# Proje kök dizinini path'e ekle (modüller import edilebilsin)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# pytest TEST ortamı işareti — bazı kodlar opsiyonel davranışı buradan okur.
os.environ.setdefault('TESTING', '1')
# Config'in fail-loud SECRET_KEY kontrolü için env'de bir değer olsun:
os.environ.setdefault('SECRET_KEY', 'pytest-only-secret-key-xxxxxxxxxxxxxxxxxxxxxxxx')

import pytest

from app import create_app
from config import TestConfig
from extensions import db as _db
from models import User, Plan, get_tr_now


# ─────────────────────────────────────────────────────────────────────────────
# APP + DB
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope='session')
def app():
    """Test config ile tek bir Flask app örneği (tüm session boyunca)."""
    flask_app = create_app(TestConfig)
    with flask_app.app_context():
        _db.create_all()
        _seed_plans()
    return flask_app


@pytest.fixture(autouse=True)
def _db_cleanup(app):
    """Her test'ten önce kullanıcı/tracking tablolarını temizle (Plan kalır).
    Rate limiter sayaçlarını da sıfırla (test'ler arası izolasyon).
    """
    with app.app_context():
        from sqlalchemy import text
        for table in (
            'notifications', 'price_alerts', 'price_history', 'stock_history',
            'vulnerability_alerts', 'seo_history', 'keyword_trackers',
            'tracked_products', 'ai_reports', 'usage_logs', 'jobs',
            'users',
        ):
            try:
                _db.session.execute(text(f'DELETE FROM {table}'))
            except Exception:
                pass
        _db.session.commit()

        # Rate limiter sayaçlarını sıfırla — testler arası izolasyon
        try:
            from extensions import limiter
            limiter.reset()
        except Exception:
            pass
    yield


def _seed_plans():
    """Test başlangıcında 4 plan'ı seed et (trial, starter, professional, enterprise)."""
    if Plan.query.count() > 0:
        return
    plans = [
        Plan(name='trial', display_name='Ücretsiz Deneme',
             max_requests=10, max_tracked_products=10,
             period_type='monthly', price_monthly=0, sort_order=0, is_active=True),
        Plan(name='starter', display_name='Başlangıç',
             max_requests=15, max_tracked_products=50,
             period_type='monthly', price_monthly=499, sort_order=1, is_active=True),
        Plan(name='professional', display_name='Profesyonel',
             max_requests=50, max_tracked_products=250,
             period_type='monthly', price_monthly=1499, sort_order=2, is_active=True),
        Plan(name='enterprise', display_name='Kurumsal',
             max_requests=0, max_tracked_products=0,  # 0=sınırsız
             period_type='monthly', price_monthly=4999, sort_order=3, is_active=True),
    ]
    for p in plans:
        _db.session.add(p)
    _db.session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# KULLANICILAR
# ─────────────────────────────────────────────────────────────────────────────

def _make_user(email_prefix, password='testpass123', is_admin=False, plan_name='starter',
               is_active=True, is_approved=True, full_name=None):
    """Helper — test user oluştur, DB'ye kaydet, döndür."""
    plan = Plan.query.filter_by(name=plan_name).first()
    user = User(
        email=f'{email_prefix}-{uuid.uuid4().hex[:6]}@test.local',
        full_name=full_name or email_prefix.title(),
        company='Test Co',
        is_admin=is_admin,
        is_active=is_active,
        is_approved=is_approved,
        plan_id=plan.id if plan else None,
        trial_start=get_tr_now(),
        trial_days=14,
    )
    user.set_password(password)
    _db.session.add(user)
    _db.session.commit()
    return user


@pytest.fixture
def admin_user(app):
    with app.app_context():
        yield _make_user('admin', is_admin=True, plan_name='enterprise')


@pytest.fixture
def starter_user(app):
    with app.app_context():
        yield _make_user('starter', plan_name='starter')


@pytest.fixture
def enterprise_user(app):
    """Kurumsal plan — AI Consultant erişimi için."""
    with app.app_context():
        yield _make_user('enterprise', plan_name='enterprise')


@pytest.fixture
def pending_user(app):
    """is_approved=False — login etse de mesaj alır."""
    with app.app_context():
        yield _make_user('pending', is_approved=False, is_active=False)


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(app):
    """Anonymous test client."""
    return app.test_client()


def _login_session(client, user):
    """Flask-Login session manipülasyonu — gerçek /login POST yapmadan oturum aç."""
    with client.session_transaction() as s:
        s['_user_id'] = str(user.id)
        s['_fresh'] = True


@pytest.fixture
def auth_client(client, starter_user):
    """Starter plan ile login olmuş client."""
    _login_session(client, starter_user)
    return client


@pytest.fixture
def admin_client(client, admin_user):
    """Admin ile login olmuş client."""
    _login_session(client, admin_user)
    return client


@pytest.fixture
def enterprise_client(client, enterprise_user):
    """Enterprise ile login olmuş client (AI Consultant erişimi)."""
    _login_session(client, enterprise_user)
    return client

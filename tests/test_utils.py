"""tests/test_utils.py — Pure fonksiyon birim testleri.

filters (turkdate, timeago), notification classifier,
services/scraping/blocked, services/ai/groq.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from utils.filters import turkdate, timeago


# ─────────────────────────────────────────────────────────────────────────────
# FILTERS
# ─────────────────────────────────────────────────────────────────────────────

def test_turkdate_basic():
    dt = datetime(2026, 5, 25, 14, 30)
    assert turkdate(dt) == '25 May 2026 14:30'


def test_turkdate_none():
    assert turkdate(None) == '-'


def test_turkdate_each_month():
    months = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz',
              'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara']
    for i, ay in enumerate(months, start=1):
        dt = datetime(2026, i, 15, 10, 0)
        assert ay in turkdate(dt)


def test_timeago_none():
    assert timeago(None) == '-'


def test_timeago_just_now():
    """Aralarında 10 sn olan zaman → 'Az önce'."""
    from models import get_tr_now
    dt = get_tr_now() - timedelta(seconds=10)
    assert timeago(dt) == 'Az önce'


def test_timeago_minutes():
    from models import get_tr_now
    dt = get_tr_now() - timedelta(minutes=5)
    assert 'dk önce' in timeago(dt)


def test_timeago_hours():
    from models import get_tr_now
    dt = get_tr_now() - timedelta(hours=3)
    assert 'saat önce' in timeago(dt)


def test_timeago_yesterday():
    from models import get_tr_now
    dt = get_tr_now() - timedelta(days=1)
    assert timeago(dt) == 'Dün'


def test_timeago_days():
    from models import get_tr_now
    dt = get_tr_now() - timedelta(days=3)
    assert '3 gün önce' == timeago(dt)


def test_timeago_long_ago_returns_full_date():
    """7+ gün önce → tam tarih formatı (turkdate)."""
    from models import get_tr_now
    dt = get_tr_now() - timedelta(days=30)
    out = timeago(dt)
    assert 'gün önce' not in out
    assert ':' in out  # 'XX:YY' saat formatı


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────

def test_classifier_rule_price_down():
    from services.notifications.classifier import classify_notification_rule
    assert classify_notification_rule('📉 Ürün fiyatı düştü: 100₺ → 80₺') == 'price_down'


def test_classifier_rule_price_up():
    from services.notifications.classifier import classify_notification_rule
    assert classify_notification_rule('📈 Ürün fiyatı arttı: 100₺ → 120₺') == 'price_up'


def test_classifier_rule_opportunity():
    from services.notifications.classifier import classify_notification_rule
    assert classify_notification_rule('🚨 Fırsat! Stok azalıyor') == 'opportunity'
    assert classify_notification_rule('KRİTİK FIRSAT: Rakip stoğu bitti') == 'opportunity'


def test_classifier_rule_threat():
    from services.notifications.classifier import classify_notification_rule
    assert classify_notification_rule('⚠️ Rakibe TEHDİT geldi') == 'threat'
    assert classify_notification_rule('⚡ KRİTİK fiyat hareketi') == 'threat'


def test_classifier_rule_seo():
    from services.notifications.classifier import classify_notification_rule
    assert classify_notification_rule('SEO Sıralaması değişti') == 'seo'


def test_classifier_rule_combined():
    from services.notifications.classifier import classify_notification_rule
    msg = "'Kombine Analiz' işleminiz başarıyla tamamlandı"
    assert classify_notification_rule(msg) == 'combined'


def test_classifier_rule_system_fallback():
    from services.notifications.classifier import classify_notification_rule
    assert classify_notification_rule('Alarm kuruldu: hedef 80₺') == 'system'


def test_classifier_rule_none_for_unknown():
    """Hiçbir kural eşleşmezse None → AI fallback."""
    from services.notifications.classifier import classify_notification_rule
    out = classify_notification_rule('Anlamsız rastgele metin xyz')
    assert out is None


def test_classifier_rule_empty_returns_system():
    from services.notifications.classifier import classify_notification_rule
    assert classify_notification_rule('') == 'system'
    assert classify_notification_rule(None) == 'system'


def test_classify_notification_with_ai_fallback_no_key():
    """resolve_groq_key boş → AI fallback 'system' döner (crash etmez)."""
    from services.notifications import classifier
    with patch.object(classifier, 'resolve_groq_key', return_value=''):
        out = classifier.classify_notification('Anlamsız metin')
        assert out == 'system'


# ─────────────────────────────────────────────────────────────────────────────
# BLOCKED DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

def test_is_blocked_status_codes():
    from services.scraping.blocked import is_blocked_response

    class FakeResp:
        def __init__(self, code, text=''):
            self.status_code = code
            self.text = text

    assert is_blocked_response(FakeResp(403)) is True
    assert is_blocked_response(FakeResp(429)) is True
    assert is_blocked_response(FakeResp(503)) is True
    assert is_blocked_response(FakeResp(200, 'normal content')) is False
    assert is_blocked_response(None) is True


def test_is_blocked_captcha_phrase():
    from services.scraping.blocked import is_blocked_response

    class FakeResp:
        def __init__(self, code, text):
            self.status_code = code
            self.text = text

    assert is_blocked_response(FakeResp(200, 'Please solve the CAPTCHA')) is True
    assert is_blocked_response(FakeResp(200, 'Access denied for bot')) is True
    assert is_blocked_response(FakeResp(200, 'cf-challenge active')) is True


# ─────────────────────────────────────────────────────────────────────────────
# GROQ KEY RESOLVER
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_groq_key_prefers_user_key(app):
    from services.ai.groq import resolve_groq_key
    with app.app_context():
        user_key = 'gsk_user_provided_key_xxxxxxxxxxxxxxxx'
        assert resolve_groq_key(user_key) == user_key


def test_resolve_groq_key_rejects_short(app):
    from services.ai.groq import resolve_groq_key
    with app.app_context():
        assert resolve_groq_key('short') != 'short'  # min 15 char


def test_resolve_groq_key_falls_back_to_env(app, monkeypatch):
    """Config.GROQ_API_KEY ve Setting boşsa env'e düşer.

    NOT: Config import zamanı yüklendiği için, gerçek fallback zincirini
    test etmek için Config.GROQ_API_KEY'i geçici olarak temizliyoruz.
    """
    from services.ai import groq as groq_mod
    from config import Config
    monkeypatch.setattr(Config, 'GROQ_API_KEY', '', raising=False)
    monkeypatch.setenv('GROQ_API_KEY', 'gsk_env_test_value_xxxxxxxxxxxxxxxx')
    with app.app_context():
        # Setting tablosunda da yok — env'e düşmeli
        from models import Setting
        from extensions import db as _db
        Setting.query.filter_by(key='groq_api_key').delete()
        _db.session.commit()
        assert groq_mod.resolve_groq_key() == 'gsk_env_test_value_xxxxxxxxxxxxxxxx'


# ─────────────────────────────────────────────────────────────────────────────
# BROWSER HEADERS
# ─────────────────────────────────────────────────────────────────────────────

def test_browser_headers_contain_required_keys():
    from services.scraping.browser import build_browser_headers
    h, _ = build_browser_headers()
    for key in ('User-Agent', 'Accept', 'Accept-Language', 'Accept-Encoding',
                'Sec-Fetch-Dest', 'Referer'):
        assert key in h


def test_rand_profile_returns_valid_profile():
    from services.scraping.browser import rand_profile, UA_PROFILES
    p = rand_profile()
    assert p in UA_PROFILES
    assert p['ua']

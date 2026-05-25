"""
extensions.py — Tekil Flask uzantısı örnekleri.

Bu modül circular import'ları engellemek için merkezi bir uzantı kaynağıdır.
Her uzantı burada PARAMETRE'siz oluşturulur; Flask app'e bağlama (init_app)
işlemi `app.py:create_app()` içinde yapılır.

İçerik:
    db              — SQLAlchemy ORM örneği
    login_manager   — Flask-Login örneği (login_view ve mesajlar burada ayarlanır)
    celery          — Celery örneği + beat schedule (worker.py bunu import eder)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import timedelta
from celery import Celery
from celery.schedules import crontab  # kept for backward-compat / ad-hoc usage
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import current_user as _current_user
from config import Config


# ── SQLAlchemy ─────────────────────────────────────────────────────────────
# Tüm modeller `from extensions import db` kullanır.
db = SQLAlchemy()


# ── Flask-Login ────────────────────────────────────────────────────────────
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Bu sayfaya erişmek için giriş yapmanız gerekiyor.'
login_manager.login_message_category = 'warning'


# ── Flask-Limiter (Rate Limiting) ──────────────────────────────────────────
# Anahtar fonksiyonu: giriş yapmış kullanıcı varsa user_id, yoksa IP.
# Bu sayede aynı IP arkasındaki birden fazla kullanıcı birbirini etkilemez.
def _rate_limit_key():
    try:
        if _current_user.is_authenticated:
            return f"user:{_current_user.id}"
    except Exception:
        pass
    return get_remote_address()


# Storage: Redis varsa onu kullan (paylaşımlı), yoksa in-memory (tek worker).
_storage_uri = Config.broker_url if Config.broker_url.startswith('redis') else 'memory://'

limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=_storage_uri,
    default_limits=[],   # Endpoint başına explicit verilir
    headers_enabled=True,  # X-RateLimit-* header'ları döner
)


# ── Celery ─────────────────────────────────────────────────────────────────
def make_celery(app_name=__name__):
    return Celery(
        app_name,
        broker=Config.broker_url,
        backend=Config.result_backend,
        include=['worker']
    )

celery = make_celery()
# Load all lowercase settings from Config
celery_conf = {k: v for k, v in Config.__dict__.items() if not k.startswith('__') and k.islower()}
celery.conf.update(celery_conf)

# ── Celery Beat: Periyodik görev tanımları ──
celery.conf.timezone = 'Europe/Istanbul'
#
# ── Beat Schedule: INTERVAL (kronometre) mantığı ──
# Not: crontab(minute=0) kullanmıyoruz. Nedeni:
#   macOS App Nap / sleep, sistem saat başında kısa süreliğine uyuttuğunda
#   Celery crontab penceresini kaçırıyor ve telafi etmiyor. Interval bazlı
#   schedule ise "son çalıştırmadan bu yana 1 saat geçti mi?" diye sorar;
#   Mac uyanır uyanmaz missed task'i ANINDA ateşler.
celery.conf.beat_schedule = {
    # ── HOTFIX 1.29 / 1.41 / 1.42 / 1.72: Kademeli Standart Tarama (4 saat) ──
    # `check_tracked_products_task` worker'da iken `is_price_tracked == True`
    # tüm ürünleri toplu olarak tarar; çağrı başına yeni Playwright/HTTP
    # istekleri açar. macOS App Nap senaryosunda crontab(minute=0) penceresini
    # kaçırabildiği için kronometre tabanlı interval daha güvenli.
    #
    # HOTFIX 1.72: Periyot 60dk → 240dk (4 saat) yükseltildi.
    # Sebepler:
    #   • Sunucu maliyeti (CPU + bant genişliği): %75 azalma
    #   • DataDome / Cloudflare IP ban riski: aynı IP'den saatlik 100+ istek
    #     bot algılaması tetikliyor; 4 saatte 1 daha "insansı" davranış
    #   • E-ticarette fiyatlar genelde günde 2-3 kez değişir → 4 saat yeterli
    # Günde 6 tarama (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 — beat'in kendi
    # başlangıç zamanına göre kayar; crontab kullanmadığımız için sabit saat yok).
    #
    # HOTFIX 1.42 üçlü kalkan korunuyor:
    #   1) timedelta(hours=4) — sabit kronometre, 240dk geçmişse fire eder
    #   2) @worker_ready signal (worker.py) — açılışta DERHAL bir tur tetikler
    #   3) check_tracked_products içindeki "Acil Tarama" mantığı — 240dk+
    #      gecikmiş ürünleri kuyruğun başına alır
    # Bu üçü birlikte: sistem kapalıyken birikmiş gecikmeleri bile telafi eder.
    # ── HOTFIX 1.91 / EPIC 5.0: Stratejik Crontab (Europe/Istanbul) ──
    # Önceki: timedelta(hours=4) interval → ataletli, gece her saatte tetikleyebiliyordu.
    # Yeni: crontab(minute=15, hour='3,9,15,21')
    #   • 03:15 → Gece yarısı zamlarını yakala (e-ticaret pazaryerleri saat 00-03'te
    #              yarın için fiyat güncellemesi yapar; 03:15 stabilize sonrası ölçer)
    #   • 09:15 → Sabah trafik patlamasına 1 saat kala (mesai başlangıcı)
    #   • 15:15 → Öğleden sonra alışveriş zirvesi
    #   • 21:15 → Akşam mobil trafik zirvesi
    # 4 sefer/gün = saatlik tarama yerine STRATEJİK tarama → bot ban riski minimum.
    # `minute=15` rastgele başlangıç değil — kapsamlı zam dalgaları saatin tam başında
    # yapıldıktan 15 dk sonra ölçüm en güvenli (DB tutarlılık penceresi).
    'check-tracked-products-strategic': {
        'task': 'worker.check_tracked_products_task',
        'schedule': crontab(minute=15, hour='3,9,15,21'),  # ✅ Stratejik 4 nokta
        'options': {
            'expires': 1800,
            'time_limit': 1200,
            'soft_time_limit': 1100,
        },
    },

    # ── EPIC 8.0 / HOTFIX 1.96: Otomatik SEO Sıralama Taraması ──
    # `check_keyword_trackers_task` worker'da `is_dormant=False` ve son tarama
    # eski olan tüm KeywordPool kayıtlarını paralel tarar. Her tarama SEOHistory
    # tablosuna yeni satır yazar → "Tarihsel SEO Grafiği" otomatik birikir.
    #
    # Fiyat taraması ile AYNI saatlerde ama 30 DK SONRA tetiklenir (45 yerine).
    # Bunun nedeni:
    #   • Fiyat taraması (3:15, 9:15, ...) ~10-15 dk sürer
    #   • SEO taraması fiyat bitmeden başlarsa Playwright/HB API rate-limit
    #     paylaşımında çakışma → her ikisi de yavaşlar veya bot algılanır
    #   • 30 dk fark = fiyat işi temiz biter, SEO için fresh proxy/UA havuzu
    #
    # Günde 4 SEO taraması (03:45, 09:45, 15:45, 21:45)
    'check-seo-trackers-strategic': {
        'task': 'worker.check_keyword_trackers_task',
        'schedule': crontab(minute=45, hour='3,9,15,21'),  # ✅ Fiyat'tan 30dk sonra
        'options': {
            'expires': 1800,       # 30dk içinde worker almazsa düş
            'time_limit': 1500,    # 25dk hard kill (SEO daha uzun sürer)
            'soft_time_limit': 1400,
        },
    },
}

# HOTFIX 1.91: Beat config'i de aynı timezone'a hizalı olduğundan emin ol
celery.conf.update(
    timezone='Europe/Istanbul',     # ⚠ Stratejik crontab için ZORUNLU
    enable_utc=False,                # Türkiye yerel saatiyle çalış
)

# HOTFIX 1.29: Beat startup-immediate — worker ayağa kalkar kalkmaz ilk turu tetikler
# (varsayılan davranış 1 saat bekle olduğu için ilk taramaya bu süre kadar geç başlıyor).
celery.conf.beat_max_loop_interval = 60  # beat her 60s'de schedule'ı yeniden değerlendirir
celery.conf.beat_schedule_filename = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'celerybeat-schedule'
)

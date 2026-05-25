import os
import ssl
from dotenv import load_dotenv

load_dotenv()

class Config:
    # SECRET_KEY zorunlu — env'den okunmazsa uygulama açılmaz.
    # Üretimde rastgele ve >=32 karakter olmalı: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY ortam değişkeni tanımlı değil. "
            ".env dosyasına ekleyin veya export edin. "
            "Üret: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )

    # ── HOTFIX FAZ 1: PostgreSQL Geçişi ──────────────────────────────────────
    # Üretim ve geliştirme için PostgreSQL. Eşzamanlı Celery worker'ları + zaman
    # serisi (PriceHistory, StockHistory) verisi için SQLite yetersiz kalıyordu.
    #
    # DATABASE_URL .env dosyasından alınır. Tanımlı değilse yerel default'a düşer:
    #   postgresql://bmk_user:bmk_pass@localhost:5432/saas_db
    #
    # Heroku/Render benzeri host'lar `postgres://` schema'sı kullanır; SQLAlchemy
    # 2.x `postgresql://` ister — otomatik düzeltme aşağıda.
    _raw_db_url = os.environ.get(
        'DATABASE_URL',
        'postgresql://mac@localhost:5432/saas_db'
    )
    if _raw_db_url.startswith('postgres://'):
        _raw_db_url = _raw_db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _raw_db_url

    # Bağlantı havuzu — Celery worker'ları + Flask + arka plan tarayıcılar
    # eş zamanlı bağlantı talep ettiği için pool genişletildi.
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'max_overflow': 20,
        'pool_timeout': 30,
        'pool_recycle': 1800,    # 30 dk: idle bağlantıları yenile
        'pool_pre_ping': True,   # bağlantı düştüyse önce ping at, otomatik tekrar bağla
    }

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
    
    # App settings
    APP_NAME = 'BMK Rekabet İstihbaratı'
    APP_VERSION = '1.0.0'
    
    # Approval mode: 'manual' or 'auto'
    APPROVAL_MODE = os.environ.get('APPROVAL_MODE', 'manual')
    
    # Free trial duration in days
    FREE_TRIAL_DAYS = int(os.environ.get('FREE_TRIAL_DAYS', '14'))
    
    # ── Celery Configuration ─────────────────────────────────────────────────
    # HOTFIX 1.53: Upstash (bulut Redis) → Lokal Redis geçişi.
    # Upstash free tier aylık 500K request limitiyle Celery polling yükünü
    # kaldıramıyor (worker + beat birlikte birkaç günde tüketiyor).
    # Lokal Redis (brew install redis, brew services start redis):
    #   • Ücretsiz, sınırsız
    #   • Network latency yok (Upstash ABD, biz TR → ~300ms tasarruf/task)
    #   • İnternet kopukluğunda da çalışır
    # CELERY_BROKER_URL env'de tanımlı değilse lokal'e düşer.
    broker_url = os.environ.get(
        'CELERY_BROKER_URL',
        'redis://localhost:6379/0'   # ← Lokal Redis (rediss:// SSL DEĞİL, redis:// plain)
    )
    result_backend = broker_url
    task_serializer = 'json'
    accept_content = ['json']
    result_serializer = 'json'
    timezone = 'Europe/Istanbul'
    # SSL ayarları SADECE bulut/managed Redis için anlamlı (rediss://).
    # Lokal redis:// ise SSL yok — bu blokları otomatik atlatmak için scheme kontrolü.
    _is_ssl_broker = broker_url.startswith('rediss://')
    if _is_ssl_broker:
        broker_use_ssl = {'ssl_cert_reqs': ssl.CERT_NONE}
        redis_backend_use_ssl = {'ssl_cert_reqs': ssl.CERT_NONE}

    # ── Resilience: Upstash / managed Redis idle-connection drops ──
    # Broker/result bağlantıları kopduğunda otomatik retry/re-establish
    broker_connection_retry_on_startup = True
    broker_connection_retry = True
    broker_connection_max_retries = None  # sonsuz retry
    broker_pool_limit = None  # bağlantı havuzu sınırsız — idle drop sonrası yeniden üret
    broker_heartbeat = 30  # saniye — Upstash sessiz socket kapatmaz
    broker_transport_options = {
        'socket_keepalive': True,
        'socket_timeout': 30,
        'socket_connect_timeout': 30,
        'retry_on_timeout': True,
        'health_check_interval': 25,
        'visibility_timeout': 3600,
    }
    result_backend_transport_options = {
        'socket_keepalive': True,
        'socket_timeout': 30,
        'socket_connect_timeout': 30,
        'retry_on_timeout': True,
        'health_check_interval': 25,
    }
    redis_socket_keepalive = True
    redis_socket_timeout = 30
    redis_socket_connect_timeout = 30
    redis_retry_on_timeout = True
    redis_health_check_interval = 25
    # Worker sağlamlığı
    worker_cancel_long_running_tasks_on_connection_loss = True
    worker_prefetch_multiplier = 1
    task_acks_late = True
    task_reject_on_worker_lost = True


# ── Test Config ─────────────────────────────────────────────────────────────
# pytest fixture'ları bu Config'i kullanır — SQLite in-memory, CSRF/limit kapalı.
# Config'in fail-loud SECRET_KEY kontrolünden kaçınmak için BAĞIMSIZ class.
class TestConfig:
    TESTING = True
    SECRET_KEY = 'test-secret-key-not-for-production-' + 'x' * 32
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}  # SQLite pool opsiyonları kabul etmez
    WTF_CSRF_ENABLED = False
    APP_NAME = 'BMK (Test)'
    APP_VERSION = 'test'
    APPROVAL_MODE = 'auto'  # Testte kayıt sonrası onay beklemesin
    FREE_TRIAL_DAYS = 14
    GROQ_API_KEY = ''  # Testte Groq çağrılarını mock'larız

    # Celery — testte memory broker (queue gerçekten çalışmaz, .delay no-op)
    broker_url = 'memory://'
    result_backend = 'cache+memory://'
    task_always_eager = True       # task.delay() → senkron çağrı, await
    task_eager_propagates = False  # task içi exception test'i bozmasın
    task_serializer = 'json'
    accept_content = ['json']
    result_serializer = 'json'
    timezone = 'Europe/Istanbul'

    # Rate limiter — testte memory + yüksek limit (rate-limit testleri hariç)
    RATELIMIT_STORAGE_URI = 'memory://'
    RATELIMIT_ENABLED = True  # default'lar tetiklenmesin, endpoint başına override

"""
logging_config.py — Merkezi loglama + observability yapılandırması.

İki hedef:
  • Konsol (geliştirme — renkli, kısa format)
  • Rotating file: logs/app.log (üretim — 10MB max, 5 yedek)
  • Sentry (opsiyonel — SENTRY_DSN env varsa otomatik aktif)

Kullanım:
    from logging_config import setup_logging
    setup_logging()  # app.py veya worker.py başlangıcında bir kez

Modüller:
    import logging
    log = logging.getLogger(__name__)
    log.info("Mesaj")
    log.exception("Hata oldu")   # sadece except bloğunda — traceback ekler
"""
import logging
import logging.handlers
import os
import sys

_CONFIGURED = False
_SENTRY_CONFIGURED = False


def setup_logging(level=None, log_file=None, app_name='bmk'):
    """Logging'i yapılandır. İdempotent — birden fazla çağrı güvenli."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    # Seviye: env > parametre > default INFO
    env_level = os.environ.get('LOG_LEVEL', '').upper()
    level = getattr(logging, env_level, level or logging.INFO)

    # Log dizini
    root_dir = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(root_dir, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    log_file = log_file or os.path.join(logs_dir, f'{app_name}.log')

    # Formatlar
    console_fmt = logging.Formatter(
        '%(asctime)s %(levelname).1s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )
    file_fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # Root logger — varsa handler'ları temizle (re-init senaryosu)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(console_fmt)
    root.addHandler(console)

    # File handler — 10MB, 5 yedek
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8',
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(file_fmt)
    root.addHandler(file_handler)

    # Üçüncü taraf gürültüsünü azalt
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('selenium').setLevel(logging.WARNING)
    logging.getLogger('playwright').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)  # zaten EndpointFilter var

    logging.getLogger(__name__).info("Logging hazır — seviye=%s, dosya=%s",
                                     logging.getLevelName(level), log_file)

    # Sentry'yi opsiyonel olarak başlat — DSN env varsa
    setup_sentry()


def setup_sentry(dsn=None, environment=None, release=None):
    """Sentry SDK'sını başlat — Flask + Celery + logging entegrasyonları.

    SENTRY_DSN env yoksa sessizce no-op olur (geliştirmede gürültü yapmaz).
    İdempotent — birden fazla çağrı güvenli.

    Args:
        dsn:          opsiyonel; verilmezse SENTRY_DSN env okunur
        environment:  'production' | 'staging' | 'development' (default 'development')
        release:      git SHA veya sürüm etiketi (default 'unknown')
    """
    global _SENTRY_CONFIGURED
    if _SENTRY_CONFIGURED:
        return

    dsn = dsn or os.environ.get('SENTRY_DSN', '').strip()
    if not dsn:
        return  # Sentry aktif değil — sessiz çık

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logging.getLogger(__name__).warning(
            "[Sentry] sentry-sdk yüklü değil — atlanıyor. "
            "Yüklemek için: pip install 'sentry-sdk[flask]'"
        )
        return

    environment = environment or os.environ.get('FLASK_ENV') or os.environ.get('SENTRY_ENVIRONMENT', 'development')
    release = release or os.environ.get('SENTRY_RELEASE') or os.environ.get('GIT_SHA', 'unknown')

    # Logging integration: WARNING+ → Sentry breadcrumb; ERROR+ → event
    logging_int = LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)

    try:
        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration(), CeleryIntegration(), logging_int],
            traces_sample_rate=float(os.environ.get('SENTRY_TRACES_RATE', '0.1')),  # 10%
            send_default_pii=False,  # KVKK — kişisel veri otomatik gönderilmez
            environment=environment,
            release=release,
            attach_stacktrace=True,
        )
        _SENTRY_CONFIGURED = True
        logging.getLogger(__name__).info(
            "[Sentry] aktif — environment=%s, release=%s", environment, release
        )
    except Exception:
        logging.getLogger(__name__).exception("[Sentry] init başarısız")

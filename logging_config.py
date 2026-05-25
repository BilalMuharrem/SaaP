"""
logging_config.py — Merkezi loglama yapılandırması.

İki hedef:
  • Konsol (geliştirme — renkli, kısa format)
  • Rotating file: logs/app.log (üretim — JSON benzeri, 10MB max, 5 yedek)

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

"""
app.py — BMK Rekabet İstihbaratı Flask uygulaması.

Mimari:
    create_app(config_object)   → Yeni Flask örneği üretir, uzantıları ve
                                  blueprint'leri bağlar.
    app                         → Modül seviyesinde tek örnek; worker.py ve
                                  gunicorn bu örneği import eder.

Tüm route'lar `blueprints/` altındaki modüllerde tanımlıdır:
    auth, dashboard, jobs, tracked, seo, ai_consultant, notifications, plans, admin
"""
import logging
import os
import sys

from flask import render_template
from flask_login import current_user

from extensions import db, login_manager, limiter, csrf
from models import User, Notification, init_db
from config import Config
from logging_config import setup_logging
from utils.filters import register_filters
from blueprints import register_blueprints

# Logging'i import sırasında bir kez kur (idempotent)
setup_logging(app_name='bmk-web')


# ── Werkzeug log gürültüsünü sustur ───────────────────────────────────────
# /api/system-status 5 saniyede bir poll ediliyor; /healthz UptimeRobot tarafından.
# İkisi de logu kirletir.
_NOISY_ENDPOINTS = ('/api/system-status', '/healthz')

class _EndpointFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return not any(ep in msg for ep in _NOISY_ENDPOINTS)

logging.getLogger('werkzeug').addFilter(_EndpointFilter())


def create_app(config_object=Config):
    """Yeni Flask uygulaması üret ve uzantıları bağla.

    Test'lerde farklı config nesnesi geçilerek izole örnek üretilebilir.
    """
    from flask import Flask
    flask_app = Flask(__name__)
    flask_app.config.from_object(config_object)

    # Uzantılar
    db.init_app(flask_app)
    login_manager.init_app(flask_app)
    limiter.init_app(flask_app)
    csrf.init_app(flask_app)

    # Jinja filtreleri + blueprint'ler
    register_filters(flask_app)
    register_blueprints(flask_app)

    # Context processor + hata sayfaları
    flask_app.context_processor(_inject_global_data)
    flask_app.register_error_handler(403, _forbidden)
    flask_app.register_error_handler(404, _not_found)
    flask_app.register_error_handler(429, _too_many_requests)

    return flask_app


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def _inject_global_data():
    """Layout template'lerinde kullanılabilen genel context.

    Sağlananlar:
        unread_notifications  — kullanıcının okunmamış bildirim sayısı
        pending_count         — (sadece admin için) bekleyen onay sayısı
    """
    data = {'unread_notifications': 0, 'pending_count': 0}
    if current_user.is_authenticated:
        data['unread_notifications'] = Notification.query.filter_by(
            user_id=current_user.id, is_read=False
        ).count()
        # FAZ 6A: Admin her sayfadayken bekleyen onay sayısını görür
        if current_user.is_admin:
            data['pending_count'] = User.query.filter_by(
                is_admin=False, is_approved=False
            ).count()
    return data


def _forbidden(e):
    return render_template('error.html', code=403,
                           message='Bu sayfaya erişim yetkiniz yok.'), 403


def _not_found(e):
    return render_template('error.html', code=404,
                           message='Aradığınız sayfa bulunamadı.'), 404


def _too_many_requests(e):
    # AJAX/JSON istemcisi ise JSON döndür; normal sayfa ise HTML
    from flask import request, jsonify
    detail = getattr(e, 'description', 'İstek limiti aşıldı. Birkaç dakika sonra tekrar deneyin.')
    if (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in (request.headers.get('Accept') or '')):
        return jsonify({'success': False, 'error': str(detail), 'rate_limited': True}), 429
    return render_template(
        'error.html', code=429,
        message=f'İstek limitiniz aşıldı. {detail} Lütfen birkaç dakika sonra tekrar deneyin.'
    ), 429


# Modül seviyesinde tek örnek — worker.py ve gunicorn bunu bekler.
app = create_app()


def _spawn_background_services():
    """app.py tek başına çalıştırıldığında Celery worker + beat'i (ve macOS'ta
    caffeinate'i) otomatik alt-süreç olarak başlatır → tek `python app.py` ile
    web + işçi + zamanlayıcı birden ayağa kalkar, Ctrl+C ile hepsi kapanır.

    Neden burada (reloader parent'ında): debug=True reloader __main__'i iki kez
    çalıştırır. Alt-süreçleri SADECE parent'ta (WERKZEUG_RUN_MAIN tanımsız) bir
    kez başlatırız; child her kod-reload'unda yeniden doğsa da worker/beat parent'a
    bağlı kaldığı için tekrar tekrar açılmaz.

    Kapatmak için: BMK_AUTOSTART_WORKERS=0 (örn. ./scripts/start.sh kullanırken).
    """
    import subprocess
    import atexit
    import signal

    here = os.path.dirname(os.path.abspath(__file__))
    py = sys.executable  # app.py'yi çalıştıran aynı (.venv) yorumlayıcı
    services = [
        ("worker", [py, "-m", "celery", "-A", "extensions.celery", "worker",
                    "--pool=solo", "--loglevel=info"]),
        ("beat",   [py, "-m", "celery", "-A", "extensions.celery", "beat",
                    "--loglevel=info", "--schedule=./celerybeat-schedule"]),
    ]
    # macOS: caffeinate ile sistemi uyanık tut → gece 03:15 taraması kaçmasın.
    if sys.platform == "darwin":
        services.append(("caffeinate", ["caffeinate", "-i"]))

    procs = []
    for name, cmd in services:
        try:
            # stdout/stderr terminale miras kalır → worker/beat adımları aynı
            # konsolda canlı görünür (kullanıcının istediği "tek yerden izleme").
            p = subprocess.Popen(cmd, cwd=here)
            procs.append((name, p))
            print(f"  ✅ {name} başlatıldı (pid={p.pid})")
        except Exception as exc:  # bir servis açılmazsa web yine çalışsın
            print(f"  ⚠️  {name} başlatılamadı: {exc}")

    def _cleanup(*_args):
        for name, p in procs:
            if p.poll() is None:  # hâlâ çalışıyorsa
                try:
                    p.terminate()
                except Exception:
                    pass
    atexit.register(_cleanup)
    # Ctrl+C / kill durumunda da çocukları topla
    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, lambda *_a: (_cleanup(), sys.exit(0)))
        except Exception:
            pass
    return procs


if __name__ == '__main__':
    init_db(app)
    # Periyodik tarama Celery Beat tarafından yönetiliyor (extensions.py beat_schedule)
    logging.info("[App] Celery Beat scheduling aktif. Stratejik 4 nokta tarama otomatik.")
    # HOTFIX 1.36: macOS AirPlay Receiver port 5000'i tutuyor → 5005
    _port = int(os.environ.get("PORT", "5005"))

    _autostart = os.environ.get("BMK_AUTOSTART_WORKERS", "1") != "0"

    # ── Arka plan servislerini otomatik başlat (reloader parent'ında, bir kez) ──
    if _autostart and not os.environ.get("WERKZEUG_RUN_MAIN"):
        print("\n" + "=" * 60)
        print("  ⚙️  ARKA PLAN SERVİSLERİ OTOMATİK BAŞLATILIYOR")
        print("     (worker + beat" + (" + caffeinate" if sys.platform == "darwin" else "") + ")")
        _spawn_background_services()
        print("=" * 60 + "\n", flush=True)

    # Konsola net başlangıç banner'ı. Banner'ı yalnızca asıl servis sürecinde
    # (WERKZEUG_RUN_MAIN='true') bas → reloader çift basımını önle. print kasıtlı.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        try:
            import redis as _redis
            _bu = app.config.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
            _redis.from_url(_bu).ping()
            _broker_ok = "✓ açık"
        except Exception:
            _broker_ok = "✗ KAPALI (worker çalışmaz!)"
        _auto_txt = ("worker + beat OTOMATİK başlatıldı (aynı pencerede)"
                     if _autostart else "otomatik başlatma KAPALI (BMK_AUTOSTART_WORKERS=0)")
        print("\n" + "=" * 60)
        print("  🚀 BMK WEB SUNUCUSU BAŞLADI")
        print(f"     → http://localhost:{_port}")
        print(f"     Redis broker : {_broker_ok}")
        print(f"     Arka plan    : {_auto_txt}")
        print("=" * 60 + "\n", flush=True)

    app.run(debug=True, host='0.0.0.0', port=_port)

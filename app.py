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

from flask import render_template
from flask_login import current_user

from extensions import db, login_manager
from models import User, Notification, init_db
from config import Config
from logging_config import setup_logging
from utils.filters import register_filters
from blueprints import register_blueprints

# Logging'i import sırasında bir kez kur (idempotent)
setup_logging(app_name='bmk-web')


# ── Werkzeug log gürültüsünü sustur ───────────────────────────────────────
# /api/system-status 5 saniyede bir poll ediliyor; logu kirletir.
class _EndpointFilter(logging.Filter):
    def filter(self, record):
        return '/api/system-status' not in record.getMessage()

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

    # Jinja filtreleri + blueprint'ler
    register_filters(flask_app)
    register_blueprints(flask_app)

    # Context processor + hata sayfaları
    flask_app.context_processor(_inject_global_data)
    flask_app.register_error_handler(403, _forbidden)
    flask_app.register_error_handler(404, _not_found)

    return flask_app


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def _inject_global_data():
    """Layout template'lerinde kullanılabilen genel context."""
    if current_user.is_authenticated:
        unread = Notification.query.filter_by(
            user_id=current_user.id, is_read=False
        ).count()
        return dict(unread_notifications=unread)
    return dict(unread_notifications=0)


def _forbidden(e):
    return render_template('error.html', code=403,
                           message='Bu sayfaya erişim yetkiniz yok.'), 403


def _not_found(e):
    return render_template('error.html', code=404,
                           message='Aradığınız sayfa bulunamadı.'), 404


# Modül seviyesinde tek örnek — worker.py ve gunicorn bunu bekler.
app = create_app()


if __name__ == '__main__':
    init_db(app)
    # Periyodik tarama Celery Beat tarafından yönetiliyor (extensions.py beat_schedule)
    logging.info("[App] Celery Beat scheduling aktif. Stratejik 4 nokta tarama otomatik.")
    # HOTFIX 1.36: macOS AirPlay Receiver port 5000'i tutuyor → 5005
    _port = int(os.environ.get("PORT", "5005"))
    app.run(debug=True, host='0.0.0.0', port=_port)

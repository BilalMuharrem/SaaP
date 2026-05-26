"""
blueprints/health.py — UptimeRobot / load balancer için sağlık kontrolü.

Endpoints:
    GET /healthz       — sade "alive" yanıtı (200 OK, "ok")
                          Hiçbir bağımlılığı sorgulamaz; sadece process ayakta mı.
    GET /healthz/deep  — DB + Redis bağlantısını kontrol eder.
                          200 = her ikisi de OK, 503 = en az biri başarısız.
                          UptimeRobot'un düşük sıklıkta poll'laması için (örn. 5 dk).
"""
import logging

from flask import Blueprint, jsonify
from sqlalchemy import text

from extensions import db

log = logging.getLogger(__name__)

bp = Blueprint('health', __name__)


@bp.route('/healthz')
def liveness():
    """Liveness probe — process yanıt veriyor mu? Bağımlılık sorgulamaz."""
    return 'ok', 200, {'Content-Type': 'text/plain; charset=utf-8'}


@bp.route('/healthz/deep')
def readiness():
    """Readiness probe — DB + Redis erişilebilir mi?"""
    checks = {'db': False, 'redis': False}
    overall_ok = True

    # DB ping — basit SELECT 1
    try:
        db.session.execute(text('SELECT 1'))
        checks['db'] = True
    except Exception as e:
        log.error("[Healthz] DB ping başarısız: %s", e)
        overall_ok = False

    # Redis ping (Celery broker üzerinden)
    try:
        from extensions import celery
        # Celery broker bağlantısını ping
        conn = celery.broker_connection()
        conn.ensure_connection(max_retries=1, timeout=2)
        conn.close()
        checks['redis'] = True
    except Exception as e:
        log.error("[Healthz] Redis ping başarısız: %s", e)
        overall_ok = False

    status = 200 if overall_ok else 503
    return jsonify({
        'status': 'ok' if overall_ok else 'degraded',
        'checks': checks,
    }), status

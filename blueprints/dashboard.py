"""
blueprints/dashboard.py — Müşteri ana paneli ve geçmiş.

Rotalar:
    GET /dashboard   — finansal komuta merkezi metrikleri + son işler
    GET /history     — sayfalı job listesi (admin ise admin.jobs'a yönlendirir)
"""
import logging

from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import login_required, current_user

from models import (
    Job, TrackedProduct, VulnerabilityAlert, PriceAlert, Notification,
)

log = logging.getLogger(__name__)

bp = Blueprint('dashboard', __name__)


@bp.route('/dashboard')
@login_required
def dashboard():
    recent_jobs = (Job.query.filter_by(user_id=current_user.id)
                   .order_by(Job.created_at.desc()).limit(50).all())
    total_jobs = Job.query.filter_by(user_id=current_user.id).count()
    completed_jobs = Job.query.filter_by(user_id=current_user.id, status='completed').count()

    # Vulnerability Radar: son 5 aktif uyarı (uyku modunda olsa da UI dinleyebilir)
    vulnerability_alerts = (VulnerabilityAlert.query.filter_by(
        user_id=current_user.id, is_active=True
    ).order_by(VulnerabilityAlert.created_at.desc()).limit(5).all())

    # ── FAZ 3.1: Finansal Komuta Merkezi metrikleri ────────────────────────
    # Tüm base ürünleri çek → her base'in grubundaki rakip min fiyatı bul →
    #   min_comp > unit_cost  → Kârlı Fırsat
    #   min_comp < unit_cost  → Zarar Riski
    profitable_count = 0
    risk_count = 0
    try:
        base_products = TrackedProduct.query.filter(
            TrackedProduct.user_id == current_user.id,
            TrackedProduct.is_base_product == True,
            TrackedProduct.unit_cost.isnot(None),
            TrackedProduct.unit_cost > 0,
            TrackedProduct.group_id.isnot(None),
        ).all()

        for base in base_products:
            competitors = TrackedProduct.query.filter(
                TrackedProduct.user_id == current_user.id,
                TrackedProduct.group_id == base.group_id,
                TrackedProduct.is_base_product == False,
                TrackedProduct.current_price.isnot(None),
                TrackedProduct.current_price > 0,
            ).all()
            if not competitors:
                continue
            min_comp = min(c.current_price for c in competitors)
            if min_comp > base.unit_cost:
                profitable_count += 1
            elif min_comp < base.unit_cost:
                risk_count += 1
    except Exception:
        log.exception("[Dashboard] Finansal metrik hesaplama hatası")

    try:
        active_alerts_count = PriceAlert.query.filter_by(
            user_id=current_user.id, is_active=True
        ).count()
    except Exception:
        log.exception("[Dashboard] Aktif alarm sayım hatası")
        active_alerts_count = 0

    try:
        recent_notifications = (Notification.query.filter_by(
            user_id=current_user.id
        ).order_by(Notification.created_at.desc()).limit(5).all())
    except Exception:
        log.exception("[Dashboard] Bildirim çekme hatası")
        recent_notifications = []

    return render_template(
        'dashboard.html',
        jobs=recent_jobs,
        total_jobs=total_jobs,
        completed_jobs=completed_jobs,
        vulnerability_alerts=vulnerability_alerts,
        profitable_count=profitable_count,
        risk_count=risk_count,
        active_alerts_count=active_alerts_count,
        recent_notifications=recent_notifications,
    )


@bp.route('/history')
@login_required
def history():
    if current_user.is_admin:
        return redirect(url_for('admin.admin_jobs'))

    page = request.args.get('page', 1, type=int)
    jobs = (Job.query.filter_by(user_id=current_user.id)
            .order_by(Job.created_at.desc())
            .paginate(page=page, per_page=20, error_out=False))
    return render_template('history.html', jobs=jobs)

"""
blueprints/admin.py — Yönetici paneli.

Rotalar:
    GET  /admin                                — dashboard (KPI, MRR, son işler)
    GET  /admin/customers
    POST /admin/customers/<id>/approve
    POST /admin/customers/<id>/toggle
    POST /admin/customers/<id>/plan
    GET  /admin/jobs
    GET  /admin/tracking
    GET  /admin/plans
    POST /admin/plans/<id>/edit
    GET/POST /admin/settings
"""
from datetime import timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify,
)
from flask_login import login_required
from sqlalchemy import func

from extensions import db
from models import (
    User, Plan, Job, TrackedProduct, PriceHistory, Setting, get_tr_now,
)
from utils.decorators import admin_required

bp = Blueprint('admin', __name__, url_prefix='/admin')


@bp.route('/api/pending-count')
@login_required
@admin_required
def api_pending_count():
    """FAZ 6B: Tab title polling için bekleyen onay sayısı."""
    count = User.query.filter_by(is_admin=False, is_approved=False).count()
    return jsonify({'pending_count': count})


@bp.route('')
@login_required
@admin_required
def admin_dashboard():
    total_customers = User.query.filter_by(is_admin=False).count()
    active_customers = User.query.filter_by(is_admin=False, is_active=True).count()
    pending_customers = User.query.filter_by(is_admin=False, is_approved=False).count()
    total_jobs = Job.query.count()
    completed_jobs = Job.query.filter_by(status='completed').count()
    running_jobs = Job.query.filter_by(status='running').count()
    pending_jobs = Job.query.filter_by(status='pending').count()

    recent_jobs = Job.query.order_by(Job.created_at.desc()).limit(50).all()
    pending_users = (User.query.filter_by(is_approved=False, is_admin=False)
                     .order_by(User.created_at.desc()).all())

    week_ago = get_tr_now() - timedelta(days=7)
    weekly_jobs = Job.query.filter(Job.created_at >= week_ago).count()

    # Plan dağılımı + MRR
    plans = Plan.query.all()
    plan_distribution = []
    mrr = 0.0
    for p in plans:
        count = User.query.filter_by(is_admin=False, plan_id=p.id, is_active=True).count()
        plan_distribution.append({'name': p.display_name, 'count': count})
        mrr += count * (p.price_monthly or 0)

    # MRR büyüme: 30 gün öncesiyle karşılaştır
    month_ago = get_tr_now() - timedelta(days=30)
    users_last_month = User.query.filter(
        User.is_admin == False, User.is_active == True, User.created_at <= month_ago
    ).count()
    mrr_growth = 0
    if users_last_month > 0 and active_customers > users_last_month:
        mrr_growth = round(((active_customers - users_last_month) / users_last_month) * 100)

    # Sistem yükü
    system_capacity = max(active_customers * 2, 20)
    system_load = (min(round((running_jobs / system_capacity) * 100), 100)
                   if system_capacity > 0 else 0)

    return render_template(
        'admin/dashboard.html',
        total_customers=total_customers,
        active_customers=active_customers,
        pending_customers=pending_customers,
        total_jobs=total_jobs,
        completed_jobs=completed_jobs,
        running_jobs=running_jobs,
        pending_jobs=pending_jobs,
        weekly_jobs=weekly_jobs,
        recent_jobs=recent_jobs,
        pending_users=pending_users,
        plan_distribution=plan_distribution,
        mrr=mrr,
        mrr_growth=mrr_growth,
        system_load=system_load,
    )


@bp.route('/customers')
@login_required
@admin_required
def admin_customers():
    customers = User.query.filter_by(is_admin=False).order_by(User.created_at.desc()).all()
    plans = Plan.query.filter_by(is_active=True).order_by(Plan.sort_order).all()
    return render_template('admin/customers.html', customers=customers, plans=plans)


@bp.route('/customers/<int:user_id>/approve', methods=['POST'])
@login_required
@admin_required
def admin_approve_customer(user_id):
    user = User.query.get_or_404(user_id)
    user.is_approved = True
    user.is_active = True
    if not user.plan_id:
        trial_plan = Plan.query.filter_by(name='trial').first()
        if trial_plan:
            user.plan_id = trial_plan.id
    if not user.trial_start:
        user.trial_start = get_tr_now()
    db.session.commit()
    flash(f'{user.full_name} onaylandı.', 'success')
    return redirect(url_for('admin.admin_customers'))


@bp.route('/customers/<int:user_id>/toggle', methods=['POST'])
@login_required
@admin_required
def admin_toggle_customer(user_id):
    user = User.query.get_or_404(user_id)
    user.is_active = not user.is_active
    db.session.commit()
    status = 'aktif' if user.is_active else 'devre dışı'
    flash(f'{user.full_name} artık {status}.', 'info')
    return redirect(url_for('admin.admin_customers'))


@bp.route('/customers/<int:user_id>/plan', methods=['POST'])
@login_required
@admin_required
def admin_change_plan(user_id):
    user = User.query.get_or_404(user_id)
    plan_id = request.form.get('plan_id', type=int)
    if plan_id:
        user.plan_id = plan_id
        db.session.commit()
        plan = db.session.get(Plan, plan_id)
        flash(f'{user.full_name} planı "{plan.display_name}" olarak değiştirildi.', 'success')
    return redirect(url_for('admin.admin_customers'))


@bp.route('/jobs')
@login_required
@admin_required
def admin_jobs():
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')
    query = Job.query.order_by(Job.created_at.desc())
    if status_filter:
        query = query.filter_by(status=status_filter)
    jobs = query.paginate(page=page, per_page=20, error_out=False)

    status_counts = dict(
        db.session.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
    )
    total_count = sum(status_counts.values())

    return render_template(
        'admin/jobs.html',
        jobs=jobs,
        status_filter=status_filter,
        status_counts=status_counts,
        total_count=total_count,
    )


@bp.route('/tracking')
@login_required
@admin_required
def admin_tracking():
    from worker import worker_state

    user_filter = request.args.get('user', '', type=str)
    status_filter = request.args.get('status', '')

    query = TrackedProduct.query.join(User, TrackedProduct.user_id == User.id)

    if user_filter:
        query = query.filter(TrackedProduct.user_id == int(user_filter))
    if status_filter == 'active':
        query = query.filter(TrackedProduct.is_active == True)
    elif status_filter == 'inactive':
        query = query.filter(TrackedProduct.is_active == False)

    page = request.args.get('page', 1, type=int)
    products = (query.order_by(TrackedProduct.last_checked.desc().nullsfirst())
                .paginate(page=page, per_page=25, error_out=False))

    total_tracked = TrackedProduct.query.count()
    active_tracked = TrackedProduct.query.filter_by(is_active=True).count()
    inactive_tracked = total_tracked - active_tracked

    day_ago = get_tr_now() - timedelta(hours=24)
    price_changes_24h = PriceHistory.query.filter(PriceHistory.timestamp >= day_ago).count()

    tracking_users = (db.session.query(
        User.id, User.full_name, func.count(TrackedProduct.id).label('count')
    ).join(TrackedProduct, TrackedProduct.user_id == User.id)
       .group_by(User.id, User.full_name).all())

    return render_template(
        'admin/tracking.html',
        products=products,
        total_tracked=total_tracked,
        active_tracked=active_tracked,
        inactive_tracked=inactive_tracked,
        price_changes_24h=price_changes_24h,
        worker_state=worker_state,
        tracking_users=tracking_users,
        user_filter=user_filter,
        status_filter=status_filter,
    )


@bp.route('/plans')
@login_required
@admin_required
def admin_plans():
    plans = Plan.query.order_by(Plan.sort_order).all()
    return render_template('admin/plans.html', plans=plans)


@bp.route('/plans/<int:plan_id>/edit', methods=['POST'])
@login_required
@admin_required
def admin_edit_plan(plan_id):
    plan = Plan.query.get_or_404(plan_id)
    plan.display_name = request.form.get('display_name', plan.display_name)
    plan.max_requests = request.form.get('max_requests', plan.max_requests, type=int)
    plan.max_tracked_products = request.form.get('max_tracked_products', plan.max_tracked_products, type=int)
    plan.period_type = request.form.get('period_type', plan.period_type)
    plan.price_monthly = request.form.get('price_monthly', plan.price_monthly, type=float)
    db.session.commit()
    flash(f'{plan.display_name} başarıyla güncellendi.', 'success')
    return redirect(url_for('admin.admin_plans'))


@bp.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_settings():
    # FAZ 10A: groq_api_key DB'den kaldırıldı (güvenlik). Artık sadece
    # .env üzerinden okunur (Config.GROQ_API_KEY). Burada güncelleme yok.
    if request.method == 'POST':
        Setting.set('approval_mode', request.form.get('approval_mode', 'manual'))
        Setting.set('free_trial_days', request.form.get('free_trial_days', '14'))
        flash('Ayarlar kaydedildi.', 'success')
        return redirect(url_for('admin.admin_settings'))

    # GROQ key durumunu read-only göster (sadece "set/unset" bilgisi, gerçek key DEĞİL)
    from config import Config
    settings = {
        'approval_mode': Setting.get('approval_mode', 'manual'),
        'free_trial_days': Setting.get('free_trial_days', '14'),
        'groq_api_key_configured': bool((Config.GROQ_API_KEY or '').strip()),
    }
    return render_template('admin/settings.html', settings=settings)

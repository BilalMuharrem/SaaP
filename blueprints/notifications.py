"""
blueprints/notifications.py — Bildirim merkezi.

Rotalar:
    GET  /notifications                            — sınıflandırılmış liste
    POST /notifications/read-all
    POST /notifications/clear
    GET  /notifications/<id>/open                  — okundu işaretle + yönlendir
    GET  /api/notifications/unread-count
    POST /api/notifications/mark-category-read
"""
import logging

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify,
)
from flask_login import login_required, current_user
from sqlalchemy import func

from extensions import db
from models import Notification, TrackedProduct, KeywordTracker

log = logging.getLogger(__name__)

VALID_CATS = ('all', 'price_down', 'price_up', 'combined',
              'opportunity', 'threat', 'system', 'seo')

CAT_LABEL = {
    'all': 'Tüm bildirimler',
    'price_down': 'Fiyatı düşen bildirimler',
    'price_up': 'Fiyatı yükselen bildirimler',
    'combined': 'Kombine analiz bildirimleri',
    'opportunity': 'Fırsat bildirimleri',
    'threat': 'Tehdit bildirimleri',
    'system': 'Sistem mesajları',
    'seo': 'SEO sıralama bildirimleri',
}

bp = Blueprint('notifications', __name__)


@bp.route('/notifications')
@login_required
def notifications():
    """HOTFIX 1.54: Sınıflandırılmış bildirim sayfası."""
    cat = request.args.get('cat', 'all')
    if cat not in VALID_CATS:
        cat = 'all'

    # Lazy AI backfill — category=NULL kayıtları sınıflandır
    try:
        from worker import classify_notification
        null_cats = Notification.query.filter_by(
            user_id=current_user.id, category=None
        ).limit(20).all()
        if null_cats:
            for n in null_cats:
                try:
                    n.category = classify_notification(n.message) or 'system'
                except Exception:
                    n.category = 'system'
            db.session.commit()
            log.info("[NotificationBackfill] %d kayıt sınıflandırıldı.", len(null_cats))
    except Exception:
        log.exception("[NotificationBackfill] Hata")
        db.session.rollback()

    try:
        page = max(1, int(request.args.get('page', 1)))
    except Exception:
        page = 1
    per_page = 50

    base_q = Notification.query.filter_by(user_id=current_user.id)
    if cat != 'all':
        base_q = base_q.filter_by(category=cat)
    base_q = base_q.order_by(Notification.created_at.desc())

    total = base_q.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    notifs = base_q.offset((page - 1) * per_page).limit(per_page).all()

    # HOTFIX 1.81: Sekme rozetleri okunmamış sayım
    cat_unread_rows = db.session.query(
        Notification.category, func.count(Notification.id)
    ).filter_by(user_id=current_user.id, is_read=False).group_by(Notification.category).all()
    cat_counts = {row[0] or 'system': row[1] for row in cat_unread_rows}
    cat_counts['all'] = sum(row[1] for row in cat_unread_rows)
    for c in VALID_CATS[1:]:
        cat_counts.setdefault(c, 0)

    sidebar_unread = cat_counts.get('all', 0)
    return render_template(
        'notifications.html',
        notifications=notifs,
        active_cat=cat,
        cat_counts=cat_counts,
        page=page,
        total_pages=total_pages,
        total=total,
        unread_notifications=sidebar_unread,
    )


@bp.route('/api/notifications/unread-count')
@login_required
def api_unread_notifications():
    count = Notification.query.filter_by(
        user_id=current_user.id, is_read=False
    ).count()
    return jsonify({'count': count})


@bp.route('/api/notifications/mark-category-read', methods=['POST'])
@login_required
def api_mark_category_read():
    """HOTFIX 1.81: AJAX endpoint — optimistic update."""
    data = request.get_json(silent=True) or {}
    cat = data.get('cat', 'all')
    if cat not in VALID_CATS:
        cat = 'all'

    q = Notification.query.filter_by(user_id=current_user.id, is_read=False)
    if cat != 'all':
        q = q.filter_by(category=cat)
    marked = q.update({'is_read': True}, synchronize_session=False)
    db.session.commit()

    rows = db.session.query(
        Notification.category, func.count(Notification.id)
    ).filter_by(user_id=current_user.id, is_read=False).group_by(Notification.category).all()
    cat_unread = {r[0] or 'system': r[1] for r in rows}
    cat_unread['all'] = sum(r[1] for r in rows)
    for c in VALID_CATS[1:]:
        cat_unread.setdefault(c, 0)

    return jsonify({'success': True, 'marked': marked, 'cat_unread': cat_unread})


@bp.route('/notifications/read-all', methods=['POST'])
@login_required
def read_all_notifications():
    marked = Notification.query.filter_by(
        user_id=current_user.id, is_read=False
    ).update({'is_read': True}, synchronize_session=False)
    db.session.commit()

    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    if is_ajax:
        return jsonify({'success': True, 'marked': marked})

    cat = request.args.get('cat') or request.form.get('cat', 'all')
    return redirect(url_for('notifications.notifications', cat=cat))


@bp.route('/notifications/<int:notif_id>/open')
@login_required
def notification_open(notif_id):
    """HOTFIX 1.80: Linke tıklamak = okundu sayılır, sonra yönlendir."""
    n = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first()
    if not n:
        flash('Bildirim bulunamadı.', 'error')
        return redirect(url_for('notifications.notifications'))

    if not n.is_read:
        n.is_read = True
        db.session.commit()

    to = (request.args.get('to') or '').strip().lower()

    # HOTFIX 1.99.1: Dış link (pazaryeri)
    if to == 'external' and n.link:
        return redirect(n.link)

    # HOTFIX 1.99.1: İç grafik — internal_link veya runtime resolution
    if to == 'internal':
        if n.internal_link:
            return redirect(n.internal_link)

        if n.link and n.link.startswith('http'):
            try:
                if n.category == 'seo':
                    kt = KeywordTracker.query.filter_by(
                        user_id=current_user.id, target_url=n.link, is_active=True
                    ).first()
                    if kt:
                        if kt.group_id:
                            return redirect(url_for('seo.seo_graph') + f'#group-{kt.group_id}')
                        return redirect(url_for('seo.seo_graph'))
                    return redirect(url_for('seo.seo_graph'))

                tp = TrackedProduct.query.filter_by(
                    user_id=current_user.id, url=n.link
                ).first()
                if tp and tp.group_id:
                    return redirect(url_for('tracked.tracked_products') + f'#group-{tp.group_id}')
                if tp:
                    return redirect(url_for('tracked.tracked_products'))
            except Exception:
                log.exception("[notification_open] iç çözünürlük hatası")

        # Fallback
        if n.category == 'seo':
            return redirect(url_for('seo.seo_graph'))
        if n.category == 'combined':
            return redirect(url_for('dashboard.history'))
        return redirect(url_for('tracked.tracked_products'))

    # Backward compat
    target = n.internal_link or n.link
    if not target:
        return redirect(url_for('notifications.notifications'))
    return redirect(target)


@bp.route('/notifications/clear', methods=['POST'])
@login_required
def clear_notifications_category():
    """HOTFIX 1.54: Sadece aktif sekmede gözüken bildirimleri sil."""
    cat = request.form.get('cat', 'all')
    if cat not in VALID_CATS:
        cat = 'all'

    q = Notification.query.filter_by(user_id=current_user.id)
    if cat != 'all':
        q = q.filter_by(category=cat)

    count = q.count()
    q.delete(synchronize_session=False)
    db.session.commit()
    flash(f"🗑️ {CAT_LABEL.get(cat, 'Bildirimler')} silindi ({count} kayıt).", 'success')
    return redirect(url_for('notifications.notifications', cat=cat))

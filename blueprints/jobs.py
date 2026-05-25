"""
blueprints/jobs.py — Analiz işleri (Job) ve durum API'leri.

Rotalar:
    GET/POST /new-request           — yeni analiz/takip talebi
    GET      /job/<id>              — job sonucu sayfası
    POST     /job/<id>/cancel
    GET      /job/<id>/report       — ham HTML sonuç
    GET      /api/job/<id>/status   — JSON status (polling)
    GET      /api/system-status     — worker durumu
    GET      /api/dashboard/jobs-status
"""
import logging
import os
import uuid
from datetime import timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, abort,
)
from flask_login import login_required, current_user

from extensions import db, limiter
from models import (
    Job, TrackedProduct, UsageLog, Setting, get_tr_now,
    attach_tracked_product_to_global,
)

log = logging.getLogger(__name__)

bp = Blueprint('jobs', __name__)


@bp.route('/new-request', methods=['GET', 'POST'])
@login_required
@limiter.limit("10 per hour", methods=['POST'])
def new_request():
    if not current_user.can_submit:
        flash('Talep hakkınız kalmadı. Planınızı yükseltin veya dönem yenilenmesini bekleyin.', 'warning')
        return redirect(url_for('dashboard.dashboard'))

    if request.method == 'POST':
        job_type = request.form.get('job_type', 'combined')
        urls_raw = request.form.get('urls', '')
        base_cost = request.form.get('base_cost', '').strip()

        urls = [u.strip() for u in urls_raw.strip().split('\n')
                if u.strip() and u.strip().startswith('http')]

        if not urls:
            flash('En az bir geçerli URL girmelisiniz.', 'error')
            return render_template('new_request.html')

        if len(urls) > 10:
            flash('Tek seferde en fazla 10 URL analiz edilebilir.', 'error')
            return render_template('new_request.html')

        # FAZ 3.5: Kombine/Fiyat/Yorum Analizi → SADECE TY & HB
        if job_type in ('combined', 'review', 'price'):
            unsupported = [u for u in urls
                           if not ('trendyol.com' in u.lower() or 'hepsiburada.com' in u.lower())]
            if unsupported:
                flash(
                    '⚠️ Detaylı Yorum Analizi (Kombine/Fiyat/Yorum) modülü şu an sadece '
                    'Trendyol ve Hepsiburada linklerini desteklemektedir. Diğer pazar yerlerini '
                    '(N11, Çiçeksepeti, PttAVM, Amazon) "Fiyat Takibi" modülüne ekleyebilirsiniz.',
                    'warning'
                )
                return render_template('new_request.html')

        # Faz 2D: API key form'dan kaldırıldı; admin Settings veya .env'den
        api_key = Setting.get('groq_api_key', '') or os.environ.get('GROQ_API_KEY', '')

        # Faz 3A: 'radar' job_type kaldırıldı (Zafiyet Radarı silindi).
        # Eski client'lardan gelirse Fiyat Takibi'ne yönlendir.
        if job_type == 'radar':
            job_type = 'track'

        if job_type == 'track':
            if (current_user.plan and current_user.plan.max_tracked_products > 0
                    and not current_user.is_admin):
                current_campaigns = (db.session.query(TrackedProduct.group_id)
                                     .filter_by(user_id=current_user.id)
                                     .distinct().count())
                if current_campaigns + 1 > current_user.plan.max_tracked_products:
                    flash(
                        f'Planınızın limitini ({current_user.plan.max_tracked_products} takip '
                        f'kampanya grubu) aşıyorsunuz. Lütfen yükseltin.', 'error'
                    )
                    return redirect(url_for('plans.user_plans'))

            group_id = str(uuid.uuid4())
            added = 0
            added_ids = []
            is_first = True
            parsed_cost = None
            if base_cost:
                try:
                    parsed_cost = float(base_cost)
                except ValueError:
                    parsed_cost = None

            for url in urls:
                exists = TrackedProduct.query.filter_by(
                    user_id=current_user.id, url=url
                ).first()
                if exists:
                    exists.group_id = group_id
                    exists.is_active = True
                    exists.is_price_tracked = True
                    exists.is_radar_tracked = False
                    exists.tracking_type = 'price'
                    if is_first and parsed_cost is not None:
                        exists.unit_cost = parsed_cost
                    added += 1
                    added_ids.append(exists.id)
                else:
                    tp = TrackedProduct(
                        user_id=current_user.id, url=url,
                        unit_cost=(parsed_cost if (is_first and parsed_cost is not None) else None),
                        group_id=group_id, is_base_product=is_first,
                        tracking_type='price',
                        is_price_tracked=True,
                        is_radar_tracked=False,
                    )
                    db.session.add(tp)
                    db.session.flush()
                    try:
                        attach_tracked_product_to_global(tp)
                    except Exception:
                        log.exception("[New analiz attach_global]")
                    added += 1
                    added_ids.append(tp.id)
                is_first = False

            db.session.add(UsageLog(
                user_id=current_user.id,
                action='add_tracked',
                details=f'{added} ürün eklendi',
            ))
            db.session.commit()

            if added > 0:
                try:
                    from worker import check_single_product_task
                    for pid in added_ids:
                        check_single_product_task.delay(pid)
                    flash(f'{added} adet ürün eklendi. İlk kontrol asenkron olarak başlatıldı.', 'success')
                except Exception:
                    log.exception("[App] Background task error")
                    flash(
                        f'{added} adet ürün eklendi, ancak görev kuyruğu (Celery) şu an erişilemiyor. '
                        f'Ürünler bir sonraki periyodik taramada (her 6 saatte bir) otomatik işlenecek.',
                        'warning'
                    )
            else:
                flash('Girdiğiniz ürünler zaten takip ediliyor.', 'info')

            return redirect(url_for('tracked.tracked_products'))

        # Standart job (analiz)
        if not current_user.can_submit:
            flash('Talep hakkınız kalmadı. Planınızı yükseltin veya dönem yenilenmesini bekleyin.', 'warning')
            return redirect(url_for('dashboard.dashboard'))

        job = Job(
            user_id=current_user.id,
            job_type=job_type,
            status='pending',
            api_key_used=api_key,
        )
        job.set_urls(urls)
        db.session.add(job)
        db.session.add(UsageLog(
            user_id=current_user.id,
            action='submit_job',
            details=f'{job_type}: {len(urls)} URL',
        ))
        db.session.commit()

        try:
            from worker import process_job_task
            process_job_task.delay(job.id)
            flash(
                '✅ Analiziniz başarıyla sıraya alındı! Arka planda yüzlerce veriyi tarıyoruz, '
                'tamamlandığında size bildirim göndereceğiz. Sitede özgürce gezinebilirsiniz.',
                'success'
            )
        except Exception:
            log.exception("[App] Hata! Celery görevi başlatılamadı")
            # Job DB'ye kaydedildi ama queue ulaşılamadı — kullanıcıya dürüst ol.
            # Job 'pending' kalır; worker döndüğünde işlenecek.
            flash(
                '⚠️ Analiz talebi kaydedildi ancak görev kuyruğu (Celery) şu an erişilemiyor. '
                'Sistem yöneticisi bilgilendirildi. Worker'
                ' yeniden başladığında bekleyen işiniz otomatik işlenecek.',
                'warning'
            )
        return redirect(url_for('dashboard.dashboard'))

    return render_template('new_request.html')


@bp.route('/job/<int:job_id>')
@login_required
def job_status(job_id):
    job = Job.query.get_or_404(job_id)
    if job.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return render_template('job_result.html', job=job)


@bp.route('/job/<int:job_id>/cancel', methods=['POST'])
@login_required
def cancel_job(job_id):
    job = Job.query.get_or_404(job_id)
    if job.user_id != current_user.id and not current_user.is_admin:
        abort(403)

    if job.status in ['pending', 'running']:
        job.status = 'cancelled'
        job.error_message = "Kullanıcı tarafından iptal edildi."
        db.session.commit()
        flash('Analiz başarıyla iptal edildi.', 'info')
    else:
        flash('Bu analiz zaten tamamlanmış veya iptal edilmiş.', 'warning')

    return redirect(url_for('dashboard.dashboard'))


@bp.route('/job/<int:job_id>/report')
@login_required
def job_report(job_id):
    job = Job.query.get_or_404(job_id)
    if job.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    if not job.result_html:
        abort(404)
    return job.result_html


@bp.route('/api/job/<int:job_id>/status')
@login_required
def api_job_status(job_id):
    job = Job.query.get_or_404(job_id)
    if job.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return jsonify({
        'status': job.status,
        'status_label': job.status_label,
        'completed_at': job.completed_at.isoformat() if job.completed_at else None,
        'duration': job.duration_str,
        'has_result': bool(job.result_html),
    })


@bp.route('/api/system-status')
@login_required
def api_system_status():
    from worker import worker_state

    running_job = (Job.query.filter_by(status='running')
                   .order_by(Job.started_at.desc()).first())
    if running_job:
        jt_map = {'price': 'Fiyat Analizi', 'review': 'Yorum Analizi', 'combined': 'Kombine Analiz'}
        return jsonify({
            'is_active': True,
            'text': f"#{running_job.id} {jt_map.get(running_job.job_type, 'Analiz')} işleniyor..."
        })

    return jsonify({
        'is_active': worker_state.get('is_active', False),
        'text': worker_state.get('status_text', 'Hazır ve izlemede.'),
    })


@bp.route('/api/dashboard/jobs-status')
@login_required
def api_dashboard_jobs_status():
    """Dashboard JS bunu poll eder; has_changes True ise sayfa reload edilir."""
    recent_threshold = get_tr_now() - timedelta(seconds=30)
    recently_changed = Job.query.filter(
        Job.user_id == current_user.id,
        Job.status.in_(['completed', 'failed']),
        Job.completed_at >= recent_threshold,
    ).count()
    return jsonify({'has_changes': recently_changed > 0})

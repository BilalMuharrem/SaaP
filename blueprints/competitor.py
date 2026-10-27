"""
blueprints/competitor.py — "Rakip Röntgeni" (Competitor X-Ray).

BMK özgünlük özelliği: tek bir rakip ürün için stratejik teşhis + karşı hamle.
Mevcut Job altyapısını (job_type='xray') kullanır — worker.run_competitor_xray
scrape eder, AI sentezler, result_html üretir. Sonuç /job/<id> ekranında görünür.
"""
import logging

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from extensions import db, limiter
from models import Job, UsageLog

log = logging.getLogger(__name__)

bp = Blueprint('competitor', __name__)


@bp.route('/rakip-rontgeni', methods=['GET', 'POST'])
@login_required
def xray():
    if request.method == 'POST':
        url = (request.form.get('url') or '').strip()
        low = url.lower()

        if not url.startswith('http') or not ('trendyol.com' in low or 'hepsiburada.com' in low):
            flash('Lütfen geçerli bir Trendyol veya Hepsiburada ürün linki girin.', 'warning')
            return redirect(url_for('competitor.xray'))

        if not current_user.can_submit:
            flash('Talep hakkınız kalmadı. Planınızı yükseltin veya dönem yenilenmesini bekleyin.', 'warning')
            return redirect(url_for('competitor.xray'))

        from services.ai.groq import resolve_groq_key
        api_key = resolve_groq_key()

        job = Job(
            user_id=current_user.id,
            job_type='xray',
            status='pending',
            api_key_used=api_key,
        )
        job.set_urls([url])
        db.session.add(job)
        db.session.add(UsageLog(
            user_id=current_user.id, action='submit_job', details=f'xray: {url[:80]}'
        ))
        db.session.commit()

        try:
            from worker import process_job_task
            process_job_task.delay(job.id)
            flash('🔬 Rakip Röntgeni sıraya alındı! Scrape + stratejik analiz birkaç '
                  'dakika sürebilir; tamamlanınca bu ekranda görünecek.', 'success')
        except Exception:
            log.exception('[Xray] Celery görevi başlatılamadı')
            flash('⚠️ Talep kaydedildi ama görev kuyruğu (Celery) şu an erişilemiyor. '
                  'Worker yeniden başladığında otomatik işlenecek.', 'warning')

        return redirect(url_for('jobs.job_status', job_id=job.id))

    return render_template('competitor_xray.html')

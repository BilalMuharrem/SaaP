"""
blueprints/onboarding.py — Yeni kullanıcı için 3 adımlık karşılama akışı.

Akış:
    1) /onboarding              — Hoşgeldin + "Başla" CTA
    2) /onboarding/product      — İlk ürün URL'i (POST: TrackedProduct kayıt)
    3) /onboarding/cost         — Birim maliyet (opsiyonel)
    4) /onboarding/done         — Tebrikler + dashboard'a yönlendir

Kullanıcı herhangi bir adımda "Atla" ile çıkabilir → onboarding_completed=True olur,
bir daha buraya yönlendirilmez. Tamamlama da aynı flag'i set eder.

Auth: login_required. has_completed_onboarding=True ise direkt dashboard'a yönlendirir.
"""
import logging
import uuid

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
)
from flask_login import login_required, current_user

from extensions import db
from models import TrackedProduct, attach_tracked_product_to_global

log = logging.getLogger(__name__)

bp = Blueprint('onboarding', __name__, url_prefix='/onboarding')


def _mark_completed():
    """Onboarding'i tamamlandı olarak işaretle (idempotent)."""
    if not current_user.onboarding_completed:
        current_user.onboarding_completed = True
        db.session.commit()


@bp.route('')
@login_required
def start():
    """Adım 0 — Karşılama. Zaten tamamlandıysa dashboard'a."""
    if current_user.onboarding_completed:
        return redirect(url_for('dashboard.dashboard'))
    return render_template('onboarding/start.html')


@bp.route('/product', methods=['GET', 'POST'])
@login_required
def product():
    """Adım 1 — İlk ürün URL'i."""
    if current_user.onboarding_completed:
        return redirect(url_for('dashboard.dashboard'))

    if request.method == 'POST':
        url = (request.form.get('url') or '').strip()
        if not url.startswith('http'):
            flash('Lütfen geçerli bir ürün linki girin (https:// ile başlamalı).', 'warning')
            return render_template('onboarding/product.html')

        # Aynı URL zaten varsa atla, yeniden oluşturma
        existing = TrackedProduct.query.filter_by(
            user_id=current_user.id, url=url
        ).first()
        if existing:
            existing.is_active = True
            existing.is_price_tracked = True
            db.session.commit()
            product_id = existing.id
        else:
            group_id = str(uuid.uuid4())
            tp = TrackedProduct(
                user_id=current_user.id,
                url=url,
                group_id=group_id,
                is_base_product=True,
                tracking_type='price',
                is_price_tracked=True,
                is_radar_tracked=False,
            )
            db.session.add(tp)
            db.session.flush()
            try:
                attach_tracked_product_to_global(tp)
            except Exception:
                log.exception("[Onboarding] attach_to_global fail")
            db.session.commit()
            product_id = tp.id

            # İlk fiyat kontrolünü kuyruğa at — kullanıcı /onboarding/done'a vardığında
            # ürün adı/fiyatı muhtemelen hazır olur.
            try:
                from worker import check_single_product_task
                check_single_product_task.delay(product_id)
            except Exception:
                log.exception("[Onboarding] check_single_product_task delay fail")

        return redirect(url_for('onboarding.cost', product_id=product_id))

    return render_template('onboarding/product.html')


@bp.route('/cost/<int:product_id>', methods=['GET', 'POST'])
@login_required
def cost(product_id):
    """Adım 2 — Birim maliyet (opsiyonel)."""
    if current_user.onboarding_completed:
        return redirect(url_for('dashboard.dashboard'))

    tp = TrackedProduct.query.filter_by(
        id=product_id, user_id=current_user.id
    ).first()
    if not tp:
        flash('Ürün bulunamadı. Tekrar deneyin.', 'warning')
        return redirect(url_for('onboarding.product'))

    if request.method == 'POST':
        raw_cost = (request.form.get('unit_cost') or '').strip().replace(',', '.')
        if raw_cost:
            try:
                cost_val = float(raw_cost)
                if cost_val > 0:
                    tp.unit_cost = cost_val
                    db.session.commit()
            except ValueError:
                flash('Geçersiz maliyet değeri — atlandı.', 'info')
        # Maliyet boşsa veya geçersizse: sessizce devam.
        return redirect(url_for('onboarding.done'))

    return render_template('onboarding/cost.html', product=tp)


@bp.route('/done')
@login_required
def done():
    """Adım 3 — Tebrikler ekranı + onboarding'i tamamlandı işaretle."""
    _mark_completed()
    return render_template('onboarding/done.html')


@bp.route('/skip', methods=['POST', 'GET'])
@login_required
def skip():
    """Herhangi bir adımdan onboarding'i atla."""
    _mark_completed()
    flash('Onboarding atlandı. İstediğin zaman ürün ekleyebilirsin.', 'info')
    return redirect(url_for('dashboard.dashboard'))

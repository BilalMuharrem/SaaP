"""
blueprints/tracked.py — Fiyat takibi (TrackedProduct) ürünleri ve grup işlemleri.

Rotalar:
    GET/POST /tracked-products
    GET      /tracked-products/export-excel
    GET      /tracked-products/export-pdf
    POST     /tracked-products/<id>/delete
    POST     /tracked-products/group/<gid>/rename
    POST     /tracked-products/group/<gid>/delete
    POST     /tracked-products/group/<gid>/add
    POST     /tracked-products/group/<gid>/cost
    POST     /tracked-products/alert/add
"""
import csv
import io
import json
import logging
import re
import uuid

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, abort, Response,
)
from flask_login import login_required, current_user

from extensions import db
from models import (
    TrackedProduct, PriceHistory, PriceAlert, KeywordTracker, KeywordPool,
    get_tr_now, attach_tracked_product_to_global, detach_tracked_product_from_global,
)

log = logging.getLogger(__name__)

bp = Blueprint('tracked', __name__)


@bp.route('/tracked-products', methods=['GET', 'POST'])
@login_required
def tracked_products():
    if request.method == 'POST':
        # Plan grup limiti
        if (current_user.plan and current_user.plan.max_tracked_products > 0
                and not current_user.is_admin):
            current_campaigns = (db.session.query(TrackedProduct.group_id)
                                 .filter_by(user_id=current_user.id)
                                 .distinct().count())
            if current_campaigns + 1 > current_user.plan.max_tracked_products:
                flash(
                    f'Fiyat takip limitinizi ({current_user.plan.max_tracked_products} '
                    f'takip paketi) doldurdunuz.', 'warning'
                )
                return redirect(url_for('tracked.tracked_products'))

        urls_raw = request.form.get('urls', '')
        if not urls_raw:
            urls_raw = request.form.get('url', '')

        # HOTFIX 1.87: Kullanıcının verdiği özel grup adı
        group_label_raw = (request.form.get('group_name') or '').strip()
        if len(group_label_raw) > 100:
            group_label_raw = group_label_raw[:100]
        group_label = group_label_raw or None

        raw_list = re.split(r'[\n\r\s,  ]+', urls_raw)
        valid_urls = [u.strip() for u in raw_list if u.strip().startswith('http')]

        if not valid_urls:
            flash('Geçerli bir ürün URL\'si girin.', 'error')
            return redirect(url_for('tracked.tracked_products'))

        group_id = str(uuid.uuid4())
        added_count = 0
        added_ids = []

        for idx, u in enumerate(valid_urls):
            exists = TrackedProduct.query.filter_by(
                user_id=current_user.id, url=u
            ).first()
            if not exists:
                tp = TrackedProduct(
                    user_id=current_user.id,
                    url=u,
                    group_id=group_id,
                    is_base_product=(idx == 0),
                    tracking_type='price',
                    is_price_tracked=True,
                    is_radar_tracked=False,
                    # HOTFIX 1.87: özel grup etiketi sadece BASE üründe
                    group_label=(group_label if idx == 0 else None),
                )
                db.session.add(tp)
                db.session.flush()
                try:
                    attach_tracked_product_to_global(tp)
                except Exception:
                    log.exception("[Tracked POST] attach_to_global fail")
                added_count += 1
                added_ids.append(tp.id)

        db.session.commit()
        if added_count > 0:
            queued = False
            try:
                from worker import check_single_product_task
                for pid in added_ids:
                    check_single_product_task.delay(pid)
                queued = True
            except Exception:
                log.exception("[App] Background task error")
            if queued:
                flash(f'{added_count} ürün takibe alındı. İlk fiyat kontrolü arka planda başlatıldı.', 'success')
            else:
                flash(
                    f'{added_count} ürün takibe alındı, ancak görev kuyruğu (Celery) erişilemiyor. '
                    f'Bir sonraki periyodik taramada otomatik fiyat çekilecek.', 'warning'
                )
        else:
            flash('Girdiğiniz ürünler zaten takip ediliyor veya geçerli değil.', 'info')
        return redirect(url_for('tracked.tracked_products'))

    # GET — Fiyat takibinde görünen tüm ürünler
    products = (TrackedProduct.query.filter_by(
        user_id=current_user.id, is_price_tracked=True
    ).order_by(TrackedProduct.created_at.desc()).all())

    # Grupla
    grouped = {}
    for p in products:
        gid = p.group_id or str(p.id)
        grouped.setdefault(gid, []).append(p)

    # ApexCharts verisi
    chart_data = {}
    group_costs = {}
    for gid, gp_list in grouped.items():
        gp_list.sort(key=lambda x: (not x.is_base_product, x.created_at))
        base_cost_val = None
        if gp_list and gp_list[0].unit_cost is not None and gp_list[0].unit_cost > 0:
            base_cost_val = float(gp_list[0].unit_cost)
        group_costs[gid] = base_cost_val

        series = []
        for gp in gp_list:
            history = (PriceHistory.query.filter_by(product_id=gp.id)
                       .order_by(PriceHistory.timestamp.asc()).all())
            data_points = [[int(h.timestamp.timestamp() * 1000), h.price] for h in history]

            if not data_points and gp.current_price and gp.current_price > 0:
                data_points.append([int(gp.created_at.timestamp() * 1000), gp.current_price])
            if data_points and gp.last_checked:
                ts_end = int(gp.last_checked.timestamp() * 1000)
                if ts_end > data_points[-1][0]:
                    data_points.append([ts_end, gp.current_price])

            if gp.product_name:
                name = gp.product_name[:35] + "..." if len(gp.product_name) > 35 else gp.product_name
            else:
                name = gp.platform_name or "Yükleniyor..."

            # HOTFIX 1.46: JSON/HTML attribute kesme-işareti güvenliği
            name = (name
                    .replace("'", "ʼ")     # U+02BC
                    .replace('"', "”")
                    .replace('\\', ' '))

            series.append({
                "name": ("👑 " if gp.is_base_product else "📉 ") + name,
                "data": data_points,
            })
        chart_data[gid] = json.dumps(series, ensure_ascii=False)

    # FAZ 2.1: Aktif PriceAlert eşikleri
    active_alerts = PriceAlert.query.filter_by(
        user_id=current_user.id, is_active=True
    ).all()
    product_alerts = {
        a.tracked_product_id: {"below": a.price_below, "above": a.price_above}
        for a in active_alerts
    }

    # HOTFIX 1.84: Hangi gruplar SEO takibinde {group_id: keyword}
    seo_grouped_rows = (KeywordTracker.query.filter_by(
        user_id=current_user.id, is_active=True
    ).filter(KeywordTracker.group_id.isnot(None)).all())
    group_seo = {}
    for kt in seo_grouped_rows:
        if kt.group_id not in group_seo:
            group_seo[kt.group_id] = kt.keyword

    return render_template(
        'tracked_products.html',
        grouped_products=grouped,
        chart_data=chart_data,
        group_costs=group_costs,
        product_alerts=product_alerts,
        group_seo=group_seo,
    )


@bp.route('/tracked-products/export-excel')
@login_required
def export_tracked_excel():
    products = (TrackedProduct.query.filter_by(user_id=current_user.id)
                .order_by(TrackedProduct.created_at.desc()).all())
    output = io.StringIO()
    output.write('﻿')  # BOM
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Ürün Adı', 'Platform', 'Mevcut Fiyat (₺)', 'Önceki Fiyat (₺)',
                     'Değişim (%)', 'Stok', 'Son Kontrol', 'URL'])
    for p in products:
        degisim = ''
        if p.previous_price and p.previous_price > 0 and p.current_price:
            degisim = f"{((p.current_price - p.previous_price) / p.previous_price) * 100:.1f}"
        writer.writerow([
            p.product_name or '-',
            p.platform_name or '-',
            f"{p.current_price:,.2f}" if p.current_price else '-',
            f"{p.previous_price:,.2f}" if p.previous_price else '-',
            degisim,
            p.current_stock if p.current_stock is not None else '-',
            p.last_checked.strftime('%d.%m.%Y %H:%M') if p.last_checked else '-',
            p.url,
        ])
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=bmk_takip_verileri.csv'},
    )


@bp.route('/tracked-products/export-pdf')
@login_required
def export_tracked_pdf():
    products = (TrackedProduct.query.filter_by(user_id=current_user.id)
                .order_by(TrackedProduct.created_at.desc()).all())
    rows = ''
    for p in products:
        degisim = ''
        if p.previous_price and p.previous_price > 0 and p.current_price:
            oran = ((p.current_price - p.previous_price) / p.previous_price) * 100
            renk = '#ef4444' if oran > 0 else '#10b981'
            degisim = f"<span style='color:{renk};font-weight:700;'>%{oran:+.1f}</span>"
        rows += f"""<tr>
            <td style="font-weight:600;">{p.product_name or '-'}</td>
            <td>{p.platform_name or '-'}</td>
            <td style="font-weight:700;">{f"{p.current_price:,.2f} ₺" if p.current_price else '-'}</td>
            <td>{f'{p.previous_price:,.2f} ₺' if p.previous_price else '-'}</td>
            <td>{degisim}</td>
            <td>{p.current_stock if p.current_stock is not None else '-'}</td>
            <td>{p.last_checked.strftime('%d.%m.%Y %H:%M') if p.last_checked else '-'}</td>
        </tr>"""
    return f"""<html><head><meta charset="utf-8">
    <style>
        body{{font-family:system-ui,sans-serif;padding:30px;color:#1a1a2e;}}
        h1{{font-size:22px;margin-bottom:4px;}}
        .sub{{color:#666;font-size:13px;margin-bottom:24px;}}
        table{{width:100%;border-collapse:collapse;font-size:12px;}}
        th{{background:#1a1a2e;color:#fff;padding:10px 8px;text-align:left;font-weight:600;}}
        td{{padding:8px;border-bottom:1px solid #e5e7eb;}}
        tr:nth-child(even){{background:#f9fafb;}}
        @media print{{body{{padding:10px;}}}}
    </style>
    <title>BMK Fiyat Takip Raporu</title></head><body>
    <h1>BMK Rekabet İstihbaratı — Fiyat Takip Raporu</h1>
    <div class="sub">{current_user.full_name} | {get_tr_now().strftime('%d.%m.%Y %H:%M')}</div>
    <table><thead><tr><th>Ürün</th><th>Platform</th><th>Fiyat</th><th>Önceki</th><th>Değişim</th><th>Stok</th><th>Son Kontrol</th></tr></thead>
    <tbody>{rows}</tbody></table>
    <script>window.onload=function(){{window.print();}}</script>
    </body></html>"""


@bp.route('/tracked-products/<int:id>/delete', methods=['POST'])
@login_required
def delete_tracked_product(id):
    product = TrackedProduct.query.get_or_404(id)
    if product.user_id != current_user.id:
        abort(403)
    db.session.delete(product)
    db.session.commit()
    flash('Ürün takipten kaldırıldı.', 'info')
    return redirect(url_for('tracked.tracked_products'))


# NOT (Faz 3A): Zafiyet Radarı (/zafiyet-radari/*) tamamen kaldırıldı.
# Eski URL'lere gelen istekler Flask varsayılan 404 döner — bilinçli karar.
# VulnerabilityAlert ve StockHistory modelleri DB'de kalır (migration yok),
# ama UI/route tarafında hiçbir referans yok.


@bp.route('/tracked-products/group/<string:group_id>/rename', methods=['POST'])
@login_required
def rename_tracked_group(group_id):
    new_name = (request.form.get('group_name') or '').strip()
    if len(new_name) > 100:
        new_name = new_name[:100]
    new_label = new_name or None

    # Sahiplik kontrolü + base
    base = TrackedProduct.query.filter_by(
        user_id=current_user.id, group_id=group_id, is_base_product=True
    ).first()
    if not base:
        base = TrackedProduct.query.filter_by(
            user_id=current_user.id, group_id=group_id
        ).first()
    if not base:
        flash('Grup bulunamadı.', 'warning')
        return redirect(url_for('tracked.tracked_products'))

    try:
        base.group_label = new_label
        db.session.commit()

        # HOTFIX 1.97: Grup adı = SEO anahtar kelime senkronizasyonu
        seo_synced = 0
        if new_label:
            from models import get_or_create_keyword_pool
            trackers = KeywordTracker.query.filter_by(
                user_id=current_user.id, group_id=group_id, is_active=True
            ).all()
            for kt in trackers:
                if kt.keyword == new_label:
                    continue
                old_pool_id = kt.pool_id
                kt.keyword = new_label
                kt.previous_page = kt.current_page or 0
                kt.previous_rank = kt.current_rank or 0
                kt.current_page = 0
                kt.current_rank = 0
                kt.last_checked = None

                new_pool = get_or_create_keyword_pool(kt.platform, new_label, kt.target_url)
                if new_pool:
                    if old_pool_id and old_pool_id != new_pool.id:
                        old_pool = KeywordPool.query.get(old_pool_id)
                        if old_pool:
                            old_pool.active_users_count = max(0, (old_pool.active_users_count or 0) - 1)
                            if old_pool.active_users_count == 0:
                                old_pool.is_dormant = True
                        new_pool.active_users_count = (new_pool.active_users_count or 0) + 1
                        new_pool.is_dormant = False
                    kt.pool_id = new_pool.id
                seo_synced += 1
            if seo_synced:
                db.session.commit()
                try:
                    from worker import check_keyword_trackers_task
                    check_keyword_trackers_task.delay([kt.id for kt in trackers])
                except Exception:
                    log.exception("[rename_tracked_group] SEO re-trigger fail")

        if new_label:
            msg = f'✏️ Grup adı güncellendi: "{new_label}"'
            if seo_synced:
                msg += f' &mdash; {seo_synced} SEO takibi yeni anahtar kelimeyle senkronize edildi (taramaya alındı).'
            flash(msg, 'success')
        else:
            flash('Grup adı temizlendi (otomatik isim kullanılacak).', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Güncelleme hatası: {e}', 'error')

    ref = request.form.get('return_to') or request.referrer or ''
    if 'seo-graph' in ref:
        return redirect(url_for('seo.seo_graph') + f'#group-{group_id}')
    return redirect(url_for('tracked.tracked_products'))


@bp.route('/tracked-products/group/<string:group_id>/delete', methods=['POST'])
@login_required
def delete_tracked_group(group_id):
    products = TrackedProduct.query.filter_by(
        user_id=current_user.id, group_id=group_id
    ).all()
    if not products:
        product = TrackedProduct.query.filter_by(
            user_id=current_user.id, id=group_id
        ).first()
        if product:
            products = [product]

    for p in products:
        try:
            detach_tracked_product_from_global(p)
        except Exception:
            log.exception("[delete_tracked_group detach]")
        db.session.delete(p)
    db.session.commit()

    flash('Ürün grubu takipten kaldırıldı.', 'info')
    return redirect(url_for('tracked.tracked_products'))


@bp.route('/tracked-products/group/<string:group_id>/add', methods=['POST'])
@login_required
def add_to_tracked_group(group_id):
    urls_raw = request.form.get('urls', '')
    if not urls_raw:
        urls_raw = request.form.get('url', '')

    raw_list = re.split(r'[\n\r\s,  ]+', urls_raw)
    valid_urls = [u.strip() for u in raw_list if u.strip().startswith('http')]

    if not valid_urls:
        return redirect(url_for('tracked.tracked_products'))

    if current_user.remaining_tracked_quota <= 0:
        flash('Takip kotanız doldu. Daha fazla ürün takip etmek için planınızı yükseltin.', 'danger')
        return redirect(url_for('plans.user_plans'))

    added_count = 0
    added_ids = []
    quota_exceeded = False

    for u in valid_urls:
        if u.startswith('__COST__:'):
            continue
        if current_user.remaining_tracked_quota <= 0:
            quota_exceeded = True
            break

        new_tp = TrackedProduct(
            user_id=current_user.id,
            url=u,
            group_id=group_id,
            is_base_product=False,
            tracking_type='price',
            is_price_tracked=True,
            is_radar_tracked=False,
        )
        db.session.add(new_tp)
        db.session.flush()
        added_count += 1
        added_ids.append(new_tp.id)

    db.session.commit()

    queued = False
    if added_count > 0:
        try:
            from worker import check_single_product_task
            for pid in added_ids:
                check_single_product_task.delay(pid)
            queued = True
        except Exception:
            log.exception("[App] Background task error")

    if quota_exceeded:
        flash(f'Kota sınırı nedeniyle eklenebilen ürün sayısı: {added_count}.', 'warning')
    elif added_count > 0 and not queued:
        flash(
            f'{added_count} ürün gruba eklendi, ancak görev kuyruğu (Celery) erişilemiyor. '
            f'Bir sonraki periyodik taramada işlenecek.', 'warning'
        )
    else:
        flash(f'{added_count} yeni ürün başarıyla gruba eklendi! Arka planda fiyatı kontrol edilecek.', 'success')
    return redirect(url_for('tracked.tracked_products'))


@bp.route('/tracked-products/group/<string:group_id>/cost', methods=['POST'])
@login_required
def update_tracked_group_cost(group_id):
    if not current_user.has_premium_access:
        flash('🔒 Bu özellik Profesyonel ve Kurumsal planlara özeldir.', 'warning')
        return redirect(url_for('plans.user_plans'))

    raw_cost = request.form.get('unit_cost')
    if raw_cost is None or raw_cost == '':
        raw_cost = request.form.get('target_price')
    try:
        cost = float(raw_cost) if raw_cost not in (None, '') else 0.0
    except ValueError:
        cost = 0.0

    products = TrackedProduct.query.filter_by(
        user_id=current_user.id, group_id=group_id
    ).all()
    new_cost = cost if cost > 0 else None
    for p in products:
        p.unit_cost = new_cost
    db.session.commit()
    flash('Birim maliyet başarıyla güncellendi.', 'success')
    return redirect(url_for('tracked.tracked_products'))


@bp.route('/tracked-products/alert/add', methods=['POST'])
@login_required
def add_price_alert():
    """FAZ 2.1: Çift yönlü akıllı tetikleyiciler."""
    def _parse_opt_float(raw):
        if raw is None:
            return None
        raw = str(raw).strip().replace(',', '.')
        if raw == '':
            return None
        try:
            v = float(raw)
            return v if v > 0 else None
        except ValueError:
            return None

    try:
        product_id = int(request.form.get('tracked_product_id') or 0)
    except (TypeError, ValueError):
        flash('⚠️ Geçersiz ürün.', 'danger')
        return redirect(url_for('tracked.tracked_products'))

    price_below = _parse_opt_float(request.form.get('price_below'))
    price_above = _parse_opt_float(request.form.get('price_above'))

    if product_id <= 0:
        flash('⚠️ Geçersiz ürün.', 'danger')
        return redirect(url_for('tracked.tracked_products'))

    if price_below is None and price_above is None:
        flash('⚠️ En az bir eşik değeri (Alt veya Üst Limit) girmelisiniz.', 'danger')
        return redirect(url_for('tracked.tracked_products'))

    if price_below is not None and price_above is not None and price_below >= price_above:
        flash('⚠️ Alt Limit, Üst Limit\'ten küçük olmalıdır.', 'danger')
        return redirect(url_for('tracked.tracked_products'))

    product = TrackedProduct.query.filter_by(
        id=product_id, user_id=current_user.id
    ).first()
    if not product:
        flash('⚠️ Ürün bulunamadı veya bu işlem için yetkiniz yok.', 'danger')
        return redirect(url_for('tracked.tracked_products'))

    existing = PriceAlert.query.filter_by(
        user_id=current_user.id,
        tracked_product_id=product_id,
        is_active=True,
    ).first()

    if existing:
        existing.price_below = price_below
        existing.price_above = price_above
        db.session.commit()
        flash('🔔 Alarm güncellendi. Yeni eşikler aktif.', 'success')
    else:
        db.session.add(PriceAlert(
            user_id=current_user.id,
            tracked_product_id=product_id,
            price_below=price_below,
            price_above=price_above,
            is_active=True,
        ))
        db.session.commit()
        flash('🔔 Alarm kuruldu. Eşikler sağlandığında anında haber vereceğiz.', 'success')

    return redirect(url_for('tracked.tracked_products'))

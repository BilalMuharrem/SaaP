"""
blueprints/seo.py — Trendyol SEO/anahtar kelime takibi.

Rotalar:
    GET/POST /seo-tracker
    POST     /seo-tracker/<id>/delete
    POST     /seo-tracker/<id>/refresh
    GET      /api/generate-seo-tips/<id>           — Groq long-tail önerileri
    POST     /tracked-products/group/<gid>/start-seo
    GET      /seo-graph
    POST     /seo-graph/group/<gid>/delete
    POST     /seo-graph/tracker/<id>/delete
"""
import json
import logging
import re
import uuid

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify,
)
from flask_login import login_required, current_user

from extensions import db, limiter
from models import (
    KeywordTracker, SEOHistory, TrackedProduct, GlobalProduct,
    attach_keyword_tracker_to_pool, detach_keyword_tracker_from_pool,
)

log = logging.getLogger(__name__)


# ── HOTFIX 10.3: Grup adı fallback zinciri ──────────────────────────────────
# Eskiden f'Grup {gid[:14]}' fallback'ı kullanıcıya çirkin UUID gösteriyordu.
# Yeni zincir (öncelik sırası):
#   1. Kullanıcının özel verdiği group_label (TP.group_label)
#   2. Base TP'nin ürün adı (kısaltılmış)
#   3. Gruba bağlı SEO takip listesindeki ilk KT.keyword (kullanıcıyı en iyi
#      anlatan değer — neyi takip ettiğini söyler)
#   4. Son çare: "Grup" + ekleme yok (UUID DEĞİL)
def _resolve_seo_group_label(group_id, user_id, kt_list=None, max_len=60):
    """Bir SEO/fiyat grubunun gösterim adını çöz. UUID asla göstermez."""
    base = TrackedProduct.query.filter_by(
        user_id=user_id, group_id=group_id, is_base_product=True
    ).first()
    rep = base or TrackedProduct.query.filter_by(
        user_id=user_id, group_id=group_id
    ).first()

    if base and base.group_label:
        return base.group_label[:max_len]
    if rep and rep.product_name:
        name = rep.product_name.strip()
        return name[:max_len] + ('…' if len(name) > max_len else '')
    # Son çare: SEO takibinden keyword
    if kt_list:
        kw = (kt_list[0].keyword or '').strip()
        if kw:
            return kw[:max_len] + ('…' if len(kw) > max_len else '')
    return 'İsimsiz Grup'

bp = Blueprint('seo', __name__)


@bp.route('/seo-tracker', methods=['GET', 'POST'])
@login_required
def seo_tracker():
    """SEO Takibi: Trendyol arama sonuçlarında ürün konumu izleme."""
    if request.method == 'POST':
        keyword = (request.form.get('keyword') or '').strip()
        target_url = (request.form.get('target_url') or '').strip()
        group_label_raw = (request.form.get('group_label') or '').strip()
        if len(group_label_raw) > 100:
            group_label_raw = group_label_raw[:100]
        group_label = group_label_raw or None
        return_to = (request.form.get('return_to') or '').strip()

        # HOTFIX 11.1: Hepsiburada SEO artık AKTİF (curl_cffi ile arama tarama,
        # proxy gerekmiyor). Trendyol + Hepsiburada ikisi de destekleniyor.
        raw_platform = (request.form.get('platform') or 'Trendyol').strip().lower()
        platform = 'Hepsiburada' if raw_platform.startswith('hep') else 'Trendyol'

        if not keyword or len(keyword) < 2:
            flash('⚠️ Lütfen geçerli bir arama kelimesi girin.', 'warning')
            return redirect(url_for(return_to or 'seo.seo_tracker'))
        if not target_url.startswith('http'):
            flash('⚠️ Hedef URL geçerli bir ürün linki olmalı.', 'warning')
            return redirect(url_for(return_to or 'seo.seo_tracker'))

        # Platform ↔ URL tutarlılık kontrolü
        _url_low = target_url.lower()
        if platform == 'Trendyol' and 'trendyol.com' not in _url_low:
            flash('⚠️ "Trendyol" platformu seçildi ama URL Trendyol ürün linki değil.', 'warning')
            return redirect(url_for(return_to or 'seo.seo_tracker'))
        if platform == 'Hepsiburada' and 'hepsiburada.com' not in _url_low:
            flash('⚠️ "Hepsiburada" platformu seçildi ama URL Hepsiburada ürün linki değil.', 'warning')
            return redirect(url_for(return_to or 'seo.seo_tracker'))

        exists = KeywordTracker.query.filter_by(
            user_id=current_user.id, keyword=keyword, target_url=target_url
        ).first()
        if exists:
            exists.is_active = True
            if group_label:
                exists.group_id = exists.group_id or f"solo-{exists.id}"
            db.session.commit()
            flash('🔁 Bu kelime + ürün takibi zaten kayıtlı, yeniden aktif edildi.', 'info')
        else:
            new_gid = f"solo-{uuid.uuid4().hex[:12]}" if group_label else None
            kt = KeywordTracker(
                user_id=current_user.id, platform=platform,
                keyword=keyword, target_url=target_url, is_active=True,
                group_id=new_gid,
            )
            db.session.add(kt)
            db.session.flush()
            try:
                attach_keyword_tracker_to_pool(kt)
            except Exception:
                log.exception("[SEO tracker attach_pool]")
            db.session.commit()

            # Grup etiketi opsiyonel olarak base TrackedProduct'a yazılır
            if group_label and new_gid:
                try:
                    base = TrackedProduct.query.filter_by(
                        user_id=current_user.id, url=target_url
                    ).first()
                    if base:
                        if not base.group_label:
                            base.group_label = group_label
                            db.session.commit()
                    else:
                        ph = TrackedProduct(
                            user_id=current_user.id,
                            url=target_url,
                            group_id=new_gid,
                            is_base_product=True,
                            tracking_type='seo',
                            is_price_tracked=False,
                            is_radar_tracked=False,
                            group_label=group_label,
                        )
                        db.session.add(ph)
                        db.session.commit()
                except Exception:
                    log.exception("[SEO group_label persist]")
                    db.session.rollback()

            flash('✅ Arama sırası takibi eklendi. İlk kontrol birkaç dakika içinde tamamlanır.', 'success')

            try:
                from worker import check_keyword_trackers_task
                check_keyword_trackers_task.delay([kt.id])
            except Exception:
                log.exception("[SEO] İlk kontrol tetiklenemedi")

        return redirect(url_for(return_to or 'seo.seo_tracker'))

    # GET — grup bazlı listeleme
    trackers = (KeywordTracker.query.filter_by(user_id=current_user.id)
                .order_by(KeywordTracker.created_at.desc()).all())

    grouped_seo = {}
    for kt in trackers:
        gid = kt.group_id or '__bireysel__'
        grouped_seo.setdefault(gid, []).append(kt)

    group_seo_labels = {}
    for gid, kt_list in grouped_seo.items():
        if gid == '__bireysel__':
            group_seo_labels[gid] = 'Bireysel Aramalar'
            continue
        # HOTFIX 10.3: UUID fallback yerine düzgün isim zinciri
        group_seo_labels[gid] = _resolve_seo_group_label(
            gid, current_user.id, kt_list=kt_list
        )

    return render_template(
        'seo_tracker.html',
        trackers=trackers,
        grouped_seo=grouped_seo,
        group_seo_labels=group_seo_labels,
    )


@bp.route('/seo-tracker/<int:tracker_id>/delete', methods=['POST'])
@login_required
def seo_tracker_delete(tracker_id):
    kt = KeywordTracker.query.filter_by(id=tracker_id, user_id=current_user.id).first()
    if not kt:
        flash('Kayıt bulunamadı.', 'warning')
        return redirect(url_for('seo.seo_tracker'))
    try:
        detach_keyword_tracker_from_pool(kt)
    except Exception:
        log.exception("[seo_tracker_delete detach_pool]")
    db.session.delete(kt)
    db.session.commit()
    flash('🗑️ Arama takibi silindi.', 'success')
    return redirect(url_for('seo.seo_tracker'))


@bp.route('/api/generate-seo-tips/<int:tracker_id>', methods=['GET', 'POST'])
@login_required
@limiter.limit("10 per hour;3 per minute")
def api_generate_seo_tips(tracker_id):
    """EPIC 8.1 / HOTFIX 1.98: Dinamik YZ SEO ipuçları.

    Rate limit: kullanıcı başına 3/dk, 10/saat — Groq API maliyeti koruması.
    """
    kt = KeywordTracker.query.filter_by(id=tracker_id, user_id=current_user.id).first()
    if not kt:
        return jsonify({'success': False, 'error': 'Takip kaydı bulunamadı.'}), 404

    keyword = kt.keyword or ''
    target_url = kt.target_url or ''

    # Ürün adı tespiti — slug → DB.product_name → GlobalProduct → URL
    product_name = ''
    try:
        if 'trendyol.com' in target_url.lower():
            m = re.search(r'/([^/]+)-p-\d+', target_url)
            if m:
                product_name = m.group(1).replace('-', ' ').strip()
        elif 'hepsiburada.com' in target_url.lower():
            m = re.search(r'/([^/]+)-p-[A-Za-z0-9]+', target_url)
            if m:
                product_name = m.group(1).replace('-', ' ').strip()
    except Exception:
        pass

    if not product_name:
        tp = TrackedProduct.query.filter_by(
            user_id=current_user.id, url=target_url
        ).first()
        if tp and tp.product_name:
            product_name = tp.product_name
        else:
            try:
                gp = GlobalProduct.query.filter_by(url=target_url).first()
                if gp and gp.product_name:
                    product_name = gp.product_name
            except Exception:
                pass

    if not product_name:
        product_name = target_url[:80]

    try:
        from worker import _resolve_groq_key
        api_key = _resolve_groq_key()
        if not api_key:
            return jsonify({
                'success': False,
                'error': 'YZ servisi şu an kullanılamıyor (API anahtarı yok). Lütfen yönetici ile iletişime geçin.'
            }), 503

        from groq import Groq
        client = Groq(api_key=api_key)

        system_prompt = (
            "Sen üst düzey bir e-ticaret SEO uzmanısın. "
            "Kullanıcılara Trendyol/Hepsiburada üzerinde organik görünürlüklerini "
            "artırma konusunda kısa, vurucu ve UYGULANABİLİR tavsiyeler verirsin. "
            "Kibar değil, DOĞRUDAN konuş. Sayısal/spesifik öneriler ver, klişeden kaç.\n\n"
            "ÇIKTI FORMATI (KESİN): Sadece geçerli JSON döndür, başka HİÇBİR metin yazma.\n"
            "Şu şemada:\n"
            "{\n"
            '  "relevance": "good" | "weak" | "off",\n'
            '  "diagnosis": "<2-3 cümlelik teşhis>",\n'
            '  "suggestions": ["<long-tail kelime 1>", "<long-tail kelime 2>", "<long-tail kelime 3>"]\n'
            "}\n\n"
            "relevance:\n"
            "  • 'good' = kelime ürünle çok iyi eşleşiyor ama rekabet yüksek\n"
            "  • 'weak' = kelime ürünle KISMEN eşleşiyor, daha spesifik olmalı\n"
            "  • 'off'  = kelime ürünle ALAKASIZ (örn: bebek mama kabını 'kedi maması'nda arıyor)\n\n"
            "diagnosis: Türkçe, 2-3 cümle, bold yok, emoji yok.\n"
            "suggestions: SADECE 3 adet uzun-kuyruklu (long-tail) anahtar kelime. Her biri 3-6 kelimeden oluşmalı, "
            "müşterinin gerçekten yazacağı doğal ifadeler olmalı. Markaları kullanma (Trendyol içinde dahili filtre)."
        )

        user_msg = (
            f"ÜRÜN: {product_name[:200]}\n"
            f"ÜRÜN LİNKİ: {target_url[:300]}\n"
            f"KULLANICININ ARADIĞI KELİME: {keyword[:120]}\n"
            f"SONUÇ: Ürün ilk 5 sayfada (~200 ürün arasında) bulunamadı.\n\n"
            "Bu kelime bu ürün için uygun mu? Eğer değilse, ürünün ASIL ne olduğunu link/isimden çıkar ve "
            "müşterinin kullanması gereken 3 nokta atışı uzun-kuyruklu kelime öner. JSON döndür."
        )

        resp = client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_msg},
            ],
            temperature=0.4,
            max_tokens=500,
            response_format={'type': 'json_object'},
        )
        raw = (resp.choices[0].message.content or '').strip()
        if raw.startswith('```'):
            raw = raw.strip('`')
            if raw.lower().startswith('json'):
                raw = raw[4:].strip()

        try:
            data = json.loads(raw)
        except Exception:
            return jsonify({
                'success': True,
                'relevance': 'weak',
                'diagnosis': raw[:400] if raw else 'YZ yanıtı yorumlanamadı. Lütfen tekrar deneyin.',
                'suggestions': [],
                'context': {'keyword': keyword, 'product_name': product_name[:120]},
            })

        relevance = (data.get('relevance') or 'weak').lower()
        if relevance not in ('good', 'weak', 'off'):
            relevance = 'weak'
        diagnosis = (data.get('diagnosis') or '').strip()[:600]
        suggestions = data.get('suggestions') or []
        if not isinstance(suggestions, list):
            suggestions = []
        suggestions = [str(s).strip()[:120] for s in suggestions if s][:3]

        return jsonify({
            'success': True,
            'relevance': relevance,
            'diagnosis': diagnosis or 'Bu kelime için belirgin bir teşhis üretilemedi.',
            'suggestions': suggestions,
            'context': {'keyword': keyword, 'product_name': product_name[:120]},
        })

    except ImportError:
        return jsonify({'success': False, 'error': 'YZ kütüphanesi sunucuda kurulu değil.'}), 500
    except Exception as e:
        log.exception("[SEO Tips API]")
        return jsonify({
            'success': False,
            'error': f'YZ analizi yapılamadı: {str(e)[:150]}',
        }), 500


@bp.route('/seo-tracker/<int:tracker_id>/refresh', methods=['POST'])
@login_required
@limiter.limit("20 per hour")
def seo_tracker_refresh(tracker_id):
    """Tek bir kelime takibini anında yeniden kontrol et.

    Rate limit: 20/saat — scraper kuyruğunu doldurmayı engeller.
    """
    kt = KeywordTracker.query.filter_by(id=tracker_id, user_id=current_user.id).first()
    if not kt:
        flash('Kayıt bulunamadı.', 'warning')
        return redirect(url_for('seo.seo_tracker'))
    try:
        from worker import check_keyword_trackers_task
        check_keyword_trackers_task.delay([kt.id])
        flash('🔄 Tarama kuyruğa alındı — birkaç saniye içinde güncellenecek.', 'success')
    except Exception:
        log.exception("[seo_tracker_refresh] Celery tetikleme hatası")
        flash('Kontrol kuyruğa alınamadı. Lütfen Celery worker\'ı kontrol edin.', 'error')
    return redirect(url_for('seo.seo_tracker'))


@bp.route('/tracked-products/group/<group_id>/start-seo', methods=['POST'])
@login_required
def start_group_seo(group_id):
    """HOTFIX 1.84: Grup bazlı toplu SEO başlatma."""
    keyword = (request.form.get('keyword') or '').strip()
    if not keyword:
        flash('Anahtar kelime gereklidir.', 'danger')
        return redirect(url_for('tracked.tracked_products'))
    if len(keyword) > 200:
        keyword = keyword[:200]

    products = TrackedProduct.query.filter_by(
        user_id=current_user.id, group_id=group_id, is_active=True
    ).all()
    if not products:
        flash('Grup bulunamadı veya ürün yok.', 'warning')
        return redirect(url_for('tracked.tracked_products'))

    added = 0
    skipped = 0
    new_ids = []
    for p in products:
        url = p.url or ''
        if not url:
            continue
        existing = KeywordTracker.query.filter_by(
            user_id=current_user.id,
            group_id=group_id,
            keyword=keyword,
            target_url=url,
        ).first()
        if existing:
            skipped += 1
            continue
        plat = ('Trendyol' if 'trendyol.com' in url.lower()
                else ('Hepsiburada' if 'hepsiburada.com' in url.lower() else 'Trendyol'))
        kt = KeywordTracker(
            user_id=current_user.id,
            platform=plat,
            keyword=keyword,
            target_url=url,
            group_id=group_id,
            is_active=True,
        )
        db.session.add(kt)
        db.session.flush()
        try:
            attach_keyword_tracker_to_pool(kt)
        except Exception:
            log.exception("[start_group_seo attach_pool]")
        new_ids.append(kt.id)
        added += 1

    db.session.commit()

    queued = False
    if new_ids:
        try:
            from worker import check_keyword_trackers_task
            check_keyword_trackers_task.delay(new_ids)
            queued = True
        except Exception:
            log.exception("[SEO group-start] Celery tetikleme hatası")

    if new_ids and not queued:
        flash(
            f'🔍 SEO takibi kaydedildi ({added} yeni, {skipped} atlandı), ancak görev kuyruğu '
            f'erişilemiyor. Periyodik taramada (her 6 saatte bir) işlenecek.', 'warning'
        )
    else:
        flash(f'🔍 SEO takibi başlatıldı: {added} yeni, {skipped} atlandı (zaten var).', 'success')
    return redirect(url_for('seo.seo_graph'))


@bp.route('/seo-graph/group/<group_id>/delete', methods=['POST'])
@login_required
def seo_graph_delete_group(group_id):
    """HOTFIX 1.85: Grup SEO takibini toplu sil (history dahil)."""
    trackers = KeywordTracker.query.filter_by(
        user_id=current_user.id, group_id=group_id
    ).all()
    if not trackers:
        flash('SEO grubu bulunamadı.', 'warning')
        return redirect(url_for('seo.seo_graph'))

    deleted = len(trackers)
    try:
        tracker_ids = [kt.id for kt in trackers]
        SEOHistory.query.filter(
            SEOHistory.keyword_tracker_id.in_(tracker_ids)
        ).delete(synchronize_session=False)
        for kt in trackers:
            try:
                detach_keyword_tracker_from_pool(kt)
            except Exception:
                pass
            db.session.delete(kt)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Silme hatası: {e}', 'error')
        return redirect(url_for('seo.seo_graph'))

    flash(f'🗑️ Grup SEO takibi silindi ({deleted} ürün).', 'success')
    return redirect(url_for('seo.seo_graph'))


@bp.route('/seo-graph/tracker/<int:tracker_id>/delete', methods=['POST'])
@login_required
def seo_graph_delete_tracker(tracker_id):
    """HOTFIX 1.85: Tekil SEO takibini sil (history dahil)."""
    kt = KeywordTracker.query.filter_by(id=tracker_id, user_id=current_user.id).first()
    if not kt:
        flash('Kayıt bulunamadı.', 'warning')
        return redirect(url_for('seo.seo_graph'))

    try:
        SEOHistory.query.filter_by(keyword_tracker_id=kt.id).delete(
            synchronize_session=False
        )
        try:
            detach_keyword_tracker_from_pool(kt)
        except Exception:
            pass
        db.session.delete(kt)
        db.session.commit()
        flash('🗑️ SEO takibi silindi.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Silme hatası: {e}', 'error')
    return redirect(url_for('seo.seo_graph'))


@bp.route('/seo-graph')
@login_required
def seo_graph():
    """HOTFIX 1.84: SEO grafik takibi sayfası."""
    trackers = (KeywordTracker.query.filter_by(
        user_id=current_user.id, is_active=True
    ).order_by(KeywordTracker.created_at.desc()).all())

    grouped = {}
    for kt in trackers:
        key = kt.group_id or '__solo__'
        grouped.setdefault(key, []).append(kt)

    chart_data = {}
    for gkey, kt_list in grouped.items():
        series = []
        for kt in kt_list:
            history = (SEOHistory.query
                       .filter_by(keyword_tracker_id=kt.id)
                       .order_by(SEOHistory.timestamp.asc())
                       .limit(200).all())
            points = []
            for h in history:
                ts = int(h.timestamp.timestamp() * 1000)
                rank = h.overall_rank if h.overall_rank > 0 else None
                points.append([ts, rank])
            if not points and (kt.current_page and kt.current_rank):
                overall = (((kt.current_page - 1) * 40 + kt.current_rank)
                           if (kt.current_page > 0 and kt.current_rank > 0) else None)
                if overall:
                    points.append([int((kt.last_checked or kt.created_at).timestamp() * 1000), overall])
            name_short = (kt.target_url or '').split('/')[-1][:30].replace("'", "ʼ").replace('"', '”')
            series.append({
                'name': name_short or f'URL #{kt.id}',
                'data': points,
            })
        chart_data[gkey] = json.dumps(series, ensure_ascii=False)

    group_labels = {}
    for gkey, kt_list in grouped.items():
        if gkey == '__solo__':
            group_labels[gkey] = 'Tekil Kelime Takipleri'
            continue
        # HOTFIX 10.3: UUID fallback yerine düzgün isim zinciri
        group_labels[gkey] = _resolve_seo_group_label(
            gkey, current_user.id, kt_list=kt_list, max_len=80
        )

    return render_template(
        'seo_graph.html',
        grouped=grouped,
        chart_data=chart_data,
        group_labels=group_labels,
    )

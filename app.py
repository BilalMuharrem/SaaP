"""
app.py — BMK Rekabet İstihbaratı Flask uygulaması.

Mimari (Faz 1A): App factory pattern.
    create_app()   → yeni Flask örneği üretir, uzantıları bağlar.
    app            → modül seviyesinde tek örnek; worker.py ve gunicorn bunu bekler.

Rotalar şu an hâlâ bu dosyada kayıtlı (Faz 1C-F'de blueprint'lere taşınacak).
"""
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_user, logout_user, login_required, current_user
import logging

from extensions import db, login_manager
from models import (
    User, Plan, Job, UsageLog, Setting, init_db,
    TrackedProduct, PriceHistory, StockHistory, VulnerabilityAlert,
    Notification, AiReport, PriceAlert, KeywordTracker, SEOHistory,
    GlobalProduct, KeywordPool,
    attach_tracked_product_to_global, attach_keyword_tracker_to_pool,
    detach_tracked_product_from_global, detach_keyword_tracker_from_pool,
    get_tr_now,
)
from config import Config
from utils.filters import register_filters, turkdate as turkdate_filter
from utils.decorators import admin_required
from utils.analytics import extract_review_insights_from_jobs as _extract_review_insights_from_jobs
from datetime import timedelta
import json
import os
import threading
import requests


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
    flask_app = Flask(__name__)
    flask_app.config.from_object(config_object)

    db.init_app(flask_app)
    login_manager.init_app(flask_app)
    register_filters(flask_app)

    return flask_app


# Modül seviyesinde tek örnek — worker.py `from app import app` ile bunu bekler.
app = create_app()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# =========================================================================
# CONTEXT PROCESSORS
# (Template filtreleri utils/filters.py'de, register_filters(app) ile bağlandı.)
# =========================================================================

@app.context_processor
def inject_global_data():
    if current_user.is_authenticated:
        unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
        return dict(unread_notifications=unread)
    return dict(unread_notifications=0)


# =========================================================================
# AUTH ROUTES
# =========================================================================
@app.route('/')
def index():
    # HOTFIX 2.00: Authenticated kullanıcı da landing'i görebilir.
    # Önceden zorla /dashboard'a redirect ediyorduk → kullanıcı vitrin/fiyatlandırma
    # sayfalarına erişemiyordu. Şimdi landing her durumda render edilir; nav
    # buton seti current_user.is_authenticated'a göre koşullu gösterilir.
    return render_template('landing.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if not user.is_approved and not user.is_admin:
                flash('Hesabınız henüz onaylanmadı. Lütfen yönetici onayını bekleyin.', 'warning')
                return render_template('login.html')
            if not user.is_active and not user.is_admin:
                flash('Hesabınız devre dışı bırakılmış. Lütfen yöneticiyle iletişime geçin.', 'error')
                return render_template('login.html')

            user.last_login = get_tr_now()
            log = UsageLog(user_id=user.id, action='login')
            db.session.add(log)
            db.session.commit()

            login_user(user, remember=True)

            if user.is_admin:
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('dashboard'))
        else:
            flash('Geçersiz e-posta veya şifre.', 'error')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        full_name = request.form.get('full_name', '').strip()
        company = request.form.get('company', '').strip()
        phone = request.form.get('phone', '').strip()

        errors = []
        if not email or '@' not in email:
            errors.append('Geçerli bir e-posta adresi girin.')
        if len(password) < 6:
            errors.append('Şifre en az 6 karakter olmalıdır.')
        if password != confirm:
            errors.append('Şifreler eşleşmiyor.')
        if not full_name:
            errors.append('Ad Soyad zorunludur.')
        if User.query.filter_by(email=email).first():
            errors.append('Bu e-posta adresi zaten kayıtlı.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('register.html')

        approval_mode = Setting.get('approval_mode', 'manual')
        auto_approve = (approval_mode == 'auto')

        trial_plan = Plan.query.filter_by(name='trial').first()
        trial_days = int(Setting.get('free_trial_days', '14'))

        user = User(
            email=email,
            full_name=full_name,
            company=company,
            phone=phone,
            is_active=auto_approve,
            is_approved=auto_approve,
            plan_id=trial_plan.id if trial_plan else None,
            trial_start=get_tr_now(),
            trial_days=trial_days
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        if auto_approve:
            flash('Kayıt başarılı! Giriş yapabilirsiniz.', 'success')
        else:
            flash('Kayıt başarılı! Hesabınız yönetici onayı bekliyor.', 'info')

        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Başarıyla çıkış yaptınız.', 'info')
    return redirect(url_for('login'))


# =========================================================================
# CUSTOMER ROUTES
# =========================================================================
@app.route('/dashboard')
@login_required
def dashboard():

    recent_jobs = Job.query.filter_by(user_id=current_user.id).order_by(Job.created_at.desc()).limit(50).all()
    total_jobs = Job.query.filter_by(user_id=current_user.id).count()
    completed_jobs = Job.query.filter_by(user_id=current_user.id, status='completed').count()

    # Vulnerability Radar: son 5 aktif uyarı (uyku modunda olabilir ama UI hâlâ dinleyebilir)
    vulnerability_alerts = VulnerabilityAlert.query.filter_by(
        user_id=current_user.id, is_active=True
    ).order_by(VulnerabilityAlert.created_at.desc()).limit(5).all()

    # ── FAZ 3.1: Finansal Komuta Merkezi metrikleri ─────────────────────────
    # 1) Tüm base ürünleri (maliyeti olanlar) çek
    # 2) Her base'in group_id'sindeki rakipleri (is_base_product=False) bul
    # 3) Rakiplerin min fiyatı maliyetin ÜSTÜNDEYSE → Kârlı Fırsat
    #    Rakiplerin min fiyatı maliyetin ALTINDAYSA → Zarar Riski
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
            # Aynı grupta base olmayan rakipler
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
            # min_comp == unit_cost → break-even, sayma
    except Exception as e:
        print(f"[Dashboard] Finansal metrik hesaplama hatası: {e}")

    # 3) Aktif Alarm sayısı (FAZ 2.1)
    try:
        active_alerts_count = PriceAlert.query.filter_by(
            user_id=current_user.id, is_active=True
        ).count()
    except Exception as e:
        print(f"[Dashboard] Aktif alarm sayım hatası: {e}")
        active_alerts_count = 0

    # 4) Son 5 Bildirim (timeline)
    try:
        recent_notifications = Notification.query.filter_by(
            user_id=current_user.id
        ).order_by(Notification.created_at.desc()).limit(5).all()
    except Exception as e:
        print(f"[Dashboard] Bildirim çekme hatası: {e}")
        recent_notifications = []

    return render_template('dashboard.html',
                           jobs=recent_jobs,
                           total_jobs=total_jobs,
                           completed_jobs=completed_jobs,
                           vulnerability_alerts=vulnerability_alerts,
                           # FAZ 3.1
                           profitable_count=profitable_count,
                           risk_count=risk_count,
                           active_alerts_count=active_alerts_count,
                           recent_notifications=recent_notifications)


@app.route('/ai-consultant', methods=['GET'])
@login_required
def ai_consultant():
    """HOTFIX 1.45 — Geçmiş rapor arşivi + grup filtresi için tüm raporlar ve
    takip grupları template'e iletilir.
    """
    all_reports = []
    tracked_groups = []
    if current_user.has_enterprise_access:
        all_reports = (
            AiReport.query
            .filter_by(user_id=current_user.id)
            .order_by(AiReport.created_at.desc())
            .all()
        )
        # HOTFIX 1.95: Kullanıcının aktif takip gruplarını çek — group_label ÖNCELİKLİ
        # Önceki davranış: sadece product_name (Trendyol uzun başlık) gösteriliyordu.
        # Yeni: önce BASE ürünün `group_label` (HOTFIX 1.87'de eklenen özel ad) kontrol
        # edilir; varsa onu kullan, yoksa product_name'e düş.
        from sqlalchemy import func as sqlfunc
        groups_raw = (
            db.session.query(
                TrackedProduct.group_id,
                sqlfunc.min(TrackedProduct.product_name).label('rep_name'),
                sqlfunc.count(TrackedProduct.id).label('cnt'),
                sqlfunc.min(TrackedProduct.created_at).label('first_created'),
            )
            .filter(
                TrackedProduct.user_id == current_user.id,
                TrackedProduct.is_active == True,
                TrackedProduct.group_id.isnot(None),
            )
            .group_by(TrackedProduct.group_id)
            .order_by(sqlfunc.min(TrackedProduct.created_at).desc())
            .all()
        )
        # Base ürünlerin group_label'larını topla (tek seferde lookup)
        gid_list = [g.group_id for g in groups_raw if g.group_id]
        base_labels = {}
        if gid_list:
            base_rows = TrackedProduct.query.filter(
                TrackedProduct.user_id == current_user.id,
                TrackedProduct.group_id.in_(gid_list),
                TrackedProduct.is_base_product == True,
                TrackedProduct.group_label.isnot(None),
            ).all()
            for b in base_rows:
                if b.group_label and b.group_label.strip():
                    base_labels[b.group_id] = b.group_label.strip()

        tracked_groups = []
        for g in groups_raw:
            # Öncelik: özel group_label → product_name → group_id kısaltma
            label = base_labels.get(g.group_id)
            if not label:
                label = (g.rep_name or '').strip() or (g.group_id[:12] + '…')
            tracked_groups.append({
                'id': g.group_id,
                'name': label[:70],
                'cnt': g.cnt,
            })

    latest_report = all_reports[0] if all_reports else None
    return render_template(
        'ai_consultant.html',
        latest_report=latest_report,
        all_reports=all_reports,
        tracked_groups=tracked_groups,
    )


# ── HOTFIX 1.74: Standalone Rapor Görüntüleyici ───────────────────────────────
# "Yeni Sekmede Aç" butonunun açtığı minimal, sidebar'sız tam ekran sayfa.
# Sadece rapor içeriği render edilir → maksimum okuma alanı.
# Kullanıcı sahibi olduğu raporları görür (user_id check); aksi 404.
@app.route('/ai-consultant/report/<int:report_id>')
@login_required
def ai_consultant_report_standalone(report_id):
    if not current_user.has_enterprise_access:
        flash('Bu özellik Kurumsal plana özeldir.', 'warning')
        return redirect(url_for('user_plans'))

    report = AiReport.query.filter_by(id=report_id, user_id=current_user.id).first()
    if not report:
        flash('Rapor bulunamadı.', 'error')
        return redirect(url_for('ai_consultant'))

    return render_template('ai_report_standalone.html', report=report)


# ── HOTFIX 1.94: YZ Strateji Raporu PDF Export — Client-Side (html2pdf.js) ──
# Önceki WeasyPrint çözümü sistem kütüphanesi (Pango/Cairo) gerektiriyordu →
# Production deploy yükünü azaltmak için PDF üretimi TARAYICIYA devredildi.
# Backend artık sadece:
#   1) Markdown → HTML çevirir
#   2) Kurumsal strategy_pdf.html şablonunu render edip HTML olarak döner
# Tarayıcı html2pdf.js CDN ile sayfa içeriğini A4 PDF'e çevirir.
@app.route('/analysis/<int:report_id>/download-pdf')
@login_required
def download_strategy_pdf(report_id):
    if not current_user.has_enterprise_access:
        flash('Bu özellik Kurumsal plana özeldir.', 'warning')
        return redirect(url_for('user_plans'))

    report = AiReport.query.filter_by(id=report_id, user_id=current_user.id).first()
    if not report:
        flash('Rapor bulunamadı.', 'error')
        return redirect(url_for('ai_consultant'))

    # Markdown → HTML (sunucu tarafı — server-side daha güvenli)
    try:
        import markdown as _md
        report_html = _md.markdown(
            report.content or '',
            extensions=['extra', 'sane_lists', 'nl2br', 'tables'],
        )
    except Exception:
        # markdown kütüphanesi yoksa düz metni paragraf wrap ile basla
        from markupsafe import escape
        raw = (report.content or '').strip()
        report_html = '<p>' + escape(raw).replace('\n\n', '</p><p>').replace('\n', '<br>') + '</p>'

    # Dosya adı (html2pdf.js'e iletilir, JS tarafında kullanılır)
    date_str = report.created_at.strftime('%Y%m%d-%H%M')
    slug_src = (report.group_name or 'genel-strateji').lower()
    import re as _re
    slug = _re.sub(r'[^a-z0-9]+', '-', slug_src).strip('-')[:40] or 'rapor'
    pdf_filename = f'BMK-Strateji-{slug}-{date_str}.pdf'

    # Şablonu render et + HTML olarak dön (kullanıcı tarayıcıda butona basıp PDF indirir)
    return render_template(
        'strategy_pdf.html',
        report=report,
        report_html=report_html,
        pdf_filename=pdf_filename,
    )



@app.route('/ai-consultant/generate', methods=['POST'])
@login_required
def generate_ai_consultant():
    """FAZ 3.2 / HOTFIX 1.45 — Veri Güdümlü YZ Strateji Danışmanı.

    HOTFIX 1.45: Formdan gelen group_id ile belirli bir grubu filtrele;
    custom_prompt varsa sistem promptuna zorunlu talimat olarak ekle.
    """
    if not current_user.has_enterprise_access:
        flash('Bu özellik Kurumsal plana özeldir.', 'warning')
        return redirect(url_for('ai_consultant'))

    # HOTFIX 1.45: Formdan grup filtresi ve özel prompt al
    selected_group_id = (request.form.get('group_id') or '').strip() or None
    custom_prompt_raw  = (request.form.get('custom_prompt') or '').strip()

    # ── 1) Veri Toplama (HOTFIX 1.8 — Legacy Data Uyumlu) ───────────────────
    # YENİ MANTIK: Kullanıcının tüm aktif ürünlerini group_id'ye göre grupla.
    #   • Her grupta unit_cost > 0 olan herhangi bir ürün → "BASE" olarak seç.
    #   • Aynı gruptaki diğer tüm ürünler → "RAKİP" olarak hesaba katılır.
    #   • group_id NULL olan ürünler "tek başına grup" sayılır (kendi id'si key).
    # HOTFIX 1.45: selected_group_id varsa SADECE o gruba ait ürünleri al.
    product_query = TrackedProduct.query.filter(
        TrackedProduct.user_id == current_user.id,
        TrackedProduct.is_active == True,
    )
    if selected_group_id:
        product_query = product_query.filter(TrackedProduct.group_id == selected_group_id)
    all_products = product_query.all()

    if not all_products:
        flash('Danışmanlık raporu üretebilmek için en az 1 ürün takip ediyor olmalısınız.', 'warning')
        return redirect(url_for('ai_consultant'))

    # Group by group_id (NULL → kendi id'si ile pseudo-group)
    groups = {}
    for p in all_products:
        gkey = p.group_id if p.group_id else f"_solo_{p.id}"
        groups.setdefault(gkey, []).append(p)

    # FAZ 5: Tüm portföy URL'leri için Kombine/Yorum analizi insight'larını TEK SORGUDA topla.
    # Her grupta base + rakipler için praises/complaints/general döner.
    all_urls = [p.url for p in all_products if p.url]
    review_insights = _extract_review_insights_from_jobs(current_user.id, all_urls)

    # Her grup için base seçimi: öncelik sırası
    #   1) unit_cost > 0 olan ürün (yeni öncelik — legacy uyumlu)
    #   2) is_base_product=True olan ürün (yeni şema)
    #   3) İlk eklenen ürün (created_at ASC)
    portfolio = []
    has_cost_data = False
    skipped_no_cost = 0

    for gkey, members in groups.items():
        members.sort(key=lambda x: x.created_at or get_tr_now())

        # Maliyetli adayları öncelikle al
        cost_candidates = [m for m in members if m.unit_cost and m.unit_cost > 0]
        if cost_candidates:
            base = cost_candidates[0]
        else:
            # Maliyet yoksa is_base_product işaretine düş, yoksa ilk üye
            flagged = [m for m in members if getattr(m, 'is_base_product', False)]
            base = flagged[0] if flagged else members[0]

        # Rakipler: gruptaki diğer TÜM ürünler (is_base_product bayrağına bakılmaz)
        competitors = [m for m in members if m.id != base.id and m.current_price and m.current_price > 0]

        comp_prices = [float(c.current_price) for c in competitors]
        min_comp_price = min(comp_prices) if comp_prices else None
        avg_comp_price = round(sum(comp_prices) / len(comp_prices), 2) if comp_prices else None

        # FAZ 4: Yorum/Puan istihbaratı — base + min-fiyatlı rakip + grup ortalaması
        base_rating = float(base.rating) if getattr(base, 'rating', None) else None
        base_review_count = int(base.review_count or 0)

        # En düşük fiyatlı rakibin puan/yorum verisi (kalite-fiyat çapraz analizi için kritik)
        min_comp_rating = None
        min_comp_review_count = 0
        if competitors:
            min_comp_obj = min(competitors, key=lambda c: c.current_price)
            min_comp_rating = float(min_comp_obj.rating) if getattr(min_comp_obj, 'rating', None) else None
            min_comp_review_count = int(min_comp_obj.review_count or 0)

        # Grup ortalama puan (rakipler arası)
        comp_ratings = [float(c.rating) for c in competitors if c.rating and c.rating > 0]
        avg_comp_rating = round(sum(comp_ratings) / len(comp_ratings), 2) if comp_ratings else None

        unit_cost = float(base.unit_cost) if base.unit_cost and base.unit_cost > 0 else None
        current_price = float(base.current_price) if base.current_price and base.current_price > 0 else None

        # Maliyeti olmayan grupları rapora dahil ETMİYORUZ — LLM rakam yazamaz.
        # Ama tek tek say, kullanıcıya bilgi verelim.
        if unit_cost is None:
            skipped_no_cost += 1
            continue

        has_cost_data = True

        # Türev metrikler — LLM'e elinden iş alır
        net_profit_now = (current_price - unit_cost) if (current_price and unit_cost) else None
        margin_pct_now = round((net_profit_now / current_price) * 100, 2) if (net_profit_now is not None and current_price) else None
        delta_vs_min_comp = round(current_price - min_comp_price, 2) if (current_price and min_comp_price) else None
        cost_vs_min_comp = round(min_comp_price - unit_cost, 2) if (unit_cost is not None and min_comp_price is not None) else None

        name = (base.product_name or base.url or 'Ürün')
        if len(name) > 110:
            name = name[:110] + '...'

        # FAZ 5: Kombine/Yorum analizinden gelen praise/complaint metinleri (varsa)
        my_insight = review_insights.get(base.url, {}) if base.url else {}
        comp_insight = {}
        if competitors:
            # Rakipler arası birleştirilmiş havuz: en sık geçen şikayetleri/övgüleri vermek için
            agg_praises, agg_complaints, agg_generals = [], [], []
            for c in competitors:
                ci = review_insights.get(c.url)
                if not ci:
                    continue
                agg_praises.extend(ci.get("praises", []))
                agg_complaints.extend(ci.get("complaints", []))
                if ci.get("general"):
                    agg_generals.append(ci["general"])
            # Kabaca dedup (kelime/kelime aynılarsa atla)
            def _dedup(items, limit=8):
                seen, out = set(), []
                for it in items:
                    k = it.lower().strip()[:80]
                    if k not in seen:
                        seen.add(k)
                        out.append(it)
                    if len(out) >= limit:
                        break
                return out
            comp_insight = {
                "praises": _dedup(agg_praises),
                "complaints": _dedup(agg_complaints),
                "general": " | ".join(agg_generals[:3])[:800] if agg_generals else "",
            }

        # ── EPIC 6.0 / HOTFIX 1.92: SEO (Görünürlük) İstihbaratı ──
        # Base + competitors URL'leri için en güncel KeywordPool sıralamasını çek.
        # Bir URL birden fazla keyword için izleniyor olabilir → en iyi sırayı
        # (overall_rank en küçük) seç. Pool yoksa KeywordTracker'a düş.
        def _seo_status_for_url(url):
            """Bir ürün URL'si için en iyi (en düşük overall_rank) SEO sırasını döndür.
            Format: {'page': int, 'rank': int, 'overall': int, 'keyword': str, 'status': 'aktif'|'yok'}
            'yok' = tarama yapılmamış veya bulunamamış.
            """
            if not url:
                return {'status': 'yok'}
            # Önce KeywordPool (paylaşımlı havuz) — en güncel + tüm kullanıcılar için
            pool_rows = KeywordPool.query.filter_by(target_url=url).all()
            best = None
            best_kw = None
            for pr in pool_rows:
                if (pr.current_page or 0) > 0 and (pr.current_rank or 0) > 0:
                    overall = (pr.current_page - 1) * 40 + pr.current_rank
                    if best is None or overall < best['overall']:
                        best = {'page': pr.current_page, 'rank': pr.current_rank,
                                'overall': overall, 'status': 'aktif'}
                        best_kw = pr.keyword
            # Pool yoksa kullanıcının KeywordTracker'larına bak (eski veri)
            if not best:
                kt_rows = KeywordTracker.query.filter_by(
                    user_id=current_user.id, target_url=url, is_active=True
                ).all()
                for kt in kt_rows:
                    if (kt.current_page or 0) > 0 and (kt.current_rank or 0) > 0:
                        overall = (kt.current_page - 1) * 40 + kt.current_rank
                        if best is None or overall < best['overall']:
                            best = {'page': kt.current_page, 'rank': kt.current_rank,
                                    'overall': overall, 'status': 'aktif'}
                            best_kw = kt.keyword
            if best:
                best['keyword'] = best_kw
                return best
            return {'status': 'yok'}

        my_seo = _seo_status_for_url(base.url)
        comp_seo = [{'url_short': (c.url or '')[-50:], 'seo': _seo_status_for_url(c.url)}
                    for c in competitors]

        portfolio.append({
            'urun_adi': name,
            'platform': base.platform_name or 'Bilinmiyor',
            'birim_maliyet_tl': unit_cost,
            'guncel_satis_fiyatim_tl': current_price,
            'rakip_sayisi': len(comp_prices),
            'min_rakip_fiyati_tl': min_comp_price,
            'ortalama_rakip_fiyati_tl': avg_comp_price,
            'simdiki_net_kar_tl': net_profit_now,
            'simdiki_kar_marjim_yuzde': margin_pct_now,
            'fiyat_farkim_min_rakipten_tl': delta_vs_min_comp,
            'min_rakip_eksi_maliyetim_tl': cost_vs_min_comp,
            # FAZ 4: Kalite (Yıldız Puanı + Yorum Sayısı) istihbaratı
            'benim_puanim': base_rating,
            'benim_yorum_sayim': base_review_count,
            'min_rakip_puani': min_comp_rating,
            'min_rakip_yorum_sayisi': min_comp_review_count,
            'rakip_ortalama_puani': avg_comp_rating,
            # FAZ 5: Kombine Analiz'den çekilen yorum içgörüleri
            'benim_basarili_yonlerim': my_insight.get("praises", []),
            'benim_kritik_sikayetlerim': my_insight.get("complaints", []),
            'benim_genel_kanim': my_insight.get("general", ""),
            'rakip_basarili_yonleri': comp_insight.get("praises", []),
            'rakip_kritik_sikayetleri': comp_insight.get("complaints", []),
            'rakip_genel_kanisi': comp_insight.get("general", ""),
            # ── EPIC 6.0: SEO Görünürlük İstihbaratı ──
            'benim_seo_durumum': my_seo,            # {'page','rank','overall','keyword','status'}
            'rakip_seo_durumlari': comp_seo,        # liste: [{'url_short','seo':{...}}, ...]
        })

    if not has_cost_data:
        flash('YZ Danışman, anlamlı tavsiye üretebilmek için en az bir ürünün BİRİM MALİYET (unit_cost) bilgisine ihtiyaç duyar. Lütfen Fiyat Takibi sayfasından ürün kartındaki 💰 butonu ile maliyet ekleyin.', 'warning')
        return redirect(url_for('ai_consultant'))

    if skipped_no_cost > 0:
        flash(f'ℹ️ Maliyet bilgisi eksik {skipped_no_cost} ürün/grup rapora dahil edilmedi. Bu ürünlere maliyet eklerseniz bir sonraki raporda analiz edilirler.', 'info')

    # ── 2) API anahtar kontrolü ──────────────────────────────────────────────
    # HOTFIX 1.44: DB yoksa .env'den oku (GROQ_API_KEY ortam değişkeni)
    api_key = Setting.get('groq_api_key', '') or os.environ.get('GROQ_API_KEY', '')
    if not api_key:
        flash('Sistemde GROQ API anahtarı tanımlı değil. Lütfen yöneticiyle iletişime geçin.', 'error')
        return redirect(url_for('ai_consultant'))

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # ── 3) Sektör tespiti (kısa & ucuz çağrı) ────────────────────────────────
    sector = "Genel E-Ticaret"
    try:
        names_only = [{'name': p['urun_adi'], 'platform': p['platform']} for p in portfolio]
        sec_payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": "Sana verilecek ürün listesine bakarak bu ürünlerin ait olduğu ana e-ticaret sektörünü ve alt kategorisini tespit et. Sadece 2-5 kelime ile yaz. (Örn: Evcil Hayvan Bakım Ürünleri, Küçük Ev Aletleri, Spor Giyim ve Aksesuar)"},
                {"role": "user", "content": json.dumps(names_only, ensure_ascii=False)}
            ],
            "temperature": 0.2
        }
        resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=sec_payload, timeout=15)
        if resp.status_code == 200:
            sector = resp.json()['choices'][0]['message']['content'].strip()
    except Exception:
        pass

    # ── 4) HOTFIX 1.12 — "CEO Danışmanı" Persona Promptu ────────────────────────
    # Negatif/uzun kurallar yerine PERSONA + ŞABLON + KISA TALİMAT.
    # HOTFIX 1.45: custom_prompt varsa sistem promptuna zorunlu talimat olarak ekleniyor.
    system_prompt = (
        "Sen, üst düzey yöneticilere e-ticaret stratejisi sunan milyarlık bir danışmanlık şirketinin "
        "baş analistisin. Müşteriye asla 'şunu analiz ediyorum', 'talimatlara göre yazıyorum' gibi "
        "kendi sürecinden bahsetme. Doğrudan profesyonel, net ve vizyoner tavsiyeler ver. "
        "Soruları veya yönlendirme metinlerini rapora KESİNLİKLE kopyalama.\n\n"
        # ── HOTFIX 1.73: Görsel Hiyerarşi (Bold + Bullet Points) ──
        "✍️ FORMATLAMA KURALLARI (Markdown):\n"
        "• Önemli stratejik hamleleri, anahtar kelimeleri, fiyatları ve sayısal verileri "
        "**kalın (bold)** yaz. Örn: **premium konumlandırma**, **499 ₺**, **%18 kâr marjı**.\n"
        "• Aksiyon planlarını UZUN PARAGRAFLAR yerine kısa, net ve vurucu MADDE İŞARETLERİ "
        "(`-` veya `•`) ile sun. Her madde tek bir net eylem içermeli, en fazla 2 satır.\n"
        "• Paragraflar arasında boşluk bırak; metin duvarı oluşturma.\n"
        "• Başlık emojilerini ve numaralarını koru (🌍 1., ⚖️ 2., vb.).\n\n"
        f"Tespit edilen sektör/niş: {sector}\n\n"
        "Raporu ŞU KESİN FORMATTA ve ŞU TONDA yazmalısın (Markdown kullan):\n\n"
        "🌍 1. Pazar ve Niş Değerlendirmesi\n"
        "[Bu ürünün hitap ettiği kitleyi ve pazarın genel durumunu akıcı, tek bir profesyonel paragrafla anlat. "
        "Anahtar pazar göstergelerini **bold** yap.]\n\n"
        "⚖️ 2. Rekabet ve Ürün Analizi\n"
        "[Kullanıcının ürünü ile rakipleri arasındaki fiyat ve kalite farkını analiz et. Sana gönderilen "
        "rakip kritik şikayetleri veya kullanıcının kritik şikayet metinlerini DÜZ METİN olarak alıntıla. "
        "Örneğin: 'Rakibinizde **şarjın çabuk bitmesi** şikayeti var, bunu fırsata çevirin'. "
        "Eğer puan/yorum verisi yoksa sadece 'Henüz yeterli sosyal kanıt/yorum verisi oluşmamış' de. "
        "Asla json değişkeni, Rusça veya kod kullanma.]\n\n"
        "💡 3. Ürün Geliştirme ve Farklılaşma\n"
        "[Fiyat kırmak yerine ürüne nasıl değer katılacağını doğrudan eylem adımları olarak MADDE İŞARETLERİYLE yaz. "
        "Örn: '- Kutuya **yedek aparat** ekleyerek premium algısı yaratın.' "
        "Asla 'Pakete ne eklenebilir?' gibi soru cümleleri kullanma.]\n\n"
        "💰 4. Aksiyon ve Fiyatlandırma Stratejisi\n"
        "[Maliyet ve fiyat durumuna göre net stratejiyi yaz. KURALLAR: en düşük rakip fiyatı kullanıcının "
        "birim maliyetinin altındaysa KESİNLİKLE sayısal fiyat kırmayı önerme. 'Rakipler **maliyetinize "
        "inmiş**, premium konumlandırmada kalın' de. Önerdiğin hiçbir fiyat birim maliyetin altında olamaz. "
        "Tüm fiyat önerilerini **bold** olarak ver.]\n\n"
        # ═══════════════════════════════════════════════════════════════════
        # EPIC 6.0 / HOTFIX 1.92: STRATEJİK E-TİCARET KURALLARI (SEO + FİYAT)
        # ═══════════════════════════════════════════════════════════════════
        "🔍 5. Görünürlük (SEO) Stratejisi — KESİN KURALLAR\n"
        "[Aşağıdaki 3 kuralı UYGULAMAK ZORUNDASIN. Veri setindeki `benim_seo_durumum` ve "
        "`rakip_seo_durumlari` alanlarını analiz et — `status: 'yok'` ise 'SEO verisi yok' "
        "olarak belirt ve genel SEO tavsiyesi ver.]\n\n"
        "**🚨 KURAL 1 — GÖRÜNMEZLİK TUZAĞI (En Kritik):**\n"
        "Eğer kullanıcının fiyatı rakiplerden DÜŞÜK veya EŞİT ama SEO sırası KÖTÜ "
        "(`overall_rank > 40` yani 2. sayfa ve sonrası) ise: **ASLA fiyatı daha da "
        "düşürmesini önerme.** Şu mesajı ver: '**Kâr marjınızı feda etmeyin.** Ürününüz "
        "ucuz ama müşteri sizi göremiyor — sorun fiyat değil, **görünürlük**. Fiyat kırmak "
        "yerine **(a)** Trendyol/Hepsiburada PPM reklamı açın, **(b)** ürün başlığını "
        "anahtar kelime odaklı yeniden yazın, **(c)** ürün açıklamasına SEO odaklı uzun "
        "kuyruklu kelimeler ekleyin.'\n\n"
        "**🏆 KURAL 2 — LİDERLİK FIRSATI:**\n"
        "Eğer SEO sırası ÇOK İYİ (`overall_rank <= 5` yani 1. sayfa ilk 5) VE fiyat "
        "rakiplerden BELİRGİN UCUZ ise: '**Buybox ve organik görünürlük liderisiniz.** "
        "Satış hızınızı kaybetmeden fiyatı **%2-5 oranında** kademeli artırarak kârlılığınızı "
        "maksimize etmeyi test edin. Mevcut fiyatınız: **X ₺** → Hedef: **Y ₺**. Satış "
        "hızı düşerse derhal geri alın.'\n\n"
        "**⚖️ KURAL 3 — BÜTÜNSEL ANALİZ (Her raporda zorunlu):**\n"
        "Her raporun bu bölümünde 3 ekseni harmanlayarak değerlendir:\n"
        "  - **Fiyat Avantajı:** rakiplerle delta (₺ + %)\n"
        "  - **Müşteri Algısı (Yorumlar):** rating + complaints analizi\n"
        "  - **Görünürlük (SEO):** mevcut sıra + rakip sıraları karşılaştırması\n"
        "Sonuç: tek bir kurumsal cümle ile bağla. Örn: '**Fiyatınız rekabetçi**, **yorum "
        "puanınız üst seviyede** (4.7★) ancak **2. sayfa 14. sıradasınız** — sorun ürün "
        "değil **keşfedilebilirlik**. Stratejik öncelik: SEO + PPM.'\n\n"
        "📋 Öncelikli Aksiyon Planı\n"
        "[En acil 3-5 aksiyonu MADDE İŞARETLERİYLE (`-`), emir kipleriyle sırala. "
        "Yukarıdaki 3 SEO kuralı ihlal edilmemeli — örneğin Kural 1 senaryosunda 'fiyat "
        "düşür' aksiyonu YAZILAMAZ. "
        "Örn: '- **Listing başlıklarını** SEO odaklı optimize edin'.]\n"
    )
    # ── HOTFIX 1.73: Özel İstek Tekrar Önleme ──────────────────────────────────
    # Önceki davranış (1.45): "Özel istek için raporda ayrı bir bölüm oluşturabilirsin"
    # → Model raporun sonuna "🎯 Sorunuza Cevap" gibi ayrı bir bölüm yapıyor, kullanıcı
    # input'unu tekrar tekrar yazıyordu → kötü okuma deneyimi.
    # Yeni davranış: Kullanıcının sorusu/odağı raporun BİLEŞENİ olur, ayrı bölüm olmaz;
    # cevap "Aksiyon ve Fiyatlandırma Stratejisi" veya "Öncelikli Aksiyon Planı"
    # içinde organik olarak yedirilir. Soru hiç tekrar yazılmaz.
    if custom_prompt_raw:
        system_prompt += (
            f"\n\n🎯 KULLANICININ ÖZEL ODAK KONUSU: \"{custom_prompt_raw}\"\n"
            "KESİN KURALLAR (ihlal etme):\n"
            "1) Bu özel soruyu/odak konusunu raporun HİÇBİR YERİNDE TEKRAR YAZMA. "
            "   Soru-cevap formatında ek bölüm OLUŞTURMA. "
            "   'Sorunuz şuydu...', 'Özel isteğinize gelince...' gibi giriş cümleleri KULLANMA.\n"
            "2) Bu konunun cevabını '💰 4. Aksiyon ve Fiyatlandırma Stratejisi' "
            "   ve/veya '📋 Öncelikli Aksiyon Planı' bölümlerinin İÇİNE organik olarak "
            "   yedirerek ver — sanki ilk baştan bu konuya odaklanmışsın gibi.\n"
            "3) Cevabın bu bölümlerde belirgin olsun: ilgili önerileri **kalın** vurgula, "
            "   örneklerle destekle, ama 'özel istek' kelimesini ASLA kullanma.\n"
            "4) Raporun SONUNA bu konuyla ilgili ekstra paragraf veya bölüm EKLEME."
        )

    user_payload_text = (
        "Müşterinin ürün portföyü (ürün isimleri + KALİTE [puan/yorum] verileri DAHİL):\n\n"
        + json.dumps(portfolio, ensure_ascii=False, indent=2)
        + "\n\nNotlar:\n"
        + "• `urun_adi` ürünün GERÇEK ismidir; kategori/niş çıkarımını bundan yap.\n"
        + "• `min_rakip_eksi_maliyetim_tl` NEGATİFSE → rakipler maliyetinin altında satıyor (ZARAR RİSKİ; fiyat kırma!).\n"
        + "• `rakip_sayisi` 0 ise grupta rakip yok, fiyat referansı yalnızca senin fiyatın.\n"
        + "• `benim_puanim` ve `min_rakip_puani` 1-5 arası yıldız puanıdır (None = puan verisi yok, atla).\n"
        + "• `benim_yorum_sayim` ve `min_rakip_yorum_sayisi` o ürüne yapılmış yorum sayılarıdır.\n"
        + "  → Bölüm 2'deki KALİTE-FİYAT ÇAPRAZ ANALİZİ kuralını bu iki alanı kullanarak uygula.\n"
        + "• `benim_basarili_yonlerim` / `rakip_basarili_yonleri`: Kombine Analiz raporlarından "
        + "çekilmiş, müşteri yorumlarında sıkça geçen ÖVGÜ noktaları (liste).\n"
        + "• `benim_kritik_sikayetlerim` / `rakip_kritik_sikayetleri`: Aynı kaynaktan çekilmiş "
        + "ŞİKAYET noktaları (liste). Bölüm 2 (kalite analizi) ve Bölüm 3 (ürün geliştirme) "
        + "için BİZZAT alıntı yaparak kullan; tahmin değil veri konuş.\n"
        + "• `benim_genel_kanim` / `rakip_genel_kanisi`: Müşteri/rakip yorumlarının genel sentiment özeti.\n"
        + "• Yorum içgörü listesi BOŞSA → KURAL C uyarınca 'henüz yeterli müşteri değerlendirmesi "
        + "alınmamış' diyerek devam et; 'veri yok' deme.\n"
        # ── EPIC 6.0 / HOTFIX 1.92: SEO (Görünürlük) Notları ──
        + "• `benim_seo_durumum`: ürünün Trendyol/Hepsiburada aramasındaki en güncel sırası.\n"
        + "  Format: {'status':'aktif','page':<int>,'rank':<int>,'overall':<int>,'keyword':<str>}\n"
        + "  → `status='yok'` ise henüz SEO taraması yapılmamış demek; 'SEO verisi yok' yaz, "
        + "    fiyat ve yorum üzerinden değerlendirmeye devam et.\n"
        + "  → `overall = (page-1)*40 + rank` — Trendyol bir sayfada ~40 ürün gösterir.\n"
        + "  → `overall <= 5` = 1. sayfa ilk 5 (mükemmel görünürlük)\n"
        + "  → `overall <= 40` = 1. sayfa (iyi)\n"
        + "  → `overall > 40` = 2. sayfa ve sonrası (görünmezlik tuzağı riski)\n"
        + "• `rakip_seo_durumlari`: rakip ürünlerin SEO sıraları (liste).\n"
        + "  → Bunu kullanarak Bölüm 5 'Görünürlük Stratejisi'nde KESİN kural 1-2-3'ü uygula.\n"
        + "  → Eğer rakipler daha iyi sıradaysa ve sen 2+ sayfadaysan: KURAL 1 (Görünmezlik Tuzağı) "
        + "    devreye girer — FİYAT DÜŞÜRME ÖNERME, SEO+PPM stratejisi ver.\n"
        + "  → Eğer sen ilk 5'tesin ve fiyatın rakiplerden ucuzsa: KURAL 2 (Liderlik Fırsatı) — "
        + "    %2-5 fiyat artışı önerebilirsin.\n"
    )

    # ── 5) LLM çağrısı + raporu kaydet ───────────────────────────────────────
    try:
        rep_payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload_text}
            ],
            "temperature": 0.45,  # HOTFIX 1.10: rakamsal halüsinasyonu kısmak için 0.55 → 0.45.
                                  # İlk 3 bölüm hâlâ akıcı, ama 4. bölümdeki matematiksel kurallara uyumu sıkı.
            "max_tokens": 4500
        }
        resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=rep_payload, timeout=90)
        if resp.status_code == 200:
            content = resp.json()['choices'][0]['message']['content'].strip()

            # Rapor başına portföy özetini Markdown olarak ekle (şeffaflık)
            portfolio_summary_md = "## 📊 Analiz Edilen Portföy Özeti\n\n"
            portfolio_summary_md += "| Ürün | Maliyet | Fiyatım | Min Rakip | Net Kâr | Puanım | Min Rakip Puanı |\n"
            portfolio_summary_md += "|---|---|---|---|---|---|---|\n"
            # FAZ 5: Robotik "—" yerine kullanıcıya açıklayıcı placeholder
            DASH = "—"
            NOT_ANALYZED = "_Analiz Edilmedi_"
            for p in portfolio:
                cost_s = f"{p['birim_maliyet_tl']:.2f} ₺" if p['birim_maliyet_tl'] is not None else DASH
                price_s = f"{p['guncel_satis_fiyatim_tl']:.2f} ₺" if p['guncel_satis_fiyatim_tl'] is not None else DASH
                comp_s = f"{p['min_rakip_fiyati_tl']:.2f} ₺" if p['min_rakip_fiyati_tl'] is not None else DASH
                profit_s = f"{p['simdiki_net_kar_tl']:.2f} ₺" if p['simdiki_net_kar_tl'] is not None else DASH
                # FAZ 5: Puan yoksa "Analiz Edilmedi" — Kombine Analiz raporu yönlendirmesi sağlanır
                if p.get('benim_puanim'):
                    my_rate_s = f"⭐ {p['benim_puanim']:.1f} ({p['benim_yorum_sayim']} yorum)"
                elif p.get('benim_basarili_yonlerim') or p.get('benim_kritik_sikayetlerim'):
                    # Yorum analizi var ama yıldız çekilmemiş → metin verisi var, yıldız yok
                    my_rate_s = "📝 Yorum analizi mevcut"
                else:
                    my_rate_s = NOT_ANALYZED

                if p.get('min_rakip_puani'):
                    cmp_rate_s = f"⭐ {p['min_rakip_puani']:.1f} ({p['min_rakip_yorum_sayisi']} yorum)"
                elif p.get('rakip_basarili_yonleri') or p.get('rakip_kritik_sikayetleri'):
                    cmp_rate_s = "📝 Yorum analizi mevcut"
                else:
                    cmp_rate_s = NOT_ANALYZED

                short = (p['urun_adi'][:50] + '…') if len(p['urun_adi']) > 50 else p['urun_adi']
                portfolio_summary_md += f"| {short} | {cost_s} | {price_s} | {comp_s} | {profit_s} | {my_rate_s} | {cmp_rate_s} |\n"
            portfolio_summary_md += "\n---\n\n"

            full_content = portfolio_summary_md + content

            # HOTFIX 1.45: grup adını türet (portföydeki ilk ürün ismi)
            derived_group_name = None
            if selected_group_id and portfolio:
                raw_name = portfolio[0].get('urun_adi', '')
                derived_group_name = (raw_name[:70] + '…') if len(raw_name) > 70 else raw_name

            new_report = AiReport(
                user_id=current_user.id,
                sector=sector,
                content=full_content,
                group_id=selected_group_id,
                group_name=derived_group_name,
                custom_prompt=custom_prompt_raw or None,
            )
            db.session.add(new_report)
            db.session.commit()
            scope_label = derived_group_name or 'Tüm Portföy'
            flash(f'✅ YZ raporu üretildi — {scope_label} / {len(portfolio)} ürün analiz edildi.', 'success')
        else:
            flash(f'Rapor üretilirken API hatası: {resp.status_code}', 'error')
    except Exception as e:
        flash(f'Rapor üretilemedi: {str(e)}', 'error')

    return redirect(url_for('ai_consultant'))


# =========================================================================
# FAZ 4 — SEO / Arama Sırası Takibi (Keyword Tracker)
# =========================================================================
@app.route('/seo-tracker', methods=['GET', 'POST'])
@login_required
def seo_tracker():
    """SEO Takibi: Trendyol arama sonuçlarında ürün konumu izleme."""
    if request.method == 'POST':
        keyword = (request.form.get('keyword') or '').strip()
        target_url = (request.form.get('target_url') or '').strip()
        # HOTFIX 1.90: Opsiyonel grup_label — seo_graph hızlı ekleme modalı için
        group_label_raw = (request.form.get('group_label') or '').strip()
        if len(group_label_raw) > 100:
            group_label_raw = group_label_raw[:100]
        group_label = group_label_raw or None
        # 'return_to' hidden input — POST geldiği sayfaya geri dön
        return_to = (request.form.get('return_to') or '').strip()

        # HOTFIX 1.15: Platform formdan geliyor; whitelist ile sertleştir.
        # HOTFIX 1.23: Hepsiburada SEO Takibi geçici olarak bakımda — sunucu tarafı savunma.
        raw_platform = (request.form.get('platform') or 'Trendyol').strip().lower()
        if raw_platform.startswith('hep'):
            flash('🛠️ Hepsiburada SEO Takibi şu an bakımdadır. Trendyol takibini kullanabilirsiniz.', 'warning')
            return redirect(url_for(return_to or 'seo_tracker'))
        platform = 'Trendyol'

        if not keyword or len(keyword) < 2:
            flash('⚠️ Lütfen geçerli bir arama kelimesi girin.', 'warning')
            return redirect(url_for(return_to or 'seo_tracker'))
        if not target_url.startswith('http'):
            flash('⚠️ Hedef URL geçerli bir ürün linki olmalı.', 'warning')
            return redirect(url_for(return_to or 'seo_tracker'))

        # Platform-URL tutarlılığı kontrolü
        _u = target_url.lower()
        if platform == 'Trendyol' and 'trendyol.com' not in _u:
            flash('⚠️ "Trendyol" platformu seçildi ama URL Trendyol ürün linki değil.', 'warning')
            return redirect(url_for(return_to or 'seo_tracker'))

        # Aynı kelime + URL kombinasyonu zaten varsa, yenisini ekleme
        exists = KeywordTracker.query.filter_by(
            user_id=current_user.id, keyword=keyword, target_url=target_url
        ).first()
        if exists:
            exists.is_active = True
            if group_label:
                # Kullanıcı yeni grup adı verdiyse güncelle
                exists.group_id = exists.group_id or f"solo-{exists.id}"
                # Grup adını base ürün'e yaz — burada KeywordTracker'da group_label
                # yok; TrackedProduct'da var. Kavramsal eşleşme için ileride
                # eklenebilir. Şimdilik no-op.
            db.session.commit()
            flash('🔁 Bu kelime + ürün takibi zaten kayıtlı, yeniden aktif edildi.', 'info')
        else:
            # HOTFIX 1.90: Bireysel tekil takip — group_label varsa solo group_id atanır
            import uuid
            new_gid = f"solo-{uuid.uuid4().hex[:12]}" if group_label else None
            kt = KeywordTracker(
                user_id=current_user.id, platform=platform,
                keyword=keyword, target_url=target_url, is_active=True,
                group_id=new_gid,
            )
            db.session.add(kt)
            db.session.flush()
            # HOTFIX 1.91: KeywordPool havuzuna bağla
            try:
                attach_keyword_tracker_to_pool(kt)
            except Exception as _e:
                print(f"[SEO tracker attach_pool] {_e}")
            db.session.commit()

            # Bağlı grup ürünü için group_label yaz — yeni "solo" TrackedProduct yarat
            # (eğer URL fiyat takibinde mevcutsa onun group_label'ı kullanılır;
            # değilse hafif bir "yer tutucu" TrackedProduct kayıt edilir).
            if group_label and new_gid:
                try:
                    base = TrackedProduct.query.filter_by(
                        user_id=current_user.id, url=target_url
                    ).first()
                    if base:
                        # Mevcut grup içine eklendiyse — direkt group_label yaz (eski varsa override etme)
                        if not base.group_label:
                            base.group_label = group_label
                            db.session.commit()
                    else:
                        # SEO için minimal placeholder — fiyat takibi DEĞİL (is_price_tracked=False)
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
                except Exception as _e:
                    print(f"[SEO group_label persist] {_e}")
                    db.session.rollback()

            flash('✅ Arama sırası takibi eklendi. İlk kontrol birkaç dakika içinde tamamlanır.', 'success')

            # HOTFIX 1.89: Anlık ilk kontrol — Celery worker'a async ID listesi at
            try:
                from worker import check_keyword_trackers_task
                check_keyword_trackers_task.delay([kt.id])
            except Exception as e:
                print(f"[SEO] İlk kontrol tetiklenemedi: {e}")
                try:
                    from worker import check_keyword_trackers
                    check_keyword_trackers(app, tracker_ids=[kt.id])
                except Exception:
                    pass

        return redirect(url_for(return_to or 'seo_tracker'))

    # GET — HOTFIX 1.90: Grup bazlı listeleme
    trackers = (KeywordTracker.query
                .filter_by(user_id=current_user.id)
                .order_by(KeywordTracker.created_at.desc())
                .all())

    # Grup_id'ye göre kümele: dict{group_label_or_None: [kt, ...]}
    # group_label çözünürlüğü için TrackedProduct base'leri ile join
    grouped_seo = {}
    for kt in trackers:
        gid = kt.group_id or '__bireysel__'
        grouped_seo.setdefault(gid, []).append(kt)

    # Grup etiketleri: TrackedProduct.base.group_label > '__bireysel__' → 'Bireysel Aramalar'
    group_seo_labels = {}
    for gid in grouped_seo.keys():
        if gid == '__bireysel__':
            group_seo_labels[gid] = 'Bireysel Aramalar'
            continue
        base = TrackedProduct.query.filter_by(
            user_id=current_user.id, group_id=gid, is_base_product=True
        ).first()
        if base and base.group_label:
            group_seo_labels[gid] = base.group_label
        elif base and base.product_name:
            group_seo_labels[gid] = base.product_name[:60]
        else:
            group_seo_labels[gid] = f'Grup {gid[:14]}'

    return render_template(
        'seo_tracker.html',
        trackers=trackers,
        grouped_seo=grouped_seo,
        group_seo_labels=group_seo_labels,
    )


@app.route('/seo-tracker/<int:tracker_id>/delete', methods=['POST'])
@login_required
def seo_tracker_delete(tracker_id):
    kt = KeywordTracker.query.filter_by(id=tracker_id, user_id=current_user.id).first()
    if not kt:
        flash('Kayıt bulunamadı.', 'warning')
        return redirect(url_for('seo_tracker'))
    # HOTFIX 1.91: Pool soft delete
    try:
        detach_keyword_tracker_from_pool(kt)
    except Exception as _e:
        print(f"[seo_tracker_delete detach_pool] {_e}")
    db.session.delete(kt)
    db.session.commit()
    flash('🗑️ Arama takibi silindi.', 'success')
    return redirect(url_for('seo_tracker'))


# ── EPIC 8.1 / HOTFIX 1.98: Dinamik YZ SEO İpuçları (Contextual Tips) ──────
# Kullanıcı "?" butonuna tıklayınca AJAX ile bu endpoint'e gelir.
# YZ (Groq llama-3.3-70b) ile keyword + ürün URL bağlamında özel öneri üretir.
# JSON döner: {"diagnosis": "...", "suggestions": [...], "relevance": "good/weak/off"}
@app.route('/api/generate-seo-tips/<int:tracker_id>', methods=['GET', 'POST'])
@login_required
def api_generate_seo_tips(tracker_id):
    kt = KeywordTracker.query.filter_by(id=tracker_id, user_id=current_user.id).first()
    if not kt:
        return jsonify({'success': False, 'error': 'Takip kaydı bulunamadı.'}), 404

    keyword = kt.keyword or ''
    target_url = kt.target_url or ''

    # Ürün adı tespiti — sırayla:
    # 1) URL'deki slug'tan parse (Trendyol/Hepsiburada slug standardı)
    # 2) Bağlı TrackedProduct.product_name
    # 3) Bağlı GlobalProduct.product_name
    # 4) URL'in kendisi (fallback)
    product_name = ''
    try:
        import re as _re
        if 'trendyol.com' in target_url.lower():
            # Trendyol: /marka-urun-adi-p-XXXX → "marka urun adi"
            m = _re.search(r'/([^/]+)-p-\d+', target_url)
            if m:
                product_name = m.group(1).replace('-', ' ').strip()
        elif 'hepsiburada.com' in target_url.lower():
            # Hepsiburada: /marka-urun-adi-p-HBV0000XXXX → benzer
            m = _re.search(r'/([^/]+)-p-[A-Za-z0-9]+', target_url)
            if m:
                product_name = m.group(1).replace('-', ' ').strip()
    except Exception:
        pass

    if not product_name:
        # DB'den ürün adına düş
        tp = TrackedProduct.query.filter_by(
            user_id=current_user.id, url=target_url
        ).first()
        if tp and tp.product_name:
            product_name = tp.product_name
        else:
            try:
                from models import GlobalProduct
                gp = GlobalProduct.query.filter_by(url=target_url).first()
                if gp and gp.product_name:
                    product_name = gp.product_name
            except Exception:
                pass

    if not product_name:
        product_name = target_url[:80]

    # ── Groq LLM çağrısı ──
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
        # Olası kod-bloku temizliği
        if raw.startswith('```'):
            raw = raw.strip('`')
            if raw.lower().startswith('json'):
                raw = raw[4:].strip()

        import json as _json
        try:
            data = _json.loads(raw)
        except Exception:
            # JSON parse başarısız → ham metni diagnosis'e koy, suggestion boş
            return jsonify({
                'success': True,
                'relevance': 'weak',
                'diagnosis': raw[:400] if raw else 'YZ yanıtı yorumlanamadı. Lütfen tekrar deneyin.',
                'suggestions': [],
                'context': {
                    'keyword': keyword,
                    'product_name': product_name[:120],
                },
            })

        # Şema doğrulama
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
            'context': {
                'keyword': keyword,
                'product_name': product_name[:120],
            },
        })

    except ImportError:
        return jsonify({
            'success': False,
            'error': 'YZ kütüphanesi sunucuda kurulu değil.',
        }), 500
    except Exception as e:
        print(f"[SEO Tips API] Hata: {e}")
        return jsonify({
            'success': False,
            'error': f'YZ analizi yapılamadı: {str(e)[:150]}',
        }), 500


@app.route('/seo-tracker/<int:tracker_id>/refresh', methods=['POST'])
@login_required
def seo_tracker_refresh(tracker_id):
    """Tek bir kelime takibini anında yeniden kontrol et."""
    kt = KeywordTracker.query.filter_by(id=tracker_id, user_id=current_user.id).first()
    if not kt:
        flash('Kayıt bulunamadı.', 'warning')
        return redirect(url_for('seo_tracker'))
    # HOTFIX 1.89: Async Celery tetikleyici — web isteği bloklanmaz
    try:
        from worker import check_keyword_trackers_task
        check_keyword_trackers_task.delay([kt.id])
        flash('🔄 Tarama kuyruğa alındı — birkaç saniye içinde güncellenecek.', 'success')
    except Exception as e:
        # Fallback: senkron (Celery erişilemezse)
        try:
            from worker import check_keyword_trackers
            check_keyword_trackers(app, tracker_ids=[kt.id])
            flash('🔄 Kontrol tamamlandı.', 'success')
        except Exception as e2:
            flash(f'Kontrol hatası: {e2}', 'error')
    return redirect(url_for('seo_tracker'))


# ── HOTFIX 1.84: Grup Bazlı Toplu SEO Başlatma ────────────────────────────
# Bir fiyat takip grubundaki tüm ürünler için, verilen tek bir anahtar
# kelimeyle KeywordTracker kayıtları toplu olarak oluşturulur. Tüm tracker'lar
# `group_id` ile etiketlenir → SEO Grafik sayfasında grup bazlı kümelenir.
# Idempotent: aynı grup + aynı keyword + aynı URL kombinasyonu varsa atlanır.
@app.route('/tracked-products/group/<group_id>/start-seo', methods=['POST'])
@login_required
def start_group_seo(group_id):
    keyword = (request.form.get('keyword') or '').strip()
    if not keyword:
        flash('Anahtar kelime gereklidir.', 'danger')
        return redirect(url_for('tracked_products'))
    if len(keyword) > 200:
        keyword = keyword[:200]

    # Grubun sahibi kullanıcı mı?
    products = TrackedProduct.query.filter_by(
        user_id=current_user.id, group_id=group_id, is_active=True
    ).all()
    if not products:
        flash('Grup bulunamadı veya ürün yok.', 'warning')
        return redirect(url_for('tracked_products'))

    added = 0
    skipped = 0
    new_ids = []
    for p in products:
        url = p.url or ''
        if not url:
            continue
        # Idempotency: aynı grup+keyword+url varsa atla
        existing = KeywordTracker.query.filter_by(
            user_id=current_user.id,
            group_id=group_id,
            keyword=keyword,
            target_url=url,
        ).first()
        if existing:
            skipped += 1
            continue
        # Platform tespiti URL'den
        plat = 'Trendyol' if 'trendyol.com' in url.lower() else (
               'Hepsiburada' if 'hepsiburada.com' in url.lower() else 'Trendyol')
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
        # HOTFIX 1.91: KeywordPool havuzuna bağla
        try:
            attach_keyword_tracker_to_pool(kt)
        except Exception as _e:
            print(f"[start_group_seo attach_pool] {_e}")
        new_ids.append(kt.id)
        added += 1

    db.session.commit()

    # HOTFIX 1.89: Anında ilk tarama — Celery'ye async at
    # (web isteği bloklanmaz; kullanıcı POST sonrası SEO Grafik sayfasına yönlenir,
    #  birkaç saniye sonra sayfa yenilenince ilk veri grafikte görünür).
    if new_ids:
        try:
            from worker import check_keyword_trackers_task
            check_keyword_trackers_task.delay(new_ids)
        except Exception as e:
            print(f"[SEO group-start] Celery tetikleme hatası: {e}")
            # Fallback: senkron çağrı
            try:
                from worker import check_keyword_trackers
                check_keyword_trackers(app, tracker_ids=new_ids)
            except Exception as e2:
                print(f"[SEO group-start] senkron fallback de hatası: {e2}")

    flash(f'🔍 SEO takibi başlatıldı: {added} yeni, {skipped} atlandı (zaten var).', 'success')
    return redirect(url_for('seo_graph'))


# ── HOTFIX 1.85: Grup SEO Takibini Toplu Sil ──────────────────────────────
# Bir fiyat takip grubuna bağlı tüm KeywordTracker kayıtlarını siler.
# SEOHistory FK cascade ile birlikte düşer (DB CASCADE yoksa manuel siliyoruz).
@app.route('/seo-graph/group/<group_id>/delete', methods=['POST'])
@login_required
def seo_graph_delete_group(group_id):
    # Sahiplik kontrolü + grup tracker'larını çek
    trackers = KeywordTracker.query.filter_by(
        user_id=current_user.id, group_id=group_id
    ).all()
    if not trackers:
        flash('SEO grubu bulunamadı.', 'warning')
        return redirect(url_for('seo_graph'))

    deleted = len(trackers)
    # History kayıtlarını da temizle (FK cascade garantisi yok)
    try:
        tracker_ids = [kt.id for kt in trackers]
        SEOHistory.query.filter(
            SEOHistory.keyword_tracker_id.in_(tracker_ids)
        ).delete(synchronize_session=False)
        # HOTFIX 1.91: Her tracker için Pool soft-delete
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
        return redirect(url_for('seo_graph'))

    flash(f'🗑️ Grup SEO takibi silindi ({deleted} ürün).', 'success')
    return redirect(url_for('seo_graph'))


# ── HOTFIX 1.85: Tekil SEO Takibini Sil (history dahil) ────────────────────
# Mevcut /seo-tracker/<id>/delete sadece KeywordTracker'ı siliyor; history
# kayıtları orphan kalıyordu. Bu yeni endpoint history'yi de siler ve
# SEO Grafik sayfasından çağrılmak üzere tasarlandı.
@app.route('/seo-graph/tracker/<int:tracker_id>/delete', methods=['POST'])
@login_required
def seo_graph_delete_tracker(tracker_id):
    kt = KeywordTracker.query.filter_by(id=tracker_id, user_id=current_user.id).first()
    if not kt:
        flash('Kayıt bulunamadı.', 'warning')
        return redirect(url_for('seo_graph'))

    try:
        SEOHistory.query.filter_by(keyword_tracker_id=kt.id).delete(
            synchronize_session=False
        )
        # HOTFIX 1.91: Pool soft delete
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
    return redirect(url_for('seo_graph'))


# ── HOTFIX 1.84: SEO Grafik Takibi Sayfası ─────────────────────────────────
# Kullanıcının KeywordTracker kayıtlarını gruplar halinde gösterir; her grup
# için SEOHistory zaman serisini ApexCharts'a hazır JSON olarak iletir.
@app.route('/seo-graph')
@login_required
def seo_graph():
    # Kullanıcının tüm aktif tracker'ları
    trackers = KeywordTracker.query.filter_by(
        user_id=current_user.id, is_active=True
    ).order_by(KeywordTracker.created_at.desc()).all()

    # Tracker'ları grup_id'ye göre kümele
    grouped = {}            # group_id (None=ungrouped) → list[tracker]
    for kt in trackers:
        key = kt.group_id or '__solo__'
        grouped.setdefault(key, []).append(kt)

    # Her tracker için son 50 SEOHistory kaydı → ApexCharts serisi
    # (ts_ms, overall_rank) — 0 ise null (chart gap için)
    import json
    chart_data = {}     # group_id → series_json (list of {name, data})
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
            # Mevcut sırayı son nokta olarak ekle (henüz history yoksa görsel için)
            if not points and (kt.current_page and kt.current_rank):
                overall = ((kt.current_page - 1) * 40 + kt.current_rank) \
                    if (kt.current_page > 0 and kt.current_rank > 0) else None
                if overall:
                    points.append([int((kt.last_checked or kt.created_at).timestamp() * 1000), overall])
            # Ürün adı kısalt + JSON-safe (tek tırnak → ʼ; HOTFIX 1.46 paralelizmi)
            name_short = (kt.target_url or '').split('/')[-1][:30].replace("'", "ʼ").replace('"', '”')
            series.append({
                'name': name_short or f'URL #{kt.id}',
                'data': points,
            })
        chart_data[gkey] = json.dumps(series, ensure_ascii=False)

    # Grup adlandırma: TrackedProduct.product_name baz alarak temsil edici ad
    group_labels = {}
    for gkey, kt_list in grouped.items():
        if gkey == '__solo__':
            group_labels[gkey] = 'Tekil Kelime Takipleri'
            continue
        # HOTFIX 1.87: Önce özel group_label (base ürün), sonra product_name, sonra default
        base = TrackedProduct.query.filter_by(
            user_id=current_user.id, group_id=gkey, is_base_product=True
        ).first()
        rep = base or TrackedProduct.query.filter_by(
            user_id=current_user.id, group_id=gkey
        ).first()
        if base and base.group_label:
            group_labels[gkey] = base.group_label[:80]
        elif rep and rep.product_name:
            group_labels[gkey] = rep.product_name[:60] + ('…' if len(rep.product_name) > 60 else '')
        else:
            group_labels[gkey] = f'Grup {gkey[:10]}'

    return render_template(
        'seo_graph.html',
        grouped=grouped,
        chart_data=chart_data,
        group_labels=group_labels,
    )


@app.route('/history')
@login_required
def history():
    if current_user.is_admin:
        return redirect(url_for('admin_jobs'))
        
    page = request.args.get('page', 1, type=int)
    jobs = Job.query.filter_by(user_id=current_user.id).order_by(Job.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('history.html', jobs=jobs)


@app.route('/new-request', methods=['GET', 'POST'])
@login_required
def new_request():

    if not current_user.can_submit:
        flash('Talep hakkınız kalmadı. Planınızı yükseltin veya dönem yenilenmesini bekleyin.', 'warning')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        job_type = request.form.get('job_type', 'combined')
        urls_raw = request.form.get('urls', '')
        api_key = request.form.get('api_key', '').strip()
        base_cost = request.form.get('base_cost', '').strip()

        urls = [u.strip() for u in urls_raw.strip().split('\n') if u.strip() and u.strip().startswith('http')]

        if not urls:
            flash('En az bir geçerli URL girmelisiniz.', 'error')
            return render_template('new_request.html')

        if len(urls) > 10:
            flash('Tek seferde en fazla 10 URL analiz edilebilir.', 'error')
            return render_template('new_request.html')

        # ── FAZ 3.5: Kombine/Fiyat/Yorum Analizi → SADECE TY & HB ──
        # 'track' (Fiyat Takibi) omnichannel kalır. Diğer analiz modülleri TY/HB-only.
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

        # Use system API key if user doesn't provide one
        # HOTFIX 1.44: DB yoksa .env'den oku (GROQ_API_KEY ortam değişkeni)
        if not api_key:
            api_key = Setting.get('groq_api_key', '') or os.environ.get('GROQ_API_KEY', '')

        # Zafiyet Radarı (stok takibi) geçici olarak devre dışı — resmi API entegrasyonu beklemede.
        if job_type == 'radar':
            flash('Zafiyet Radarı (stok takibi) geçici olarak devre dışıdır. Fiyat Takibi ile devam edebilirsiniz.', 'info')
            return redirect(url_for('new_request'))

        if job_type == 'track':
            if current_user.plan and current_user.plan.max_tracked_products > 0 and not current_user.is_admin:
                # Count distinct tracking groups/campaigns, not individual URLs
                current_campaigns = db.session.query(TrackedProduct.group_id).filter_by(user_id=current_user.id).distinct().count()
                if current_campaigns + 1 > current_user.plan.max_tracked_products:
                    flash(f'Planınızın limitini ({current_user.plan.max_tracked_products} takip kampanya grubu) aşıyorsunuz. Lütfen yükseltin.', 'error')
                    return redirect(url_for('user_plans'))

            import uuid
            group_id = str(uuid.uuid4())
            added = 0
            added_ids = []
            is_first = True
            # FAZ 1: base_cost formdan geldiğinde SADECE base ürüne unit_cost olarak yazılır.
            # target_price eski "alt limit" konseptiyle kalıyor (Buy Box widget'ı bunu okur).
            parsed_cost = None
            if base_cost:
                try:
                    parsed_cost = float(base_cost)
                except:
                    parsed_cost = None

            # NOT: unit_cost (maliyet) ve target_price (hedef satış/alt limit) ayrı kavramlardır.
            # Karıştırmak Buy Box widget'ında hatalı "Sizde/Rakipte" çıktısına yol açtığından
            # artık target_price'a maliyet YAZILMIYOR.
            for url in urls:
                exists = TrackedProduct.query.filter_by(user_id=current_user.id, url=url).first()
                if exists:
                    # Update existing product to be part of the new group and active
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
                        is_radar_tracked=False
                    )
                    db.session.add(tp)
                    db.session.flush()  # ID'yi hemen al
                    # HOTFIX 1.91: Global havuz bağlantısı
                    try:
                        attach_tracked_product_to_global(tp)
                    except Exception as _e:
                        print(f"[New analiz attach_global] {_e}")
                    added += 1
                    added_ids.append(tp.id)
                is_first = False

            log = UsageLog(user_id=current_user.id, action='add_tracked', details=f'{added} ürün eklendi')
            db.session.add(log)
            db.session.commit()

            if added > 0:
                try:
                    from worker import check_single_product_task
                    for pid in added_ids:
                        check_single_product_task.delay(pid)
                    flash(f'{added} adet ürün eklendi. İlk kontrol asenkron olarak başlatıldı.', 'success')
                except Exception as e:
                    print(f"[App] Background task error: {e}")
                    flash(f'{added} adet ürün eklendi. Kontrol sıraya alındı.', 'success')
            else:
                flash('Girdiğiniz ürünler zaten takip ediliyor.', 'info')

            return redirect(url_for('tracked_products'))

        # Standard job handling
        if not current_user.can_submit:
            flash('Talep hakkınız kalmadı. Planınızı yükseltin veya dönem yenilenmesini bekleyin.', 'warning')
            return redirect(url_for('dashboard'))

        job = Job(
            user_id=current_user.id,
            job_type=job_type,
            status='pending',
            api_key_used=api_key
        )
        job.set_urls(urls)
        db.session.add(job)

        log = UsageLog(user_id=current_user.id, action='submit_job', details=f'{job_type}: {len(urls)} URL')
        db.session.add(log)
        db.session.commit()

        try:
            from worker import process_job_task
            process_job_task.delay(job.id)
        except Exception as e:
            print(f"[App] Hata! Celery görevi başlatılamadı: {e}")

        flash('✅ Analiziniz başarıyla sıraya alındı! Arka planda yüzlerce veriyi tarıyoruz, tamamlandığında size bildirim göndereceğiz. Sitede özgürce gezinebilirsiniz.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('new_request.html')


@app.route('/job/<int:job_id>')
@login_required
def job_status(job_id):
    job = Job.query.get_or_404(job_id)
    if job.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return render_template('job_result.html', job=job)


@app.route('/job/<int:job_id>/cancel', methods=['POST'])
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
        
    return redirect(url_for('dashboard'))


@app.route('/job/<int:job_id>/report')
@login_required
def job_report(job_id):
    job = Job.query.get_or_404(job_id)
    if job.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    if not job.result_html:
        abort(404)
    return job.result_html


@app.route('/api/job/<int:job_id>/status')
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
        'has_result': bool(job.result_html)
    })


@app.route('/api/system-status')
@login_required
def api_system_status():
    from models import Job
    from worker import worker_state
    
    # 1. Check DB for active jobs to guarantee sync with dashboard
    running_job = Job.query.filter_by(status='running').order_by(Job.started_at.desc()).first()
    if running_job:
        jt_map = {'price': 'Fiyat Analizi', 'review': 'Yorum Analizi', 'combined': 'Kombine Analiz'}
        return jsonify({
            'is_active': True,
            'text': f"#{running_job.id} {jt_map.get(running_job.job_type, 'Analiz')} işleniyor..."
        })
        
    # 2. Fall back to memory state for background price tracking without DB jobs
    return jsonify({
        'is_active': worker_state.get('is_active', False),
        'text': worker_state.get('status_text', 'Hazır ve izlemede.')
    })


@app.route('/api/notifications/unread-count')
@login_required
def api_unread_notifications():
    """Return the current unread notification count for live badge updates."""
    count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({'count': count})


@app.route('/api/dashboard/jobs-status')
@login_required
def api_dashboard_jobs_status():
    """Check if any of the user's recent pending/running jobs have changed status.
    The dashboard JS polls this endpoint; if has_changes is true, it reloads the page."""
    # Check if any job recently completed or failed (within last 30 seconds)
    recent_threshold = get_tr_now() - timedelta(seconds=30)
    recently_changed = Job.query.filter(
        Job.user_id == current_user.id,
        Job.status.in_(['completed', 'failed']),
        Job.completed_at >= recent_threshold
    ).count()
    return jsonify({'has_changes': recently_changed > 0})

@app.route('/tracked-products', methods=['GET', 'POST'])
@login_required
def tracked_products():
    
    if request.method == 'POST':
        # Check how many distinct campaigns/groups the user has
        if current_user.plan and current_user.plan.max_tracked_products > 0 and not current_user.is_admin:
            current_campaigns = db.session.query(TrackedProduct.group_id).filter_by(user_id=current_user.id).distinct().count()
            if current_campaigns + 1 > current_user.plan.max_tracked_products:
                flash(f'Fiyat takip limitinizi ({current_user.plan.max_tracked_products} takip paketi) doldurdunuz.', 'warning')
                return redirect(url_for('tracked_products'))
            
        urls_raw = request.form.get('urls', '')
        if not urls_raw:
            urls_raw = request.form.get('url', '')

        # HOTFIX 1.87: Kullanıcının verdiği özel grup adı
        group_label_raw = (request.form.get('group_name') or '').strip()
        if len(group_label_raw) > 100:
            group_label_raw = group_label_raw[:100]
        group_label = group_label_raw or None
            
        import re
        raw_list = re.split(r'[\n\r\s,\u2028\u2029]+', urls_raw)
        valid_urls = [u.strip() for u in raw_list if u.strip().startswith('http')]
        
        if not valid_urls:
            flash('Geçerli bir ürün URL\'si girin.', 'error')
            return redirect(url_for('tracked_products'))
            
        import uuid
        group_id = str(uuid.uuid4())
        added_count = 0
        added_ids = []
        
        for idx, u in enumerate(valid_urls):
            exists = TrackedProduct.query.filter_by(user_id=current_user.id, url=u).first()
            if not exists:
                tp = TrackedProduct(
                    user_id=current_user.id,
                    url=u,
                    group_id=group_id,
                    is_base_product=(idx==0),
                    tracking_type='price',
                    is_price_tracked=True,
                    is_radar_tracked=False,
                    # HOTFIX 1.87: özel grup etiketi sadece BASE üründe yazılır
                    group_label=(group_label if idx == 0 else None),
                )
                db.session.add(tp)
                db.session.flush()  # ID'yi hemen al
                # HOTFIX 1.91: GlobalProduct havuzuna bağla (count++, dormant=False)
                try:
                    attach_tracked_product_to_global(tp)
                except Exception as _e:
                    print(f"[Tracked POST] attach_to_global fail: {_e}")
                added_count += 1
                added_ids.append(tp.id)

        db.session.commit()
        if added_count > 0:
            try:
                from worker import check_single_product_task
                for pid in added_ids:
                    check_single_product_task.delay(pid)
            except Exception as e:
                print(f"[App] Background task error: {e}")
            flash(f'{added_count} ürün takibe alındı. İlk fiyat kontrolü arka planda başlatıldı.', 'success')
        else:
            flash('Girdiğiniz ürünler zaten takip ediliyor veya geçerli değil.', 'info')
        return redirect(url_for('tracked_products'))
        
    # Multi-tracking: Fiyat Takibinde görünmesi gereken TÜM ürünler (radar'da da olanlar dahil)
    products = TrackedProduct.query.filter_by(
        user_id=current_user.id, is_price_tracked=True
    ).order_by(TrackedProduct.created_at.desc()).all()
    
    # Grouping products by campaign/group_id
    grouped = {}
    from collections import defaultdict
    for p in products:
        gid = p.group_id or str(p.id)  # fallback for legacy standalone products
        if gid not in grouped:
            grouped[gid] = []
        grouped[gid].append(p)
        
    # Generate Chart Data
    from models import PriceHistory
    import json
    chart_data = {}
    # FAZ 1: Her grubun base ürününün unit_cost'u — grafik için "Maliyet Çizgisi" verisi
    group_costs = {}
    for gid, gp_list in grouped.items():
        # Ensure base product is first in the list, then sort others chronologically old to new
        gp_list.sort(key=lambda x: (not x.is_base_product, x.created_at))
        # Base ürünün unit_cost değeri → grup geneli için kâr/zarar analizinde kullanılır
        base_cost_val = None
        if gp_list and gp_list[0].unit_cost is not None and gp_list[0].unit_cost > 0:
            base_cost_val = float(gp_list[0].unit_cost)
        group_costs[gid] = base_cost_val
        series = []
        for gp in gp_list:
            history = PriceHistory.query.filter_by(product_id=gp.id).order_by(PriceHistory.timestamp.asc()).all()
            data_points = []
            for h in history:
                ts = int(h.timestamp.timestamp() * 1000)
                data_points.append([ts, h.price])
                
            if not data_points and gp.current_price > 0:
                data_points.append([int(gp.created_at.timestamp() * 1000), gp.current_price])
                
            if data_points and gp.last_checked:
                ts_end = int(gp.last_checked.timestamp() * 1000)
                if ts_end > data_points[-1][0]:
                    data_points.append([ts_end, gp.current_price])
                
            name = ""
            if gp.product_name:
                name = gp.product_name[:35] + "..." if len(gp.product_name) > 35 else gp.product_name
            else:
                name = gp.platform_name or "Yükleniyor..."

            # ── HOTFIX 1.46: JSON/HTML attribute kesme-işareti (apostrof) çöküşü ──
            # Ürün adında geçen tek/çift tırnak (ör. "Eğitim Pedi 30'lu", içe tırnak
            # taşıyan rakip başlıkları) JSON'a serialize olduktan sonra HTML
            # attribute içine basıldığında attribute'u erken kapatıp ApexCharts'ı
            # parse error'la çökertiyordu. Tırnakları görsel olarak korumak için
            # kesme işaretini "ʼ" (Modifier Letter Apostrophe U+02BC) ve düz çift
            # tırnağı tipografik "“ ”" alternatifleriyle değiştiriyoruz; bu sayede
            # ürün adı okunabilir kalır ama HTML/JSON parser'ı bozmaz.
            name = (name
                    .replace("'", "ʼ")   # ' → ʼ (Modifier Letter Apostrophe)
                    .replace('"', "”")   # " → ” (Right Double Quotation Mark)
                    .replace('\\', ' '))      # ters slash → boşluk (JSON escape kazası önler)

            series.append({
                "name": ("👑 " if gp.is_base_product else "📉 ") + name,
                "data": data_points
            })
        chart_data[gid] = json.dumps(series, ensure_ascii=False)

    # FAZ 2.1: Aktif PriceAlert eşikleri — çan butonu üzerinde "mevcut eşikler" gösterimi için.
    # { tracked_product_id: {"below": X|None, "above": Y|None} } sözlüğü hazırlanır.
    active_alerts = PriceAlert.query.filter_by(user_id=current_user.id, is_active=True).all()
    product_alerts = {
        a.tracked_product_id: {"below": a.price_below, "above": a.price_above}
        for a in active_alerts
    }

    # HOTFIX 1.84: Hangi gruplar için SEO takibi başlatılmış? {group_id: keyword}
    # Her grup için ilk bulduğumuz aktif tracker'ın keyword'ünü temsil et.
    seo_grouped_rows = KeywordTracker.query.filter_by(
        user_id=current_user.id, is_active=True
    ).filter(KeywordTracker.group_id.isnot(None)).all()
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


@app.route('/tracked-products/export-excel')
@login_required
def export_tracked_excel():
    import csv, io
    products = TrackedProduct.query.filter_by(user_id=current_user.id).order_by(TrackedProduct.created_at.desc()).all()
    output = io.StringIO()
    output.write('\ufeff')  # BOM for Excel UTF-8
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Ürün Adı', 'Platform', 'Mevcut Fiyat (₺)', 'Önceki Fiyat (₺)', 'Değişim (%)', 'Stok', 'Son Kontrol', 'URL'])
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
            p.url
        ])
    response = app.response_class(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=bmk_takip_verileri.csv'}
    )
    return response


@app.route('/tracked-products/export-pdf')
@login_required
def export_tracked_pdf():
    products = TrackedProduct.query.filter_by(user_id=current_user.id).order_by(TrackedProduct.created_at.desc()).all()
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
    html = f"""<html><head><meta charset="utf-8">
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
    return html


@app.route('/tracked-products/<int:id>/delete', methods=['POST'])
@login_required
def delete_tracked_product(id):
    product = TrackedProduct.query.get_or_404(id)
    if product.user_id != current_user.id:
        abort(403)

    # HOTFIX 1.24: Strict Hard Delete.
    # Zafiyet Radarı kalıcı olarak devre dışı — is_radar_tracked bayrağından bağımsız
    # olarak ürün tamamen silinir. CASCADE ile PriceHistory, StockHistory,
    # VulnerabilityAlert, PriceAlert ilişkili satırlar da temizlenir.
    db.session.delete(product)
    db.session.commit()
    flash('Ürün takipten kaldırıldı.', 'info')
    return redirect(url_for('tracked_products'))

# ═══════════════════════════════════════════════════════════════════════════
# Zafiyet Radarı route'ları geçici olarak DEVRE DIŞI.
# Stok takibi konsepti, resmi API entegrasyonu sağlanana kadar uyku modunda.
# Eski URL'lere gelen istekler Fiyat Takibi sayfasına yönlendirilir.
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/zafiyet-radari')
@login_required
def zafiyet_radari_list():
    flash('Zafiyet Radarı geçici olarak devre dışıdır.', 'info')
    return redirect(url_for('tracked_products'))

def _compute_radar_analytics(product, history):
    """
    Hibrit Stok Erime Analitiği:
      - Mikro Trend: Son 72 saat içindeki kayıtlardan günlük ortalama erime
      - Makro Trend: İlk kayıt → son kayıt arasındaki günlük ortalama erime
      - Spike: Mikro hız Makro'dan %30+ fazlaysa True
    Geri dönüş: dict (None alanlar = yetersiz veri)
    """
    from datetime import timedelta
    result = {
        'micro_per_day': None, 'micro_text': None, 'micro_window_days': 3,
        'macro_per_day': None, 'macro_text': None,
        'macro_total_days': None, 'macro_total_drained': None,
        'spike': False, 'has_data': False,
        # Sparkline + net erime özeti için
        'recent_stocks': [],  # Son 5 stok değeri (sparkline bar'ları)
        'net_drain_days': None,  # Kaç gün içinde
        'net_drain_amount': None,  # Kaç adet eridi
    }
    if not history or len(history) < 2:
        return result

    # Sırala (eskiden yeniye)
    h = sorted(history, key=lambda x: x.timestamp)
    now = get_tr_now()

    # Helper: timestamp'i naive yap (TR timezone karışıklığını önle)
    def _naive(dt):
        try:
            return dt.replace(tzinfo=None)
        except:
            return dt
    now_n = _naive(now)

    # Pozitif erime: stok düşüşü; negatif (artış) trendi sıfırlar
    def _drain_per_day(records):
        if len(records) < 2:
            return None, 0, 0
        first = records[0]
        last = records[-1]
        delta_stock = first.stock - last.stock  # +pozitif: erime
        seconds = (_naive(last.timestamp) - _naive(first.timestamp)).total_seconds()
        days = max(seconds / 86400.0, 1/24.0)  # min 1 saat → 0.0417 gün
        return (delta_stock / days), delta_stock, days

    # MAKRO: ilk → son tüm kayıtlar
    macro_rate, macro_drain, macro_days = _drain_per_day(h)
    if macro_rate is not None:
        result['macro_per_day'] = round(macro_rate, 2)
        result['macro_total_days'] = round(macro_days, 1)
        result['macro_total_drained'] = max(0, macro_drain)
        if macro_rate > 0:
            result['macro_text'] = f"Genel günlük ortalama: {round(macro_rate, 1)} adet/gün"
        elif macro_rate < 0:
            result['macro_text'] = f"Stok artıyor (günde +{round(-macro_rate, 1)} adet)"
        else:
            result['macro_text'] = "Genel: stabil"

    # MİKRO: son 72 saat
    threshold = now_n - timedelta(hours=72)
    micro_records = [r for r in h if _naive(r.timestamp) >= threshold]
    if len(micro_records) >= 2:
        micro_rate, _, _ = _drain_per_day(micro_records)
        if micro_rate is not None:
            result['micro_per_day'] = round(micro_rate, 2)
            if micro_rate > 0.5:
                result['micro_text'] = f"Son 3 günde günde ~{round(micro_rate, 1)} adet eriyor"
            elif micro_rate > 0:
                result['micro_text'] = f"Son 3 günde yavaş erime (~{round(micro_rate, 1)} adet/gün)"
            elif micro_rate < 0:
                result['micro_text'] = f"Son 3 günde stok artıyor (+{round(-micro_rate, 1)} adet/gün)"
            else:
                result['micro_text'] = "Son 3 günde stabil"
    elif len(micro_records) == 1 and macro_rate is not None:
        # Yetersizse makro'yu mikro yerine kullan (gösterge olarak)
        result['micro_text'] = f"Son haftalık ort. ~{round(macro_rate, 1)} adet/gün"
        result['micro_per_day'] = result['macro_per_day']

    # Sıçrama tespiti: Mikro >= Makro * 1.30 ve her ikisi de pozitif
    if (result['micro_per_day'] is not None
            and result['macro_per_day'] is not None
            and result['macro_per_day'] > 0.1
            and result['micro_per_day'] >= result['macro_per_day'] * 1.30):
        result['spike'] = True

    result['has_data'] = (result['micro_per_day'] is not None
                          or result['macro_per_day'] is not None)

    # Sparkline: son 5 stok kaydı (bar chart için)
    recent = h[-5:] if len(h) >= 2 else h
    result['recent_stocks'] = [r.stock for r in recent if r.stock is not None and r.stock >= 0]

    # Net erime özeti: "Son X günde Y adet eridi"
    if len(h) >= 2:
        first_stock = h[0].stock if h[0].stock is not None else 0
        last_stock = h[-1].stock if h[-1].stock is not None else 0
        drain = first_stock - last_stock
        days_span = (_naive(h[-1].timestamp) - _naive(h[0].timestamp)).total_seconds() / 86400.0
        result['net_drain_days'] = max(1, round(days_span))
        result['net_drain_amount'] = max(0, drain)

    return result


def _compute_strategic_signals(product, analytics):
    """
    Stok seviyesi + trend analizine göre stratejik rakip sinyalleri üretir.
    Geri dönüş: list[dict(level, icon, title, body)]
    """
    signals = []
    stok = product.current_stock if product.current_stock is not None else -1

    # 1. Stok seviyesi bazlı ana sinyal
    if 0 < stok <= 5:
        signals.append({
            'level': 'opportunity',
            'icon': 'target',
            'title': '🚨 BuyBox Fırsatı',
            'body': 'Rakip stok tüketmek üzere. Fiyatınızı ufak bir miktar artırmayı planlayın — Buy Box her an size geçebilir.'
        })
    elif 5 < stok <= 10:
        signals.append({
            'level': 'warning',
            'icon': 'alert-triangle',
            'title': '⚠️ Rekabet Uyarısı',
            'body': 'Rakip stok eritiyor ancak henüz kritik seviyede değil. Reklam bütçenizi koruyun, fiyat agresifliğini erken artırmayın.'
        })
    elif stok == 0:
        signals.append({
            'level': 'opportunity',
            'icon': 'check-circle-2',
            'title': '🟢 BUY BOX SİZDE',
            'body': 'Rakip stoğu tükenmiş! Bu, BuyBox\'ı kapma fırsatıdır. Trafik ve dönüşümleri yakından izleyin.'
        })
    else:
        # 10+ veya bilinmiyor (-1)
        signals.append({
            'level': 'safe',
            'icon': 'shield-check',
            'title': '🛡️ Güvenli Bölge',
            'body': 'Rakip güçlü stokla bekliyor. Yakın vadede bir BuyBox değişikliği beklenmiyor.'
        })

    # 2. Sıçrama alarmı (Mikro hız > Makro hız * 1.30)
    if analytics.get('spike'):
        micro = analytics.get('micro_per_day') or 0
        macro = analytics.get('macro_per_day') or 0
        signals.append({
            'level': 'threat',
            'icon': 'flame',
            'title': '🔥 ANLIK SIÇRAMA',
            'body': f'Rakip son 3 günde normalden çok daha hızlı stok eritiyor (mikro: ~{round(micro,1)}/gün, makro: ~{round(macro,1)}/gün). Kampanya yapmış veya viral olmuş olabilir!'
        })

    return signals


@app.route('/zafiyet-radari/<string:group_id>')
@login_required
def zafiyet_radari(group_id):
    # Zafiyet Radarı geçici olarak devre dışı — Fiyat Takibi'ne yönlendir.
    flash('Zafiyet Radarı geçici olarak devre dışıdır.', 'info')
    return redirect(url_for('tracked_products'))


# ── HOTFIX 1.88: Mevcut Grup Adını Düzenle ────────────────────────────────
# Kullanıcı tracked_products veya seo_graph sayfasından grup başlığının
# yanındaki "✏️" butonuna tıklar → modal açılır → yeni ad → POST.
# Etiket sadece BASE üründe (is_base_product=True) tutulduğu için tek satır
# UPDATE yeterli. Base ürün yoksa (eski veri) grup içinden ilk ürüne yazılır.
@app.route('/tracked-products/group/<string:group_id>/rename', methods=['POST'])
@login_required
def rename_tracked_group(group_id):
    new_name = (request.form.get('group_name') or '').strip()
    if len(new_name) > 100:
        new_name = new_name[:100]
    # Boş gönderim → etiketi temizle (None) → fallback davranışa dön
    new_label = new_name or None

    # Sahiplik kontrolü + base ürünü bul (yoksa grubun ilk ürünü)
    base = TrackedProduct.query.filter_by(
        user_id=current_user.id, group_id=group_id, is_base_product=True
    ).first()
    if not base:
        base = TrackedProduct.query.filter_by(
            user_id=current_user.id, group_id=group_id
        ).first()
    if not base:
        flash('Grup bulunamadı.', 'warning')
        return redirect(url_for('tracked_products'))

    try:
        base.group_label = new_label
        db.session.commit()

        # ── HOTFIX 1.97: Grup adı = SEO anahtar kelime senkronizasyonu ──
        # Yeni ad varsa, bu gruba bağlı tüm KeywordTracker.keyword değerlerini
        # de aynı yeni ada güncelle → bir sonraki tarama yeni keyword'ü arar.
        # NOT: Boş ad ise SEO keyword'lere dokunulmaz (önceki davranışı koru).
        seo_synced = 0
        if new_label:
            from models import KeywordPool, get_or_create_keyword_pool
            trackers = KeywordTracker.query.filter_by(
                user_id=current_user.id, group_id=group_id, is_active=True
            ).all()
            for kt in trackers:
                if kt.keyword == new_label:
                    continue  # zaten aynı, atla
                old_pool_id = kt.pool_id
                # Tracker'ın keyword'ünü güncelle + son sıra verisini sıfırla
                # (eski keyword'ün sırası yanlış yorumlanmasın; ilk taramada doğru sıra çıkar)
                kt.keyword = new_label
                kt.previous_page = kt.current_page or 0
                kt.previous_rank = kt.current_rank or 0
                kt.current_page = 0
                kt.current_rank = 0
                kt.last_checked = None  # acil taramaya alınsın

                # Yeni keyword için pool oluştur veya bağlan
                new_pool = get_or_create_keyword_pool(kt.platform, new_label, kt.target_url)
                if new_pool:
                    # Eski pool'dan ayrıl → yeni pool'a bağlan + sayaç senkronize
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
                # Anında ilk tarama tetikle (.delay async)
                try:
                    from worker import check_keyword_trackers_task
                    check_keyword_trackers_task.delay([kt.id for kt in trackers])
                except Exception:
                    pass

        # Flash mesajı
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

    # Geldiği sayfaya dön (referer)
    ref = request.form.get('return_to') or request.referrer or ''
    if 'seo-graph' in ref:
        return redirect(url_for('seo_graph') + f'#group-{group_id}')
    return redirect(url_for('tracked_products'))


@app.route('/tracked-products/group/<string:group_id>/delete', methods=['POST'])
@login_required
def delete_tracked_group(group_id):
    products = TrackedProduct.query.filter_by(user_id=current_user.id, group_id=group_id).all()
    if not products:
        # Fallback for single legacy product acting as a group
        product = TrackedProduct.query.filter_by(user_id=current_user.id, id=group_id).first()
        if product:
            products = [product]

    # HOTFIX 1.24: Strict Hard Delete (kullanıcı tarafı) — TP'ler silinir.
    # HOTFIX 1.91 Soft Delete (global): TP silinmeden ÖNCE bağlı GP.active_users_count--
    # → 0 olunca GP.is_dormant=True (worker o ürünü taramayı bırakır).
    # GP ve tüm tarihsel veri DB'de KALIR — başka kullanıcı eklerse uyanır.
    for p in products:
        try:
            detach_tracked_product_from_global(p)
        except Exception as _e:
            print(f"[delete_tracked_group detach] {_e}")
        db.session.delete(p)
    db.session.commit()

    flash('Ürün grubu takipten kaldırıldı.', 'info')
    return redirect(url_for('tracked_products'))


@app.route('/tracked-products/group/<string:group_id>/add', methods=['POST'])
@login_required
def add_to_tracked_group(group_id):
    urls_raw = request.form.get('urls', '')
    if not urls_raw:
        urls_raw = request.form.get('url', '')
        
    import re
    raw_list = re.split(r'[\n\r\s,\u2028\u2029]+', urls_raw)
    valid_urls = [u.strip() for u in raw_list if u.strip().startswith('http')]
    
    if not valid_urls:
        return redirect(url_for('tracked_products'))
        
    # Check quota
    if current_user.remaining_tracked_quota <= 0:
        flash('Takip kotanız doldu. Daha fazla ürün takip etmek için planınızı yükseltin.', 'danger')
        return redirect(url_for('user_plans'))
        
    added_count = 0
    added_ids = []
    quota_exceeded = False
        
    for u in valid_urls:
        if u.startswith('__COST__:'):
            continue
            
        if current_user.remaining_tracked_quota <= 0:
            quota_exceeded = True
            break
            
        # Zafiyet Radarı devre dışı — yeni eklemeler sadece Fiyat Takibi bayrağıyla gelir
        new_tp = TrackedProduct(
            user_id=current_user.id,
            url=u,
            group_id=group_id,
            is_base_product=False,
            tracking_type='price',
            is_price_tracked=True,
            is_radar_tracked=False
        )
        db.session.add(new_tp)
        db.session.flush()  # ID'yi hemen al
        added_count += 1
        added_ids.append(new_tp.id)
        
    db.session.commit()
    
    if added_count > 0:
        try:
            from worker import check_single_product_task
            for pid in added_ids:
                check_single_product_task.delay(pid)
        except Exception as e:
            print(f"[App] Background task error: {e}")
    
    if quota_exceeded:
        flash(f'Kota sınırı nedeniyle eklenebilen ürün sayısı: {added_count}.', 'warning')
    else:
        flash(f'{added_count} yeni ürün başarıyla gruba eklendi! Arka planda fiyatı kontrol edilecek.', 'success')
    return redirect(url_for('tracked_products'))


@app.route('/tracked-products/group/<string:group_id>/cost', methods=['POST'])
@login_required
def update_tracked_group_cost(group_id):
    if not current_user.has_premium_access:
        flash('🔒 Bu özellik Profesyonel ve Kurumsal planlara özeldir.', 'warning')
        return redirect(url_for('user_plans'))

    # Form iki farklı isimden gelebilir: 'unit_cost' (yeni Faz 1 modalı) veya 'target_price' (eski modal).
    raw_cost = request.form.get('unit_cost')
    if raw_cost is None or raw_cost == '':
        raw_cost = request.form.get('target_price')
    try:
        cost = float(raw_cost) if raw_cost not in (None, '') else 0.0
    except:
        cost = 0.0

    products = TrackedProduct.query.filter_by(user_id=current_user.id, group_id=group_id).all()
    # HOTFIX 1.5 — Eski kayıtlarda is_base_product bayrağı tanımsız olabiliyor;
    # bu yüzden şartlı yazım döngüyü sessizce boşa çıkarıyordu. Artık grup içindeki
    # TÜM ürünlerin unit_cost kolonuna maliyet yazıyoruz. target_price'a dokunmuyoruz.
    new_cost = cost if cost > 0 else None
    for p in products:
        p.unit_cost = new_cost
    db.session.commit()
    flash('Birim maliyet başarıyla güncellendi.', 'success')
    return redirect(url_for('tracked_products'))


# ── FAZ 2.1: Çift Yönlü Akıllı Tetikleyiciler ───────────────────────────────
@app.route('/tracked-products/alert/add', methods=['POST'])
@login_required
def add_price_alert():
    """Kullanıcı bir takip edilen ürün için çift yönlü alarm kurar.

    Form alanları (ikisi de opsiyonel ama en az biri zorunlu):
      • price_below → fiyat bu değerin altına düşerse 🚨 tetikle
      • price_above → fiyat bu değerin üstüne çıkarsa 📈 tetikle

    Güvenlik: Sadece kullanıcının kendi ürünleri için alarm kurulabilir (user_id eşleşmesi).
    Idempotent: Aynı ürün için aktif alarm varsa eşikleri GÜNCELLER, yeni satır açmaz.
    """
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
        return redirect(url_for('tracked_products'))

    price_below = _parse_opt_float(request.form.get('price_below'))
    price_above = _parse_opt_float(request.form.get('price_above'))

    if product_id <= 0:
        flash('⚠️ Geçersiz ürün.', 'danger')
        return redirect(url_for('tracked_products'))

    if price_below is None and price_above is None:
        flash('⚠️ En az bir eşik değeri (Alt veya Üst Limit) girmelisiniz.', 'danger')
        return redirect(url_for('tracked_products'))

    # Mantık kontrolü: Alt limit, üst limitten büyük olamaz
    if price_below is not None and price_above is not None and price_below >= price_above:
        flash('⚠️ Alt Limit, Üst Limit\'ten küçük olmalıdır.', 'danger')
        return redirect(url_for('tracked_products'))

    # Sahiplik kontrolü
    product = TrackedProduct.query.filter_by(id=product_id, user_id=current_user.id).first()
    if not product:
        flash('⚠️ Ürün bulunamadı veya bu işlem için yetkiniz yok.', 'danger')
        return redirect(url_for('tracked_products'))

    # Idempotent: aynı ürüne aktif alarm varsa eşikleri güncelle
    existing = PriceAlert.query.filter_by(
        user_id=current_user.id,
        tracked_product_id=product_id,
        is_active=True
    ).first()

    if existing:
        existing.price_below = price_below
        existing.price_above = price_above
        db.session.commit()
        flash('🔔 Alarm güncellendi. Yeni eşikler aktif.', 'success')
    else:
        alert = PriceAlert(
            user_id=current_user.id,
            tracked_product_id=product_id,
            price_below=price_below,
            price_above=price_above,
            is_active=True,
        )
        db.session.add(alert)
        db.session.commit()
        flash('🔔 Alarm kuruldu. Eşikler sağlandığında anında haber vereceğiz.', 'success')

    return redirect(url_for('tracked_products'))


@app.route('/notifications')
@login_required
def notifications():
    """
    HOTFIX 1.54: Sınıflandırılmış bildirim sayfası.
      • ?cat=<kategori>  : filter (default: all)
      • ?page=<n>        : sayfalama (50 bildirim/sayfa)
      • Lazy AI backfill : category=NULL olan kayıtları AI ile sınıflandırır

    HOTFIX 1.99: Otomatik okundu işaretleme TAMAMEN KALDIRILDI.
      • Sayfaya girmek bildirimleri okundu yapmaz
      • Sadece 2 yolla okundu işaretlenir:
        a) Kullanıcı bildirimin "Grafiği İncele" / "Pazaryerine Git" linkine tıklar
        b) Kullanıcı "✔️ Tümünü Okundu İşaretle" butonuna basar (AJAX veya form)
      • 'seo' kategorisi eklendi (Worker tarafı SEO sıralama bildirimi üretir)
    """
    # 1) cat parametresi (filtre)
    cat = request.args.get('cat', 'all')
    valid_cats = ('all', 'price_down', 'price_up', 'combined',
                  'opportunity', 'threat', 'system', 'seo')
    if cat not in valid_cats:
        cat = 'all'

    # HOTFIX 1.99: AUTO-READ KALDIRILDI — kullanıcı sayfaya girmekle okundu olmaz

    # 2) Lazy AI backfill — category=NULL kayıtları sınıflandır (en fazla 20/sayfa açılışında)
    # Önce kural-bazlı tarama (ücretsiz, hızlı); AI sadece kuralın bilemediği için.
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
            print(f"[NotificationBackfill] {len(null_cats)} kayıt sınıflandırıldı.")
    except Exception as e:
        # Backfill başarısız olursa sayfa yine açılır (graceful degrade)
        print(f"[NotificationBackfill] Hata: {e}")
        db.session.rollback()

    # 3) (cat ve valid_cats yukarıda — HOTFIX 1.62 — okuma-işaretlemeden önce alındı)

    # 4) Sayfalama
    try:
        page = max(1, int(request.args.get('page', 1)))
    except Exception:
        page = 1
    per_page = 50

    # 5) Sorgu — kategoriye göre filtre
    base_q = Notification.query.filter_by(user_id=current_user.id)
    if cat != 'all':
        base_q = base_q.filter_by(category=cat)
    base_q = base_q.order_by(Notification.created_at.desc())

    total = base_q.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)  # sınırı aşmasın
    notifs = base_q.offset((page - 1) * per_page).limit(per_page).all()

    # 6) Sekme rozet sayıları — HOTFIX 1.81: OKUNMAMIŞ sayısı.
    # Aktif sekmede yukarıda okundu işaretledik → o kategori 0 görünür,
    # diğer kategorilerin okunmamış sayıları rozetlerde belirgin kalır.
    from sqlalchemy import func
    cat_unread_rows = db.session.query(
        Notification.category, func.count(Notification.id)
    ).filter_by(user_id=current_user.id, is_read=False).group_by(Notification.category).all()
    cat_counts = {row[0] or 'system': row[1] for row in cat_unread_rows}
    cat_counts['all'] = sum(row[1] for row in cat_unread_rows)
    for c in valid_cats[1:]:
        cat_counts.setdefault(c, 0)

    # Sidebar badge → toplam kalan okunmamış
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


# ── HOTFIX 1.81: AJAX endpoint — sekme tıklamasında optimistic update için ─────
# Frontend tıklamayı yakalar, bu endpoint'e POST atar, dönen cat_unread haritası
# ile rozetleri ANINDA günceller (sayfa navigate olmadan da çalışır).
# Server-side auto-read zaten /notifications route'unda da yapılıyor — bu endpoint
# JS açık kullanıcılara hız kazandırır.
@app.route('/api/notifications/mark-category-read', methods=['POST'])
@login_required
def api_mark_category_read():
    data = request.get_json(silent=True) or {}
    cat = data.get('cat', 'all')
    # HOTFIX 1.99: 'seo' kategorisi eklendi
    valid_cats = ('all', 'price_down', 'price_up', 'combined',
                  'opportunity', 'threat', 'system', 'seo')
    if cat not in valid_cats:
        cat = 'all'

    q = Notification.query.filter_by(user_id=current_user.id, is_read=False)
    if cat != 'all':
        q = q.filter_by(category=cat)
    marked = q.update({'is_read': True}, synchronize_session=False)
    db.session.commit()

    # Güncel okunmamış sayım (frontend tüm rozetleri yenileyebilsin)
    from sqlalchemy import func
    rows = db.session.query(
        Notification.category, func.count(Notification.id)
    ).filter_by(user_id=current_user.id, is_read=False).group_by(Notification.category).all()
    cat_unread = {r[0] or 'system': r[1] for r in rows}
    cat_unread['all'] = sum(r[1] for r in rows)
    for c in valid_cats[1:]:
        cat_unread.setdefault(c, 0)

    return jsonify({
        'success': True,
        'marked': marked,
        'cat_unread': cat_unread,
    })


@app.route('/notifications/read-all', methods=['POST'])
@login_required
def read_all_notifications():
    # HOTFIX 1.99: AJAX + Form çift mod — JSON Accept header'ı varsa JSON döner
    marked = Notification.query.filter_by(
        user_id=current_user.id, is_read=False
    ).update({'is_read': True}, synchronize_session=False)
    db.session.commit()

    # AJAX isteği mi? (X-Requested-With veya Accept: application/json)
    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )
    if is_ajax:
        return jsonify({'success': True, 'marked': marked})

    cat = request.args.get('cat') or request.form.get('cat', 'all')
    return redirect(url_for('notifications', cat=cat))


# ── HOTFIX 1.80: Bildirim Aç — link tıklamasında is_read=True yap, sonra redirect ──
# Kullanıcı bildirimdeki "Ürüne Git" / "Detayları Gör" linkine tıkladığında bu
# endpoint çalışır: bildirim okundu olarak işaretlenir, ardından gerçek link'e
# yönlendirilir. Bu, e-mail/Slack davranışıdır — bir bildirimle etkileşim ettiysen
# zaten "okumuş" sayılırsın.
@app.route('/notifications/<int:notif_id>/open')
@login_required
def notification_open(notif_id):
    n = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first()
    if not n:
        flash('Bildirim bulunamadı.', 'error')
        return redirect(url_for('notifications'))

    # Okundu işaretle (linke tıklamak = etkileşim = okundu)
    if not n.is_read:
        n.is_read = True
        db.session.commit()

    to = (request.args.get('to') or '').strip().lower()

    # ── HOTFIX 1.99.1: Dış link tıklaması (her zaman n.link → pazaryeri) ──
    if to == 'external' and n.link:
        return redirect(n.link)

    # ── HOTFIX 1.99.1: İç grafik tıklaması — internal_link öncelikli,
    # yoksa n.link'ten ürünü bul, group_id'sini çıkar, anchor URL'i üret.
    # Geçmiş bildirimler (internal_link NULL) için RUNTIME RESOLUTION. ──
    if to == 'internal':
        # 1) internal_link kayıtlı ise direkt kullan
        if n.internal_link:
            return redirect(n.internal_link)

        # 2) Runtime resolution — n.link (pazaryeri URL) → grup
        if n.link and n.link.startswith('http'):
            try:
                # SEO bildirimi mi? KeywordTracker üzerinden çöz
                if n.category == 'seo':
                    kt = KeywordTracker.query.filter_by(
                        user_id=current_user.id, target_url=n.link, is_active=True
                    ).first()
                    if kt:
                        # Pool veya kullanıcı grubu üzerinden anchor üret
                        if kt.group_id:
                            return redirect(url_for('seo_graph') + f'#group-{kt.group_id}')
                        # Tekil takip — SEO grafik sayfasında genel
                        return redirect(url_for('seo_graph'))
                    # SEO tracker bulunamadıysa SEO ana sayfasına
                    return redirect(url_for('seo_graph'))

                # Fiyat / kombine / fırsat / tehdit → TrackedProduct üzerinden çöz
                tp = TrackedProduct.query.filter_by(
                    user_id=current_user.id, url=n.link
                ).first()
                if tp and tp.group_id:
                    return redirect(url_for('tracked_products') + f'#group-{tp.group_id}')
                if tp:
                    return redirect(url_for('tracked_products'))
            except Exception as e:
                print(f"[notification_open] iç çözünürlük hatası: {e}")

        # 3) Fallback — kategori bazlı genel sayfa
        if n.category == 'seo':
            return redirect(url_for('seo_graph'))
        if n.category == 'combined':
            return redirect(url_for('history'))
        return redirect(url_for('tracked_products'))

    # ── Backward compat (eski "to" parametresiz tıklamalar) ──
    target = n.internal_link or n.link
    if not target:
        return redirect(url_for('notifications'))
    return redirect(target)


# ── HOTFIX 1.54: Kategoriyi Temizle ──────────────────────────────────────────
# Sadece aktif sekmede gözüken bildirimleri (current_user'a ait olanlar) siler.
# 'all' kategorisi seçildiğinde TÜM bildirimleri siler — flash mesajıyla onay
# beklenir; frontend confirm() ile çift kontrol yapılır.
@app.route('/notifications/clear', methods=['POST'])
@login_required
def clear_notifications_category():
    cat = request.form.get('cat', 'all')
    # HOTFIX 1.99: 'seo' kategorisi eklendi
    valid_cats = ('all', 'price_down', 'price_up', 'combined',
                  'opportunity', 'threat', 'system', 'seo')
    if cat not in valid_cats:
        cat = 'all'

    q = Notification.query.filter_by(user_id=current_user.id)
    if cat != 'all':
        q = q.filter_by(category=cat)

    cat_label_map = {
        'all': 'Tüm bildirimler',
        'price_down': 'Fiyatı düşen bildirimler',
        'price_up': 'Fiyatı yükselen bildirimler',
        'combined': 'Kombine analiz bildirimleri',
        'opportunity': 'Fırsat bildirimleri',
        'threat': 'Tehdit bildirimleri',
        'system': 'Sistem mesajları',
        'seo': 'SEO sıralama bildirimleri',  # HOTFIX 1.99
    }
    count = q.count()
    q.delete(synchronize_session=False)
    db.session.commit()
    flash(f"🗑️ {cat_label_map.get(cat, 'Bildirimler')} silindi ({count} kayıt).", 'success')
    # Kullanıcıyı aynı sekmede tutmak için query string'i koru
    return redirect(url_for('notifications', cat=cat))


@app.route('/plans')
@login_required
def user_plans():
    if current_user.is_admin:
        return redirect(url_for('admin_plans'))
    plans = Plan.query.filter_by(is_active=True).order_by(Plan.sort_order).all()
    return render_template('user_plans.html', plans=plans)


# =========================================================================
# ADMIN ROUTES
# =========================================================================
@app.route('/admin')
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
    pending_users = User.query.filter_by(is_approved=False, is_admin=False).order_by(User.created_at.desc()).all()

    # Jobs in the last 7 days
    week_ago = get_tr_now() - timedelta(days=7)
    weekly_jobs = Job.query.filter(Job.created_at >= week_ago).count()

    # Plan Distribution & MRR Calculation
    plans = Plan.query.all()
    plan_distribution = []
    mrr = 0.0
    for p in plans:
        count = User.query.filter_by(is_admin=False, plan_id=p.id, is_active=True).count()
        plan_distribution.append({'name': p.display_name, 'count': count})
        mrr += count * (p.price_monthly or 0)

    # MRR Growth: compare active paying users now vs 30 days ago
    month_ago = get_tr_now() - timedelta(days=30)
    users_last_month = User.query.filter(User.is_admin == False, User.is_active == True, User.created_at <= month_ago).count()
    mrr_growth = 0
    if users_last_month > 0 and active_customers > users_last_month:
        mrr_growth = round(((active_customers - users_last_month) / users_last_month) * 100)

    # System Load: ratio of running jobs to a reasonable capacity estimate
    system_capacity = max(active_customers * 2, 20)  # rough estimate
    system_load = min(round((running_jobs / system_capacity) * 100), 100) if system_capacity > 0 else 0

    return render_template('admin/dashboard.html',
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
                           system_load=system_load)


@app.route('/admin/customers')
@login_required
@admin_required
def admin_customers():
    customers = User.query.filter_by(is_admin=False).order_by(User.created_at.desc()).all()
    plans = Plan.query.filter_by(is_active=True).order_by(Plan.sort_order).all()
    return render_template('admin/customers.html', customers=customers, plans=plans)


@app.route('/admin/customers/<int:user_id>/approve', methods=['POST'])
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
    return redirect(url_for('admin_customers'))


@app.route('/admin/customers/<int:user_id>/toggle', methods=['POST'])
@login_required
@admin_required
def admin_toggle_customer(user_id):
    user = User.query.get_or_404(user_id)
    user.is_active = not user.is_active
    db.session.commit()
    status = 'aktif' if user.is_active else 'devre dışı'
    flash(f'{user.full_name} artık {status}.', 'info')
    return redirect(url_for('admin_customers'))


@app.route('/admin/customers/<int:user_id>/plan', methods=['POST'])
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
    return redirect(url_for('admin_customers'))


@app.route('/admin/jobs')
@login_required
@admin_required
def admin_jobs():
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')
    query = Job.query.order_by(Job.created_at.desc())
    if status_filter:
        query = query.filter_by(status=status_filter)
    jobs = query.paginate(page=page, per_page=20, error_out=False)
    
    # Count jobs per status for tab badges
    from sqlalchemy import func
    status_counts = dict(db.session.query(Job.status, func.count(Job.id)).group_by(Job.status).all())
    total_count = sum(status_counts.values())
    
    return render_template('admin/jobs.html', jobs=jobs, status_filter=status_filter, 
                         status_counts=status_counts, total_count=total_count)


@app.route('/admin/tracking')
@login_required
@admin_required
def admin_tracking():
    from worker import worker_state
    from sqlalchemy import func

    # Filters
    user_filter = request.args.get('user', '', type=str)
    status_filter = request.args.get('status', '')  # active, inactive, or ''

    query = TrackedProduct.query.join(User, TrackedProduct.user_id == User.id)

    if user_filter:
        query = query.filter(TrackedProduct.user_id == int(user_filter))
    if status_filter == 'active':
        query = query.filter(TrackedProduct.is_active == True)
    elif status_filter == 'inactive':
        query = query.filter(TrackedProduct.is_active == False)

    page = request.args.get('page', 1, type=int)
    products = query.order_by(TrackedProduct.last_checked.desc().nullsfirst()).paginate(
        page=page, per_page=25, error_out=False
    )

    # Stats
    total_tracked = TrackedProduct.query.count()
    active_tracked = TrackedProduct.query.filter_by(is_active=True).count()
    inactive_tracked = total_tracked - active_tracked

    # Price changes in last 24h
    day_ago = get_tr_now() - timedelta(hours=24)
    price_changes_24h = PriceHistory.query.filter(PriceHistory.timestamp >= day_ago).count()

    # Users with tracked products (for filter dropdown)
    tracking_users = db.session.query(User.id, User.full_name, func.count(TrackedProduct.id).label('count')).join(
        TrackedProduct, TrackedProduct.user_id == User.id
    ).group_by(User.id, User.full_name).all()

    return render_template('admin/tracking.html',
                           products=products,
                           total_tracked=total_tracked,
                           active_tracked=active_tracked,
                           inactive_tracked=inactive_tracked,
                           price_changes_24h=price_changes_24h,
                           worker_state=worker_state,
                           tracking_users=tracking_users,
                           user_filter=user_filter,
                           status_filter=status_filter)


@app.route('/admin/plans')
@login_required
@admin_required
def admin_plans():
    plans = Plan.query.order_by(Plan.sort_order).all()
    return render_template('admin/plans.html', plans=plans)


@app.route('/admin/plans/<int:plan_id>/edit', methods=['POST'])
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
    return redirect(url_for('admin_plans'))


@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_settings():
    if request.method == 'POST':
        Setting.set('approval_mode', request.form.get('approval_mode', 'manual'))
        Setting.set('groq_api_key', request.form.get('groq_api_key', ''))
        Setting.set('free_trial_days', request.form.get('free_trial_days', '14'))
        flash('Ayarlar kaydedildi.', 'success')
        return redirect(url_for('admin_settings'))

    settings = {
        'approval_mode': Setting.get('approval_mode', 'manual'),
        'groq_api_key': Setting.get('groq_api_key', ''),
        'free_trial_days': Setting.get('free_trial_days', '14'),
    }
    return render_template('admin/settings.html', settings=settings)


# =========================================================================
# ERROR HANDLERS
# =========================================================================
@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, message='Bu sayfaya erişim yetkiniz yok.'), 403


@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, message='Aradığınız sayfa bulunamadı.'), 404


# =========================================================================
# APP STARTUP
# =========================================================================
if __name__ == '__main__':
    from extensions import celery
    init_db(app)
    # Periyodik tarama Celery Beat tarafından yönetiliyor (extensions.py beat_schedule)
    # Celery worker --beat ile birlikte başlatıldığında saatlik kontrol otomatik devreye girer
    print("[App] Celery Beat scheduling aktif. 4 saatte bir standart tarama otomatik başlayacak.")
    # HOTFIX 1.36: macOS AirPlay Receiver port 5000'i kapatmaya zorlamamak için
    # varsayılan 5005'e taşındı. PORT env ile override edilebilir.
    _port = int(os.environ.get("PORT", "5005"))
    app.run(debug=True, host='0.0.0.0', port=_port)

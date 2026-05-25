"""
blueprints/ai_consultant.py — YZ Strateji Danışmanı (Kurumsal plan).

Rotalar:
    GET  /ai-consultant
    GET  /ai-consultant/report/<id>             — standalone tam ekran
    GET  /analysis/<id>/download-pdf            — strategy_pdf.html (html2pdf.js)
    POST /ai-consultant/generate                — Groq Llama-3.3-70b çağrısı
"""
import json
import logging
import os
import re

import requests
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
)
from flask_login import login_required, current_user
from sqlalchemy import func as sqlfunc

from extensions import db
from models import (
    AiReport, TrackedProduct, KeywordTracker, KeywordPool, Setting, get_tr_now,
)
from utils.analytics import extract_review_insights_from_jobs

log = logging.getLogger(__name__)

bp = Blueprint('ai_consultant', __name__)


@bp.route('/ai-consultant', methods=['GET'])
@login_required
def ai_consultant():
    """HOTFIX 1.45 — Geçmiş rapor arşivi + grup filtresi için template'e iletilir."""
    all_reports = []
    tracked_groups = []
    if current_user.has_enterprise_access:
        all_reports = (AiReport.query.filter_by(user_id=current_user.id)
                       .order_by(AiReport.created_at.desc()).all())

        # HOTFIX 1.95: Kullanıcının aktif takip grupları — group_label ÖNCELİKLİ
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

        for g in groups_raw:
            label = base_labels.get(g.group_id)
            if not label:
                label = (g.rep_name or '').strip() or (g.group_id[:12] + '…')
            tracked_groups.append({'id': g.group_id, 'name': label[:70], 'cnt': g.cnt})

    latest_report = all_reports[0] if all_reports else None
    return render_template(
        'ai_consultant.html',
        latest_report=latest_report,
        all_reports=all_reports,
        tracked_groups=tracked_groups,
    )


@bp.route('/ai-consultant/report/<int:report_id>')
@login_required
def ai_consultant_report_standalone(report_id):
    """HOTFIX 1.74: Standalone rapor görüntüleyici (sidebar yok, tam ekran)."""
    if not current_user.has_enterprise_access:
        flash('Bu özellik Kurumsal plana özeldir.', 'warning')
        return redirect(url_for('plans.user_plans'))

    report = AiReport.query.filter_by(id=report_id, user_id=current_user.id).first()
    if not report:
        flash('Rapor bulunamadı.', 'error')
        return redirect(url_for('ai_consultant.ai_consultant'))

    return render_template('ai_report_standalone.html', report=report)


@bp.route('/analysis/<int:report_id>/download-pdf')
@login_required
def download_strategy_pdf(report_id):
    """HOTFIX 1.94: PDF üretimi tarayıcıya devredildi (html2pdf.js CDN)."""
    if not current_user.has_enterprise_access:
        flash('Bu özellik Kurumsal plana özeldir.', 'warning')
        return redirect(url_for('plans.user_plans'))

    report = AiReport.query.filter_by(id=report_id, user_id=current_user.id).first()
    if not report:
        flash('Rapor bulunamadı.', 'error')
        return redirect(url_for('ai_consultant.ai_consultant'))

    # Markdown → HTML (sunucu tarafı)
    try:
        import markdown as _md
        report_html = _md.markdown(
            report.content or '',
            extensions=['extra', 'sane_lists', 'nl2br', 'tables'],
        )
    except Exception:
        from markupsafe import escape
        raw = (report.content or '').strip()
        report_html = '<p>' + escape(raw).replace('\n\n', '</p><p>').replace('\n', '<br>') + '</p>'

    date_str = report.created_at.strftime('%Y%m%d-%H%M')
    slug_src = (report.group_name or 'genel-strateji').lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug_src).strip('-')[:40] or 'rapor'
    pdf_filename = f'BMK-Strateji-{slug}-{date_str}.pdf'

    return render_template(
        'strategy_pdf.html',
        report=report,
        report_html=report_html,
        pdf_filename=pdf_filename,
    )


def _seo_status_for_url(user_id, url):
    """Bir ürün URL'si için en iyi (en düşük overall_rank) SEO sırasını döndür."""
    if not url:
        return {'status': 'yok'}
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
    if not best:
        kt_rows = KeywordTracker.query.filter_by(
            user_id=user_id, target_url=url, is_active=True
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


@bp.route('/ai-consultant/generate', methods=['POST'])
@login_required
def generate_ai_consultant():
    """FAZ 3.2 / HOTFIX 1.45 — Veri güdümlü YZ Strateji Danışmanı."""
    if not current_user.has_enterprise_access:
        flash('Bu özellik Kurumsal plana özeldir.', 'warning')
        return redirect(url_for('ai_consultant.ai_consultant'))

    selected_group_id = (request.form.get('group_id') or '').strip() or None
    custom_prompt_raw = (request.form.get('custom_prompt') or '').strip()

    # ── 1) Veri toplama ─────────────────────────────────────────────────────
    product_query = TrackedProduct.query.filter(
        TrackedProduct.user_id == current_user.id,
        TrackedProduct.is_active == True,
    )
    if selected_group_id:
        product_query = product_query.filter(TrackedProduct.group_id == selected_group_id)
    all_products = product_query.all()

    if not all_products:
        flash('Danışmanlık raporu üretebilmek için en az 1 ürün takip ediyor olmalısınız.', 'warning')
        return redirect(url_for('ai_consultant.ai_consultant'))

    groups = {}
    for p in all_products:
        gkey = p.group_id if p.group_id else f"_solo_{p.id}"
        groups.setdefault(gkey, []).append(p)

    all_urls = [p.url for p in all_products if p.url]
    review_insights = extract_review_insights_from_jobs(current_user.id, all_urls)

    portfolio = []
    has_cost_data = False
    skipped_no_cost = 0

    for gkey, members in groups.items():
        members.sort(key=lambda x: x.created_at or get_tr_now())

        cost_candidates = [m for m in members if m.unit_cost and m.unit_cost > 0]
        if cost_candidates:
            base = cost_candidates[0]
        else:
            flagged = [m for m in members if getattr(m, 'is_base_product', False)]
            base = flagged[0] if flagged else members[0]

        competitors = [m for m in members
                       if m.id != base.id and m.current_price and m.current_price > 0]

        comp_prices = [float(c.current_price) for c in competitors]
        min_comp_price = min(comp_prices) if comp_prices else None
        avg_comp_price = round(sum(comp_prices) / len(comp_prices), 2) if comp_prices else None

        # Kalite (puan) verileri
        base_rating = float(base.rating) if getattr(base, 'rating', None) else None
        base_review_count = int(base.review_count or 0)

        min_comp_rating = None
        min_comp_review_count = 0
        if competitors:
            min_comp_obj = min(competitors, key=lambda c: c.current_price)
            min_comp_rating = float(min_comp_obj.rating) if getattr(min_comp_obj, 'rating', None) else None
            min_comp_review_count = int(min_comp_obj.review_count or 0)

        comp_ratings = [float(c.rating) for c in competitors if c.rating and c.rating > 0]
        avg_comp_rating = round(sum(comp_ratings) / len(comp_ratings), 2) if comp_ratings else None

        unit_cost = float(base.unit_cost) if base.unit_cost and base.unit_cost > 0 else None
        current_price = float(base.current_price) if base.current_price and base.current_price > 0 else None

        if unit_cost is None:
            skipped_no_cost += 1
            continue
        has_cost_data = True

        net_profit_now = (current_price - unit_cost) if (current_price and unit_cost) else None
        margin_pct_now = (round((net_profit_now / current_price) * 100, 2)
                          if (net_profit_now is not None and current_price) else None)
        delta_vs_min_comp = round(current_price - min_comp_price, 2) if (current_price and min_comp_price) else None
        cost_vs_min_comp = round(min_comp_price - unit_cost, 2) if (unit_cost is not None and min_comp_price is not None) else None

        name = (base.product_name or base.url or 'Ürün')
        if len(name) > 110:
            name = name[:110] + '...'

        # Yorum içgörüleri
        my_insight = review_insights.get(base.url, {}) if base.url else {}
        comp_insight = {}
        if competitors:
            agg_praises, agg_complaints, agg_generals = [], [], []
            for c in competitors:
                ci = review_insights.get(c.url)
                if not ci:
                    continue
                agg_praises.extend(ci.get("praises", []))
                agg_complaints.extend(ci.get("complaints", []))
                if ci.get("general"):
                    agg_generals.append(ci["general"])

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

        # SEO istihbaratı
        my_seo = _seo_status_for_url(current_user.id, base.url)
        comp_seo = [{'url_short': (c.url or '')[-50:],
                     'seo': _seo_status_for_url(current_user.id, c.url)}
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
            'benim_puanim': base_rating,
            'benim_yorum_sayim': base_review_count,
            'min_rakip_puani': min_comp_rating,
            'min_rakip_yorum_sayisi': min_comp_review_count,
            'rakip_ortalama_puani': avg_comp_rating,
            'benim_basarili_yonlerim': my_insight.get("praises", []),
            'benim_kritik_sikayetlerim': my_insight.get("complaints", []),
            'benim_genel_kanim': my_insight.get("general", ""),
            'rakip_basarili_yonleri': comp_insight.get("praises", []),
            'rakip_kritik_sikayetleri': comp_insight.get("complaints", []),
            'rakip_genel_kanisi': comp_insight.get("general", ""),
            'benim_seo_durumum': my_seo,
            'rakip_seo_durumlari': comp_seo,
        })

    if not has_cost_data:
        flash(
            'YZ Danışman, anlamlı tavsiye üretebilmek için en az bir ürünün BİRİM MALİYET '
            '(unit_cost) bilgisine ihtiyaç duyar. Lütfen Fiyat Takibi sayfasından ürün kartındaki '
            '💰 butonu ile maliyet ekleyin.', 'warning'
        )
        return redirect(url_for('ai_consultant.ai_consultant'))

    if skipped_no_cost > 0:
        flash(
            f'ℹ️ Maliyet bilgisi eksik {skipped_no_cost} ürün/grup rapora dahil edilmedi. '
            f'Bu ürünlere maliyet eklerseniz bir sonraki raporda analiz edilirler.', 'info'
        )

    # ── 2) API anahtar kontrolü ─────────────────────────────────────────────
    api_key = Setting.get('groq_api_key', '') or os.environ.get('GROQ_API_KEY', '')
    if not api_key:
        flash('Sistemde GROQ API anahtarı tanımlı değil. Lütfen yöneticiyle iletişime geçin.', 'error')
        return redirect(url_for('ai_consultant.ai_consultant'))

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # ── 3) Sektör tespiti ───────────────────────────────────────────────────
    sector = "Genel E-Ticaret"
    try:
        names_only = [{'name': p['urun_adi'], 'platform': p['platform']} for p in portfolio]
        sec_payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": (
                    "Sana verilecek ürün listesine bakarak bu ürünlerin ait olduğu ana e-ticaret sektörünü "
                    "ve alt kategorisini tespit et. Sadece 2-5 kelime ile yaz. (Örn: Evcil Hayvan Bakım Ürünleri, "
                    "Küçük Ev Aletleri, Spor Giyim ve Aksesuar)"
                )},
                {"role": "user", "content": json.dumps(names_only, ensure_ascii=False)},
            ],
            "temperature": 0.2,
        }
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers, json=sec_payload, timeout=15,
        )
        if resp.status_code == 200:
            sector = resp.json()['choices'][0]['message']['content'].strip()
    except Exception:
        log.exception("[YZ Danışman] sektör tespiti")

    # ── 4) CEO Persona promptu ──────────────────────────────────────────────
    system_prompt = (
        "Sen, üst düzey yöneticilere e-ticaret stratejisi sunan milyarlık bir danışmanlık şirketinin "
        "baş analistisin. Müşteriye asla 'şunu analiz ediyorum', 'talimatlara göre yazıyorum' gibi "
        "kendi sürecinden bahsetme. Doğrudan profesyonel, net ve vizyoner tavsiyeler ver. "
        "Soruları veya yönlendirme metinlerini rapora KESİNLİKLE kopyalama.\n\n"
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

    # ── 5) LLM çağrısı + rapor kaydet ───────────────────────────────────────
    try:
        rep_payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload_text},
            ],
            "temperature": 0.45,
            "max_tokens": 4500,
        }
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers, json=rep_payload, timeout=90,
        )
        if resp.status_code == 200:
            content = resp.json()['choices'][0]['message']['content'].strip()

            portfolio_summary_md = "## 📊 Analiz Edilen Portföy Özeti\n\n"
            portfolio_summary_md += "| Ürün | Maliyet | Fiyatım | Min Rakip | Net Kâr | Puanım | Min Rakip Puanı |\n"
            portfolio_summary_md += "|---|---|---|---|---|---|---|\n"
            DASH = "—"
            NOT_ANALYZED = "_Analiz Edilmedi_"
            for p in portfolio:
                cost_s = f"{p['birim_maliyet_tl']:.2f} ₺" if p['birim_maliyet_tl'] is not None else DASH
                price_s = f"{p['guncel_satis_fiyatim_tl']:.2f} ₺" if p['guncel_satis_fiyatim_tl'] is not None else DASH
                comp_s = f"{p['min_rakip_fiyati_tl']:.2f} ₺" if p['min_rakip_fiyati_tl'] is not None else DASH
                profit_s = f"{p['simdiki_net_kar_tl']:.2f} ₺" if p['simdiki_net_kar_tl'] is not None else DASH

                if p.get('benim_puanim'):
                    my_rate_s = f"⭐ {p['benim_puanim']:.1f} ({p['benim_yorum_sayim']} yorum)"
                elif p.get('benim_basarili_yonlerim') or p.get('benim_kritik_sikayetlerim'):
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
        log.exception("[YZ Danışman] Rapor üretilemedi")
        flash(f'Rapor üretilemedi: {str(e)}', 'error')

    return redirect(url_for('ai_consultant.ai_consultant'))

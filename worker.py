"""
BMK Background Worker
Polls the database for pending jobs and executes them using the scraping engine.
"""
import sys
import os
# Ensure the SaaS App directory is in Python's import path
# so that Celery workers can find app.py, models.py, etc.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import json
import logging
import random
import re as _re_hotfix
import traceback
import concurrent.futures
import cloudscraper

from logging_config import setup_logging
setup_logging(app_name='bmk-worker')
log = logging.getLogger(__name__)


# ── HOTFIX 1.34: Asyncio loop kaçış sarmalayıcısı ─────────────────────────────
# Celery worker / Flask asyncio bağlamında `sync_playwright()` doğrudan
# çağrıldığında "Sync API inside the asyncio loop" patlatır. Çözüm:
# fonksiyonu izole bir worker thread'inde çalıştır — thread'in kendi event
# loop'u yoktur, sync_playwright sorunsuz açılır. Subprocess'ten daha hızlı,
# nest_asyncio gibi global patch yapmaz (yan etki sıfır).
def _run_in_isolated_thread(fn, *args, **kwargs):
    """Run a sync function in a fresh worker thread (escapes asyncio event loop)."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(fn, *args, **kwargs).result()


# ── HOTFIX 1.35: Trendyol mağaza puanı (seller rating) helper ─────────────────
# Trendyol satıcı puanı bilgisi seller-store/header-information XHR'ından
# (apigw.trendyol.com) gelir. Ürün URL'sindeki ?merchantId=NNNN ile direkt
# bu endpoint'e istek atıyoruz. 0-10 ölçeğinde puan dönüyor (örn 9.1).
def _fetch_trendyol_seller_rating(merchant_id):
    """Returns (score: float|None, name: str|None) for given merchantId."""
    if not merchant_id or not str(merchant_id).isdigit():
        return None, None
    api_url = (
        f"https://apigw.trendyol.com/discovery-storefront-trproductgw-service"
        f"/api/seller-store/{merchant_id}/header-information?channelId=1"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9",
        "Origin": "https://www.trendyol.com",
        "Referer": "https://www.trendyol.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    try:
        # Önce curl_cffi (Chrome JA3 taklidi)
        try:
            from curl_cffi import requests as cffi_requests
            r = cffi_requests.get(api_url, headers=headers, impersonate="chrome110", timeout=10)
            if r.status_code != 200:
                r = None
        except Exception:
            r = None
        # Düşmezse cloudscraper
        if r is None or getattr(r, "status_code", 0) != 200:
            sc = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'})
            r = sc.get(api_url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None, None
        body = r.json()
        if not isinstance(body, dict):
            return None, None
        result = body.get("result") or body
        if not isinstance(result, dict):
            return None, None
        score = result.get("score") or result.get("sellerScore") or result.get("rating")
        name = result.get("name") or result.get("sellerName") or result.get("storeName")
        try:
            score_val = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_val = None
        if isinstance(name, str):
            name = name.strip() or None
        return score_val, name
    except Exception as e:
        log.info(f"[SellerRating] {merchant_id} hata: {e}")
        return None, None


def _extract_merchant_id_from_url(url):
    """Trendyol URL'inden ?merchantId=NNNN parametresini çıkarır."""
    if not url:
        return None
    try:
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        m_list = qs.get("merchantId") or qs.get("merchantid") or []
        if m_list and str(m_list[0]).isdigit():
            return str(m_list[0])
    except Exception:
        pass
    return None


# ── HOTFIX 1.35: Dinamik Tema (Light/Dark) Stylesheet + Switcher ──────────────
# Tüm rapor şablonları (price/review/combined) aynı stili kullanıyor. Dark
# default; light aktifleşince [data-theme="light"] override eder.
# Tema kaynakları (öncelik sırası):
#   1. URL ?theme=light|dark
#   2. parent window postMessage({type:"theme",theme:"..."}) — iframe senaryosu
#   3. parent.document.documentElement[data-theme] (same-origin iframe)
#   4. localStorage.theme
#   5. prefers-color-scheme
def _report_theme_block():
    return r"""
    <style>
    /* HOTFIX 1.38: Dark default + Light override — tam kontrast garantili */
    :root {
        --title: #a78bfa;            /* mor başlık */
        --title-strong: #c4b5fd;     /* daha parlak mor (vurgu) */
        --text: #f8fafc;             /* ana metin */
        --text-strong: #ffffff;      /* en parlak (sayı/önemli) */
        --text-soft: #cbd5e1;        /* alt metin */
        --muted: #94a3b8;            /* sönük metin */
        --bg: #09090b;               /* sayfa arka planı */
        --bg-card: rgba(255,255,255,0.02);
        --bg-strong: #1e293b;        /* progress bar / kapalı kutu */
        --border: rgba(255,255,255,0.08);
        --border-strong: rgba(255,255,255,0.16);
        --grad: linear-gradient(135deg,rgba(139,92,246,0.08),rgba(99,102,241,0.08));
        --ai-bg: rgba(139,92,246,0.06);
        --ai-border: rgba(139,92,246,0.15);
        --link-bg: rgba(139,92,246,0.12);
        --report-color: #64748b;
        --success: #10b981;
        --success-bg: rgba(16,185,129,0.12);
        --danger: #ef4444;
        --danger-bg: rgba(239,68,68,0.12);
        --warn: #fbbf24;             /* amber-400 — sarı uyarı (koyu tema) */
        --warn-bg: rgba(251,191,36,0.15);
        --info: #818cf8;
        --info-soft: #9ca3af;
    }
    [data-theme="light"] {
        --title: #6d28d9;            /* WCAG AA — beyaz üstünde 5.4:1 */
        --title-strong: #5b21b6;
        --text: #0f172a;             /* slate-900 — tam siyah-koyu */
        --text-strong: #000000;
        --text-soft: #334155;        /* slate-700 */
        --muted: #475569;            /* slate-600 — eski 64748b'den daha koyu */
        --bg: #f8fafc;               /* slate-50 zemin */
        --bg-card: #ffffff;          /* tam beyaz kart */
        --bg-strong: #e2e8f0;        /* progress bar açık zemin */
        --border: #e5e7eb;           /* gray-200 */
        --border-strong: #cbd5e1;    /* slate-300 */
        --grad: linear-gradient(135deg,rgba(139,92,246,0.10),rgba(99,102,241,0.06));
        --ai-bg: #ffffff;            /* AI kutu beyaz */
        --ai-border: #c4b5fd;        /* mor-soft border */
        --link-bg: rgba(109,40,217,0.10);
        --report-color: #64748b;
        --success: #047857;          /* emerald-800 — WCAG AA on white (5.7:1) */
        --success-bg: rgba(5,150,105,0.10);
        --danger: #b91c1c;           /* red-800 — WCAG AA on white (7.3:1) */
        --danger-bg: rgba(185,28,28,0.10);
        --warn: #b45309;             /* amber-700 — WCAG AA on white (5.1:1) */
        --warn-bg: rgba(180,83,9,0.12);
        --info: #4338ca;             /* daha koyu indigo */
        --info-soft: #6b7280;        /* gray-500 */
    }
    html, body {
        background: var(--bg) !important;
        color: var(--text) !important;
        transition: background 0.2s, color 0.2s;
    }
    body { font-family:'Plus Jakarta Sans',sans-serif; padding:40px; margin:0; }
    .container { max-width:1100px; margin:auto; }
    /* Light mode'da parlak rozet ailesi yumuşak kontrastı korusun */
    [data-theme="light"] .ai-card,
    [data-theme="light"] .grad-card {
        background: var(--bg-card) !important;
        border: 1px solid var(--border) !important;
        box-shadow: 0 4px 12px -4px rgba(15,23,42,0.06);
    }
    </style>
    <script>
    (function(){
        // 1. URL ?theme=
        try {
            var p = new URLSearchParams(window.location.search);
            var t = p.get('theme');
            if (t === 'light' || t === 'dark') {
                document.documentElement.setAttribute('data-theme', t);
                return;
            }
        } catch(e){}
        // 2. parent window same-origin iframe
        try {
            if (window.parent && window.parent !== window && window.parent.document) {
                var pt = window.parent.document.documentElement.getAttribute('data-theme');
                if (pt === 'light' || pt === 'dark') {
                    document.documentElement.setAttribute('data-theme', pt);
                    return;
                }
            }
        } catch(e){}
        // 3. localStorage (kendi origin'inde)
        try {
            var ls = localStorage.getItem('theme');
            if (ls === 'light' || ls === 'dark') {
                document.documentElement.setAttribute('data-theme', ls);
                return;
            }
        } catch(e){}
        // 4. prefers-color-scheme
        try {
            if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
                document.documentElement.setAttribute('data-theme','light');
                return;
            }
        } catch(e){}
        // Default: dark (data-theme set etmiyoruz)
    })();
    // 5. iframe için parent window postMessage dinleyici
    window.addEventListener('message', function(ev) {
        try {
            var d = ev.data || {};
            if (d.type === 'theme' && (d.theme === 'light' || d.theme === 'dark')) {
                document.documentElement.setAttribute('data-theme', d.theme);
            }
        } catch(e){}
    });
    </script>
    """
from bs4 import BeautifulSoup
from datetime import datetime, timedelta


from extensions import celery

# ── Services modüllerinden geri uyumluluk için public + alias'lı re-export ──
# (Worker.py'nin iç çağrıları hâlâ _rand_profile, _build_browser_headers,
#  _get_proxy_*, _is_blocked_response, _resolve_groq_key, classify_notification*
#  isimlerini kullanıyor — yeni modüllerden import edip eski alias'larla expose ediyoruz.)
from services.scraping.browser import (
    UA_PROFILES, UA_POOL,
    rand_profile as _rand_profile,
    rand_ua as _rand_ua,
    build_browser_headers as _build_browser_headers,
)
from services.scraping.proxy import (
    get_proxy_url as _get_proxy_url,
    get_proxy_for_requests as _get_proxy_for_requests,
    get_proxy_for_playwright as _get_proxy_for_playwright,
)
from services.scraping.blocked import is_blocked_response as _is_blocked_response
from services.ai.groq import resolve_groq_key as _resolve_groq_key
from services.notifications.classifier import (
    NOTIFICATION_CATEGORIES,
    classify_notification_rule,
    classify_notification_ai,
    classify_notification,
)


worker_state = {
    'is_active': False,
    'status_text': 'Celery / APScheduler ile yönetiliyor.'
}

#
# ── Anti-Deadlock: Hard timeouts ──
# Playwright/HB sonsuza girerse worker 20dk sonra acımasızca öldürülür,
# Celery prefork parent yeni çocuk spawn eder. Queue asla kilitli kalmaz.
@celery.task(
    name='worker.check_tracked_products_task',
    time_limit=1200,         # 20dk hard kill (SIGKILL)
    soft_time_limit=1100,    # 18.3dk'da SoftTimeLimitExceeded fırlatılır
)
def check_tracked_products_task():
    from celery.exceptions import SoftTimeLimitExceeded
    try:
        from app import app
        from models import init_db
        with app.app_context():
            init_db(app)  # DB migration güvenliği — yeni sütunlar otomatik eklenir
            check_tracked_products(app)
    except SoftTimeLimitExceeded:
        log.info("[Worker] ⏱️ Soft time limit aşıldı — görev temiz şekilde sonlandırılıyor.")
        return "soft_timeout"
    except Exception as e:
        log.info(f"[Worker] check_tracked_products_task error: {type(e).__name__}: {e}")
        return "error"

@celery.task(
    name='worker.process_job_task',
    time_limit=1800,         # Tekil job için 30dk hard kill
    soft_time_limit=1700,
)
def process_job_task(job_id):
    from celery.exceptions import SoftTimeLimitExceeded
    try:
        from app import app
        with app.app_context():
            _process_job_by_id(job_id)
    except SoftTimeLimitExceeded:
        log.info(f"[Worker] ⏱️ Job {job_id} soft time limit — sonlandırılıyor.")
        return "soft_timeout"

@celery.task(
    name='worker.check_single_product_task',
    time_limit=600,          # Tekil ürün kontrolü max 10dk
    soft_time_limit=540,
)
def check_single_product_task(product_id):
    try:
        from app import app
        from models import init_db
        with app.app_context():
            init_db(app)  # DB migration güvenliği
            from models import db, TrackedProduct
            product = TrackedProduct.query.get(product_id)
            if product and product.is_active:
                check_tracked_products(app, product_ids=[product_id])
    except Exception as e:
        log.info(f"[Worker] check_single_product_task crash: {e}")
        # HOTFIX 1.25: Sadece zaman damgası güncelle, "Hata" placeholder'ı yazma.
        # Eski veri korunur; sonraki turda yeniden denenecek.
        try:
            with app.app_context():
                from models import db, TrackedProduct, get_tr_now
                product = TrackedProduct.query.get(product_id)
                if product:
                    product.last_checked = get_tr_now()
                    if not product.product_name:
                        product.product_name = "Güncelleme Bekleniyor"
                    if not product.platform_name:
                        product.platform_name = "Bilinmiyor"
                    db.session.commit()
        except Exception:
            pass


# ── HOTFIX 1.42: Worker Açılışında Otomatik Catch-up Tetikleyici ─────────────
# Sistem kapalıyken geçen sürede birikmiş "60dk+ gecikmiş" ürünleri worker
# ayağa kalkar kalkmaz tarar. macOS uyku/kapanma sonrası ilk fiyatları
# saatin başını beklemeden öğreniriz.
try:
    from celery.signals import worker_ready

    @worker_ready.connect
    def _on_worker_ready_catchup(sender=None, **kwargs):
        """Worker hazır olur olmaz tracked-products taramasını ASINKRON tetikle.
        delay() = .apply_async() — task queue'ya yollanır, worker hemen alır."""
        # Konsola net başlangıç banner'ı — kullanıcı worker'ın gerçekten
        # ayağa kalktığını tek bakışta görsün. print kasıtlı (başlangıç UX'i).
        print("\n" + "=" * 60)
        print("  ⚙️  BMK CELERY WORKER HAZIR — işleri dinliyor")
        print("     Analiz/tarama istekleri artık işlenecek.")
        print("     Adımlar bu konsola canlı akacak (Ctrl+C ile durdur).")
        print("=" * 60 + "\n", flush=True)
        try:
            log.info("[Worker] 🟢 worker_ready — ilk catch-up taraması (asenkron) tetikleniyor...")
            check_tracked_products_task.apply_async(countdown=5)  # 5s gecikme: worker stabilize olsun
            log.info("[Worker] ✅ Catch-up task kuyruğa alındı (5s gecikme).")
        except Exception as ready_err:
            log.info(f"[Worker] ⚠️ worker_ready catch-up tetikleme hata: {ready_err}")
except Exception as _signal_import_err:
    # Celery signals import edilemezse sessizce devam et — beat zaten saatlik tetikliyor
    log.info(f"[Worker] ℹ️ worker_ready signal import edilemedi ({_signal_import_err}); "
          f"saatlik beat schedule normal çalışmaya devam eder.")


def check_tracked_products(app, product_ids=None):
    """Periodically check prices and stock of tracked products and generate notifications.
    
    Args:
        app: Flask application instance
        product_ids: Opsiyonel — Sadece belirli ürünleri kontrol et (yeni eklenen ürünler için).
                     None ise saatlik periyodik taramayı çalıştırır.
    """
    from models import db, TrackedProduct, PriceHistory, Notification, PriceAlert, get_tr_now
    
    if product_ids:
        # Belirli ürünleri anında kontrol et (yeni eklenenler — süre filtresi uygulanmaz)
        # HOTFIX 1.24: is_price_tracked == True filtresi eklendi — hayalet kayıtlar döngüye girmiyor.
        products = TrackedProduct.query.filter(
            TrackedProduct.id.in_(product_ids),
            TrackedProduct.is_active == True,
            TrackedProduct.is_price_tracked == True
        ).all()
    else:
        # ── HOTFIX 1.42 / 1.72: Akıllı Catch-up + Acil Tarama Kuyruğu ─────────────
        # Mevcut filtre: last_checked NULL veya 240dk (4 saat)'dan eski olan ürünler.
        # Eşik beat periyodu (HOTFIX 1.72 → 4 saat) ile uyumlu.
        # Bunları "Acil" (>240dk gecikmiş) ve "Normal" diye sınıflıyoruz,
        # acilleri ÖNCE tarayalım — sistem kapalıyken birikmiş olabilirler.
        now_ts = get_tr_now()
        threshold = now_ts - timedelta(minutes=240)   # 4 saat = 240 dk

        # ── HOTFIX 1.91: Dormant filter ──
        # GlobalProduct'ı dormant=True olan TP'leri ATLA (kimse takip etmiyor).
        # `outerjoin` ile global_product_id NULL olan eski kayıtlar da dahil edilir.
        from models import GlobalProduct
        products = (TrackedProduct.query
                    .outerjoin(GlobalProduct,
                               TrackedProduct.global_product_id == GlobalProduct.id)
                    .filter(
                        TrackedProduct.is_active == True,
                        TrackedProduct.is_price_tracked == True,
                        # GP yoksa (eski kayıt) veya GP dormant DEĞİL
                        (GlobalProduct.id.is_(None)) | (GlobalProduct.is_dormant == False),
                        (TrackedProduct.last_checked == None) |
                        (TrackedProduct.last_checked < threshold)
                    ).all())

        # Acil kuyruğu — last_checked NULL ya da 240dk+ gecikmiş. Aciller en başa.
        urgent = []
        normal = []
        for p in products:
            if p.last_checked is None:
                urgent.append(p)
            else:
                lag_min = (now_ts - p.last_checked).total_seconds() / 60.0
                if lag_min > 240:
                    urgent.append(p)
                else:
                    normal.append(p)
        if urgent:
            log.info(f"[Worker] 🚨 Catch-up: {len(urgent)} ürün 240dk+ gecikmiş — acil kuyruğa alındı.")
            for p in urgent[:5]:
                if p.last_checked:
                    lag_min = int((now_ts - p.last_checked).total_seconds() / 60.0)
                    log.info(f"[Worker]   • p{p.id} ({p.platform_name or '?'}) — {lag_min}dk gecikmiş")
                else:
                    log.info(f"[Worker]   • p{p.id} ({p.platform_name or '?'}) — hiç taranmamış")
            if len(urgent) > 5:
                log.info(f"[Worker]   ... ve {len(urgent) - 5} ürün daha.")
        # Aciller önce, normaller sonra — döngü sırası önemli (ilk taraması bitenler hızlı yanıt alır)
        products = urgent + normal

    if not products:
        return

    log.info(f"[Worker] Checking prices & stock for {len(products)} tracked products...")
    fiyati_temizle, standard_fiyat_formati, _, marka_adi_bul, _, _ = _import_bmk_utils()
    
    # ── FAZ 2.1: PLAYWRIGHT TEMİZLİĞİ ────────────────────────────────────────
    # Passive price tracker artık ASLA browser başlatmaz.
    # Sadece hafif HTTP kazıma: cloudscraper (BS4) + curl_cffi (HB TLS taklidi).
    # Stok bulunamazsa -1 bırakılır; ağır Playwright/Selenium fallback YOK.
    # (Analiz işleri için `run_price_headless` ayrı bir fonksiyondur — dokunulmadı.)
    try:
        for product in products:
            try:
                fiyat_str = None
                yeni_stok = -1  # FAZ 2.1: başlangıç -1, fallback 10 YOK
                urun_ismi = "İsim Bulunamadı"
                # FAZ 4: rating + review_count (None → bulunamadı, mevcut değeri koru)
                yeni_rating = None
                yeni_review_count = None
                # FAZ 3: Yeni marketplace branş'ları
                _u = (product.url or "").lower()
                is_hepsiburada = "hepsiburada.com" in _u
                is_n11         = "n11.com" in _u
                is_ciceksepeti = "ciceksepeti.com" in _u
                is_pttavm      = "pttavm.com" in _u

                # FAZ 3: N11
                if is_n11:
                    try:
                        n11_data = _scrape_n11(product.url)
                        if n11_data:
                            if n11_data.get("price"):
                                fiyat_str = str(n11_data["price"])
                            if n11_data.get("name"):
                                urun_ismi = n11_data["name"]
                            if n11_data.get("rating") is not None:
                                yeni_rating = n11_data["rating"]
                            if n11_data.get("review_count") is not None:
                                yeni_review_count = n11_data["review_count"]
                            log.info(f"[Worker] N11 (lightweight) p{product.id} — price: {fiyat_str} | rating: {yeni_rating} ({yeni_review_count})")
                    except Exception as e:
                        log.info(f"[Worker] N11 scrape failed for {product.id}: {e}")

                # FAZ 3: Çiçeksepeti
                elif is_ciceksepeti:
                    try:
                        cs_data = _scrape_ciceksepeti(product.url)
                        if cs_data:
                            if cs_data.get("price"):
                                fiyat_str = str(cs_data["price"])
                            if cs_data.get("name"):
                                urun_ismi = cs_data["name"]
                            if cs_data.get("rating") is not None:
                                yeni_rating = cs_data["rating"]
                            if cs_data.get("review_count") is not None:
                                yeni_review_count = cs_data["review_count"]
                            log.info(f"[Worker] Çiçeksepeti (lightweight) p{product.id} — price: {fiyat_str} | rating: {yeni_rating} ({yeni_review_count})")
                    except Exception as e:
                        log.info(f"[Worker] Çiçeksepeti scrape failed for {product.id}: {e}")

                # FAZ 3: PttAVM
                elif is_pttavm:
                    try:
                        ptt_data = _scrape_pttavm(product.url)
                        if ptt_data:
                            if ptt_data.get("price"):
                                fiyat_str = str(ptt_data["price"])
                            if ptt_data.get("name"):
                                urun_ismi = ptt_data["name"]
                            if ptt_data.get("rating") is not None:
                                yeni_rating = ptt_data["rating"]
                            if ptt_data.get("review_count") is not None:
                                yeni_review_count = ptt_data["review_count"]
                            log.info(f"[Worker] PttAVM (lightweight) p{product.id} — price: {fiyat_str} | rating: {yeni_rating} ({yeni_review_count})")
                    except Exception as e:
                        log.info(f"[Worker] PttAVM scrape failed for {product.id}: {e}")

                elif is_hepsiburada:
                    # HB: Chrome TLS taklidi (curl_cffi) — browser açmaz, saf HTTP
                    try:
                        cffi_data = _scrape_hepsiburada_cffi(product.url, fetch_reviews=False)
                        if cffi_data:
                            if cffi_data.get("price"):
                                fiyat_str = str(cffi_data["price"])
                            if cffi_data.get("name"):
                                urun_ismi = cffi_data["name"]
                            # FAZ 4: yorum/puan
                            if cffi_data.get("rating") is not None:
                                yeni_rating = cffi_data["rating"]
                            if cffi_data.get("review_count") is not None:
                                yeni_review_count = cffi_data["review_count"]
                            log.info(f"[Worker] HB cffi (lightweight) p{product.id} — price: {fiyat_str} | rating: {yeni_rating} ({yeni_review_count})")
                    except Exception as cffi_e:
                        log.info(f"[Worker] HB cffi failed for {product.id}: {cffi_e}")

                    # HB API JSON fallback (hâlâ hafif, browser yok)
                    if not fiyat_str or fiyat_str == "Bulunamadı":
                        try:
                            hb_data = _hepsiburada_api_fallback(product.url)
                            if hb_data:
                                if hb_data.get("price"):
                                    fiyat_str = str(hb_data["price"])
                                if hb_data.get("name") and urun_ismi == "İsim Bulunamadı":
                                    urun_ismi = hb_data["name"]
                                log.info(f"[Worker] HB API fallback p{product.id} — price: {fiyat_str}")
                        except Exception as api_err:
                            log.info(f"[Worker] HB API fallback failed for {product.id}: {api_err}")
                else:
                    # Trendyol vd.: cloudscraper + BS4 — browser açmaz
                    # HOTFIX 1.25: rotating UA + 403/429/CAPTCHA tespiti.
                    # HOTFIX 1.32: Sec-Ch-Ua tutarlı header seti + opsiyonel residential proxy.
                    # Bloklanma durumunda eski fiyat/isim KORUNUR — DB'ye çöp/"Hata" yazılmaz.
                    try:
                        scraper = cloudscraper.create_scraper(
                            browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'}
                        )
                        cs_headers, cs_profile = _build_browser_headers(referer="https://www.trendyol.com/")
                        proxies_cfg = _get_proxy_for_requests()
                        if proxies_cfg:
                            scraper.proxies.update(proxies_cfg)
                            log.info(f"[Worker] 🛡️ Proxy aktif (UA={cs_profile['ua'][:40]}…) p{product.id}")
                        resp = scraper.get(
                            product.url,
                            timeout=25,
                            headers=cs_headers,
                        )
                        # geriye uyum log etiketi için
                        ua_for_req = cs_profile["ua"]
                        if _is_blocked_response(resp):
                            log.info(f"[Worker] ⛔ Bot/Limit (status={getattr(resp,'status_code','?')}) — p{product.id} URL bloklandı, eski veri korunuyor.")
                            # Bu turu atla — hiçbir şey yazma; outer except'e gitmeden continue.
                            product.last_checked = get_tr_now()
                            db.session.commit()
                            continue
                        if resp.status_code == 200:
                            data = extract_data_from_html(resp.text, product.url)
                            if data.get("price") and data["price"] != "Bulunamadı":
                                fiyat_str = data["price"]
                            if data.get("name") and data["name"] != "İsim Bulunamadı":
                                urun_ismi = data["name"]
                            # FAZ 4: yorum/puan
                            if data.get("rating") is not None:
                                yeni_rating = data["rating"]
                            if data.get("review_count") is not None:
                                yeni_review_count = data["review_count"]
                            log.info(f"[Worker] Cloudscraper (lightweight) p{product.id} ua=[{ua_for_req[:30]}…] — price: {fiyat_str} | rating: {yeni_rating} ({yeni_review_count})")
                    except Exception as cs_err:
                        log.info(f"[Worker] Cloudscraper failed for {product.id}: {cs_err}")

                # FAZ 2.1 / HOTFIX 1.41: Hafif kazıma çuvallarsa ARTIK pes etmiyoruz.
                # Playwright + stealth ağır fallback'i devreye giriyor (tek URL, ~5-8s).
                if not fiyat_str or fiyat_str == "Bulunamadı" or not urun_ismi or urun_ismi == "İsim Bulunamadı":
                    log.info(f"[Worker] 🪂 Hafif kazıma yetersiz p{product.id} — Playwright ağır fallback tetikleniyor...")
                    try:
                        pw_data = _playwright_single_price(product.url)
                        if pw_data.get('price'):
                            fiyat_str = pw_data['price']
                        if pw_data.get('name') and (not urun_ismi or urun_ismi == "İsim Bulunamadı"):
                            urun_ismi = pw_data['name']
                        log.info(f"[Worker] 🪂 Playwright fallback p{product.id} — price: {fiyat_str} | name: {(urun_ismi or '')[:40]}")
                    except Exception as pw_err:
                        log.info(f"[Worker] Playwright fallback exception p{product.id}: {pw_err}")

                    # Hâlâ veri yoksa — eski davranış: sahte yazma, sadece geç.
                    # Ama bu sefer last_checked'i güncelliyoruz ki ürün ısrarla
                    # baştaki sıraya takılı kalmasın.
                    if not fiyat_str or fiyat_str == "Bulunamadı" or not urun_ismi or urun_ismi == "İsim Bulunamadı":
                        product.last_checked = get_tr_now()
                        db.session.commit()
                        raise Exception("Hem hafif hem ağır kazıma fiyat alamadı — bu tur atlandı, eski veri korunuyor.")

                # FAZ 2.1: yeni_stok -1 olarak bırakılır. ASLA 10'a yükseltme.
                # _is_radar=False olduğu için stok bloğu zaten yazılmıyor; current_stock dokunulmaz.

                # ── DB UPDATE (price only) ──
                yeni_fiyat = fiyati_temizle(fiyat_str)
                if not product.product_name and urun_ismi:
                    product.product_name = urun_ismi
                if not product.platform_name:
                    product.platform_name = marka_adi_bul(product.url)

                if product.current_price != yeni_fiyat:
                    eski_fiyat = product.current_price
                    product.previous_price = eski_fiyat
                    product.current_price = yeni_fiyat
                    history = PriceHistory(product_id=product.id, price=yeni_fiyat)
                    db.session.add(history)

                    # ── FAZ 2.1 / HOTFIX 1.7: ÇİFT YÖNLÜ Akıllı Tetikleyici ──
                    # SADECE yeni şemada (price_below/price_above) çalışır.
                    # Sorgu generic ORM'dur; eski 'target_price_threshold' SÜTUNU SORGULANMAZ.
                    # Şema uyumsuzluğunda hatayı yakalayıp diğer ürünleri etkilemeden geçeriz.
                    try:
                        short_name = (" ".join(product.product_name.split()[:6]) + "...") if product.product_name else (product.platform_name or "Ürün")
                        # ORM mapping HOTFIX 1.7 sonrası price_below/price_above döndürür.
                        # Stale .pyc varsa burada hata verir; alarmı atlarız, fiyat kaydı bozulmaz.
                        active_alerts = PriceAlert.query.filter_by(
                            tracked_product_id=product.id,
                            is_active=True
                        ).all()
                        for alert in active_alerts:
                            triggered = False
                            if alert.price_below is not None and yeni_fiyat <= alert.price_below:
                                msg = (
                                    f"🚨 Fırsat! {short_name} fiyatı "
                                    f"{standard_fiyat_formati(alert.price_below)} altına düştü! "
                                    f"Güncel fiyat: {standard_fiyat_formati(yeni_fiyat)}"
                                )
                                # HOTFIX 1.54: alarm tetiklemesi = fırsat sinyali
                                db.session.add(Notification(
                                    user_id=alert.user_id, message=msg, link=product.url,
                                    category='opportunity'
                                ))
                                triggered = True
                                log.info(f"[PriceAlert] 🚨 Alt limit — p{product.id}, eşik {alert.price_below}, yeni {yeni_fiyat}")
                            if alert.price_above is not None and yeni_fiyat >= alert.price_above:
                                msg = (
                                    f"📈 Fiyat Artışı! {short_name} fiyatı "
                                    f"{standard_fiyat_formati(alert.price_above)} üstüne çıktı! "
                                    f"Güncel fiyat: {standard_fiyat_formati(yeni_fiyat)}"
                                )
                                # HOTFIX 1.54: rakip zammı = tehdit (kullanıcı pazarda
                                # baskı altında — fiyat artışı kullanıcı için olumsuz)
                                db.session.add(Notification(
                                    user_id=alert.user_id, message=msg, link=product.url,
                                    category='threat'
                                ))
                                triggered = True
                                log.info(f"[PriceAlert] 📈 Üst limit — p{product.id}, eşik {alert.price_above}, yeni {yeni_fiyat}")
                            if triggered:
                                alert.is_active = False
                    except Exception as e:
                        log.info(f"[PriceAlert] ⚠️ Kontrol hatası (product {product.id}): {e}")

                    if eski_fiyat > 0:
                        fark = yeni_fiyat - eski_fiyat
                        yon = "arttı" if fark > 0 else "düştü"
                        ikon = "📈" if fark > 0 else "📉"

                        # Ani fiyat kırılması tespiti (%15 ve üzeri değişim)
                        oran = abs(fark / eski_fiyat) * 100
                        is_break = oran >= 15

                        short_name2 = " ".join(product.product_name.split()[:5]) + "..." if product.product_name else product.platform_name
                        # HOTFIX 1.99: Yeni mesaj formatı — yön + ünlü ifadeler
                        oran_signed = (fark / eski_fiyat) * 100  # negatif veya pozitif
                        oran_str = f"%{oran_signed:+.1f}".replace('.', ',')  # %-12,5 veya %+8,3
                        eski_str = standard_fiyat_formati(eski_fiyat)
                        yeni_str = standard_fiyat_formati(yeni_fiyat)
                        if is_break:
                            msg = (f"{ikon} Fiyat Değişti: {short_name2} — Değişim: {oran_str}. "
                                   f"(Eski: {eski_str} ➔ Yeni: {yeni_str}) ⚡ KRİTİK")
                            # Faz 3A: VulnerabilityAlert kaldırıldı — Notification
                            # zaten 'threat'/'opportunity' kategorisiyle aşağıda oluşturuluyor.
                        else:
                            msg = (f"{ikon} Fiyat Değişti: {short_name2} — Değişim: {oran_str}. "
                                   f"(Eski: {eski_str} ➔ Yeni: {yeni_str})")

                        # HOTFIX 1.54: kategori — kritik kırılma=threat, normal=yön
                        if is_break:
                            _cat = 'threat'
                        else:
                            _cat = 'price_up' if fark > 0 else 'price_down'
                        # HOTFIX 1.99: internal_link → fiyat grafiği grup anchor
                        _internal = None
                        if product.group_id:
                            _internal = f'/tracked-products#group-{product.group_id}'
                        else:
                            _internal = '/tracked-products'
                        noti = Notification(
                            user_id=product.user_id, message=msg,
                            link=product.url, internal_link=_internal, category=_cat
                        )
                        db.session.add(noti)

                # Faz 3A: Zafiyet Radarı tamamen kaldırıldı. Eskiden burada
                # stok seviyesi izleme + VulnerabilityAlert üretimi vardı. Stok
                # takibi konsepti resmi API entegrasyonu gelene kadar geri dönmeyecek.

                # FAZ 4: Yorum/Puan kalıcılaştırma — yeni veri varsa override, yoksa eskisini koru
                if yeni_rating is not None:
                    product.rating = yeni_rating
                if yeni_review_count is not None:
                    # 0 dönüşü "yorum yok" olabilir; o yüzden None ile farkı önemli
                    product.review_count = yeni_review_count

                # ── HOTFIX 1.35: Satıcı puanı (mağaza skoru) — Trendyol için ──
                try:
                    if "trendyol.com" in (product.url or "").lower():
                        mid = _extract_merchant_id_from_url(product.url)
                        if mid:
                            score, name = _fetch_trendyol_seller_rating(mid)
                            if score is not None:
                                product.seller_rating = score
                            if name:
                                product.seller_name = name[:100]
                except Exception as sr_err:
                    log.info(f"[Worker] seller_rating fetch hata p{product.id}: {sr_err}")

                # BİREBİR ÜRÜN BAZLI COMMIT (İzolasyon için kritik)
                product.last_checked = get_tr_now()
                if not product.product_name: product.product_name = "Bilinmeyen Ürün"
                if not product.platform_name: product.platform_name = marka_adi_bul(product.url)
                # HOTFIX 1.91: GlobalProduct paylaşımlı state'i güncelle (tek kez)
                try:
                    from models import GlobalProduct
                    if product.global_product_id:
                        gp = GlobalProduct.query.get(product.global_product_id)
                        if gp:
                            gp.current_price = product.current_price or 0.0
                            gp.product_name = product.product_name or gp.product_name
                            gp.platform = gp.platform or product.platform_name
                            gp.rating = product.rating
                            gp.review_count = product.review_count or 0
                            gp.last_checked = product.last_checked
                except Exception as _gp_err:
                    # GP update başarısız olursa ana işlem bozulmasın
                    log.info(f"[Worker] GP state update fail p{product.id}: {_gp_err}")
                db.session.commit()
                log.info(f"[Worker] Product {product.id} successfully updated (lightweight). rating={product.rating} reviews={product.review_count}")

            except Exception as product_err:
                log.info(f"[Worker] Error tracking product {product.id}: {product_err}")
                # HOTFIX 1.25: Sadece Son Kontrol süresini güncelle — DB'ye "Hata" yazma.
                # Eski isim/fiyat/marka korunur; tarih ileri alınır ki loop kısır döngüye girmesin.
                # Yalnızca tablo HİÇ doldurulmamışsa nötr placeholder yazıyoruz (ilk başarısız taramada
                # boş satır kalmasın), bu da grafik için "veri yok" durumunu temsil eder.
                try:
                    product.last_checked = get_tr_now()
                    if not product.product_name:
                        product.product_name = "Güncelleme Bekleniyor"
                    if not product.platform_name:
                        product.platform_name = marka_adi_bul(product.url) or "Bilinmiyor"
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                continue

    except Exception as glb_err:
        log.info("[Worker] Global error in tracking products loop:", glb_err)


def _process_job_by_id(job_id):
    """Pick up and process a specific job via Celery."""
    from models import db, Job, get_tr_now

    job = Job.query.get(job_id)
    if not job or job.status != 'pending':
        return

    log.info(f"[Worker] Processing Job #{job.id} ({job.job_type}) for user #{job.user_id}")

    global worker_state
    worker_state['is_active'] = True
    jt_map = {'price': 'Fiyat Analizi', 'review': 'Yorum Analizi', 'combined': 'Kombine Analiz'}
    worker_state['status_text'] = f"#{job.id} {jt_map.get(job.job_type, '')} işleniyor..."

    job.status = 'running'
    job.started_at = get_tr_now()
    db.session.commit()

    try:
        urls = job.get_urls(filter_metadata=False)
        api_key = job.api_key_used or ''

        if job.job_type == 'price':
            result_html = run_price_headless(urls, api_key)
        elif job.job_type == 'review':
            result_html = run_review_headless(urls, api_key)
        elif job.job_type == 'combined':
            result_html = run_combined_headless(urls, api_key)
        else:
            result_html = "<p>Bilinmeyen analiz türü.</p>"

        job.result_html = result_html
        job.status = 'completed'
        job.completed_at = get_tr_now()
        log.info(f"[Worker] Job #{job.id} completed successfully.")

        try:
            from models import Notification
            type_label = "Fiyat Analizi" if job.job_type == 'price' else ("Yorum Analizi" if job.job_type == 'review' else "Kombine Analiz")
            msg = f"✅ '{type_label}' işleminiz başarıyla tamamlandı!"
            # HOTFIX 1.54: tüm analiz sonuçları "combined" kategorisinde (3 alt tür de
            # kullanıcı için aynı UI tab'ında listelenir)
            noti = Notification(user_id=job.user_id, message=msg, link=f"/job/{job.id}", category='combined')
            db.session.add(noti)
        except Exception as e:
            log.info(f"Failed to create notification for job {job.id}: {str(e)}")

    except Exception as e:
        job.status = 'failed'
        job.completed_at = get_tr_now()
        job.error_message = str(e)
        log.info(f"[Worker] Job #{job.id} failed: {e}")
        traceback.print_exc()

        try:
            from models import Notification
            type_label = "Fiyat Analizi" if job.job_type == 'price' else ("Yorum Analizi" if job.job_type == 'review' else "Kombine Analiz")
            msg = f"❌ '{type_label}' işleminiz başarısız oldu: {str(e)[:50]}"
            # HOTFIX 1.54: başarısız analiz hâlâ "combined" kategorisinde (kullanıcı
            # 'Kombine Analiz' sekmesinde hem başarılı hem başarısız sonuçları görür)
            noti = Notification(user_id=job.user_id, message=msg, link=f"/job/{job.id}", category='combined')
            db.session.add(noti)
        except Exception as noti_e:
            pass

    db.session.commit()
    worker_state['is_active'] = False
    worker_state['status_text'] = 'Hazır ve izlemede.'


# =========================================================================
# HEADLESS SCRAPING FUNCTIONS
# These replicate the logic from bmk_suite.py but without tkinter dependencies
# =========================================================================

import os
import ssl
import re
from urllib.parse import urlparse

# SSL context: use default secure context (no global bypass)


def _import_bmk_utils():
    """Scraper paylaşımlı utility'ler.

    Faz: temizlik — eskiden bmk_suite (tkinter desktop) import edilirdi.
    Şu an services.scraping.parsers'tan geliyor. İsim _import_bmk_utils
    geriye uyumluluk için aynı (worker'da 5+ noktada çağrılıyor).
    """
    from services.scraping.parsers import (
        fiyati_temizle, standard_fiyat_formati, urun_ismi_temizle,
        marka_adi_bul, get_domain, BANNED_UI_PHRASES,
    )
    return fiyati_temizle, standard_fiyat_formati, urun_ismi_temizle, marka_adi_bul, get_domain, BANNED_UI_PHRASES




def _hepsiburada_api_fallback(url):
    """
    HepsiBurada 3rd-level fallback: Extract SKU from URL and query
    the public product API which doesn't have bot protection.
    URL format: hepsiburada.com/...-p-HBCV000... or ...-p-HBV000...
    """
    import re
    import requests
    
    # Extract product SKU from URL
    sku_match = re.search(r'-pm?-([A-Z0-9]+)', url)  # HOTFIX 11.0: -p- VE -pm- formatları
    if not sku_match:
        return None
    
    sku = sku_match.group(1)
    
    # Try HepsiBurada's product detail API
    api_url = f"https://www.hepsiburada.com/product-detail/{sku}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9",
        "Referer": "https://www.hepsiburada.com/",
        "Origin": "https://www.hepsiburada.com"
    }
    
    result = {"price": None, "stock": -1, "name": None}
    
    # Strategy 1: Direct product-detail API
    try:
        resp = requests.get(api_url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                result["name"] = data.get("name") or data.get("productName")
                listing = data.get("currentListing") or data.get("listing") or {}
                if listing:
                    price_obj = listing.get("price") or {}
                    result["price"] = price_obj.get("value") or price_obj.get("amount")
                    inv = listing.get("inventory", -1)
                    result["stock"] = 11 if inv > 10 else inv
                elif data.get("price"):
                    result["price"] = data["price"].get("value") or data["price"].get("amount")
                if result["price"]:
                    return result
    except Exception:
        pass
    
    # Strategy 2: Mobile API endpoint
    try:
        mobile_url = f"https://api.hepsiburada.com/product/detail/{sku}"
        resp = requests.get(mobile_url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                result["name"] = data.get("name") or data.get("productName") or result["name"]
                price_val = data.get("price") or data.get("currentPrice")
                if isinstance(price_val, dict):
                    result["price"] = price_val.get("value") or price_val.get("amount")
                elif price_val:
                    result["price"] = price_val
                if result["price"]:
                    return result
    except Exception:
        pass
    
    # Strategy 3: Use Cloudscraper with mobile user-agent for better success rate
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': True, 'platform': 'android'})
        mobile_headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
            "Accept-Language": "tr-TR,tr;q=0.9"
        }
        resp = scraper.get(url, headers=mobile_headers, timeout=20)
        if resp.status_code == 200:
            from bs4 import BeautifulSoup
            import json
            soup = BeautifulSoup(resp.text, 'lxml')
            
            # Try to find __NEXT_DATA__ in mobile version
            for script in soup.find_all('script', {'id': '__NEXT_DATA__'}):
                try:
                    state = json.loads(script.string)
                    prod = state.get('props', {}).get('pageProps', {}).get('product', {})
                    if prod:
                        result["name"] = prod.get("name", result["name"])
                        listing = prod.get("currentListing", {})
                        if listing:
                            result["price"] = listing.get("price", {}).get("value")
                            inv = listing.get("inventory", -1)
                            result["stock"] = 11 if inv > 10 else inv
                        if result["price"]:
                            return result
                except Exception:
                    pass
            
            # Try ld+json structured data
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    ld = json.loads(script.string)
                    if isinstance(ld, list):
                        ld = ld[0]
                    if ld.get('@type') == 'Product' or ld.get('name'):
                        result["name"] = ld.get("name", result["name"])
                        offers = ld.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        price = offers.get("price") or offers.get("lowPrice")
                        if price:
                            result["price"] = price
                            return result
                except Exception:
                    pass
    except Exception:
        pass

    return result if result["price"] else None


def _playwright_single_price(url, timeout_ms=40000):
    """HOTFIX 1.41: Hafif kazıma (cloudscraper/cffi) başarısız olunca tetiklenen
    AĞIR fallback. Tek URL için Playwright + stealth ile sayfayı yükler,
    `__INITIAL_STATE__` / `__NEXT_DATA__` JSON state'ini extract_data_from_html
    ile çözer. Tek ürün başına ~5-8s ek maliyet — yalnızca lightweight çuvalladığında çağrılır.

    Returns: {"price": str|None, "name": str|None}
    """
    result = {"price": None, "name": None}
    try:
        from playwright.sync_api import sync_playwright
        try:
            import playwright_stealth
        except Exception:
            playwright_stealth = None

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                    viewport={"width": 1440, "height": 900},
                    extra_http_headers={
                        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "none",
                        "Upgrade-Insecure-Requests": "1",
                    },
                )
                page = ctx.new_page()
                # Stealth — versiyona göre tetikle
                if playwright_stealth is not None:
                    try:
                        if hasattr(playwright_stealth, 'stealth_sync'):
                            playwright_stealth.stealth_sync(page)
                        elif hasattr(playwright_stealth, 'Stealth'):
                            playwright_stealth.Stealth().apply_stealth_sync(page)
                    except Exception:
                        pass
                page.goto(url, timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(2400)
                html = page.content()
                data = extract_data_from_html(html, url)
                if data.get('price') and data['price'] != "Bulunamadı":
                    result['price'] = data['price']
                if data.get('name') and data['name'] != "İsim Bulunamadı":
                    result['name'] = data['name']
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        log.info(f"[Worker] _playwright_single_price hata ({url[:60]}): {e}")
    return result


def _scrape_hepsiburada_cffi(url, fetch_reviews=False):
    """
    Hepsiburada'ya özgü güçlü veri çekme motoru.
    
    curl_cffi kütüphanesi kullanılarak Chrome'un tam TLS parmak izini (JA3/JA4)
    taklit eder. Akamai bot koruması, TLS parmak izine dayandığından bu yöntem
    gerçek bir Chrome tarayıcısından ayırt edilemez.
    
    Çıktı sözlüğü:
        price (float|None), name (str|None), stock (int), reviews (list[str])
    """
    import re, json
    from bs4 import BeautifulSoup

    # FAZ 4: rating + review_count alanları eklendi
    result = {"price": None, "name": None, "stock": -1, "reviews": [],
              "rating": None, "review_count": 0, "seller": None}  # HOTFIX 11.0: seller

    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        log.info("[HB-CFFI] curl_cffi not available, skipping.")
        return None

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }

    def _parse_hb_html(html, src_url):
        """HTML'den __NEXT_DATA__, ld+json ve DOM'u parse ederek fiyat/isim/stok/puan/yorum çıkar."""
        # FAZ 4: rating + review_count alanları eklendi
        _r = {"price": None, "name": None, "stock": -1, "reviews": [],
              "rating": None, "review_count": 0, "seller": None}  # HOTFIX 11.0: seller
        soup = BeautifulSoup(html, "lxml")

        # --- 1. __NEXT_DATA__ JSON state (En güvenilir kaynak) ---
        nd_tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if nd_tag and nd_tag.string:
            try:
                nd = json.loads(nd_tag.string)
                prod = (nd.get("props", {})
                          .get("pageProps", {})
                          .get("product", {}))
                if prod:
                    _r["name"] = prod.get("name") or _r["name"]
                    # Birden fazla listing yolu dene
                    listing = prod.get("currentListing") or prod.get("listing") or {}
                    if listing:
                        # Fiyat: öncelik sırasıyla sellingPrice > discountedPrice > value > amount > originalPrice
                        price_obj = listing.get("price", {})
                        if isinstance(price_obj, dict):
                            _r["price"] = (
                                price_obj.get("sellingPrice")
                                or price_obj.get("discountedPrice")
                                or price_obj.get("basketPrice")
                                or price_obj.get("value")
                                or price_obj.get("amount")
                                or price_obj.get("originalPrice")
                            )
                        elif isinstance(price_obj, (int, float)) and price_obj > 0:
                            _r["price"] = price_obj
                        # Listing seviyesinde direkt fiyat alanları
                        if not _r["price"]:
                            for pkey in ['sellingPrice', 'discountedPrice', 'salePrice']:
                                pv = listing.get(pkey)
                                if pv and isinstance(pv, (int, float)) and pv > 0:
                                    _r["price"] = pv
                                    break
                        # Stok alanları — HB API farklı isimler kullanabiliyor
                        stock_keys = ['inventory', 'stockQuantity', 'sellableStock',
                                      'freeStock', 'availableQuantity', 'availableStock',
                                      'remainingStock', 'maxPurchasableQuantity', 'stock', 'quantity']
                        inv = -1
                        for sk in stock_keys:
                            sv = listing.get(sk)
                            if sv is not None and isinstance(sv, (int, float)) and sv >= 0:
                                inv = int(sv)
                                break
                        if inv >= 0:
                            _r["stock"] = 11 if inv > 10 else inv
                        if (_r["stock"] == -1) and listing.get("availabilityStatus") == 1:
                            _r["stock"] = 11

                    # variantList / listings array fallback (stok + fiyat)
                    if _r["stock"] == -1 or not _r["price"]:
                        for arr_key in ['variantList', 'listings', 'variants']:
                            arr = prod.get(arr_key, [])
                            if not isinstance(arr, list) or not arr:
                                continue
                            min_inv = None
                            for item in arr:
                                if not isinstance(item, dict):
                                    continue
                                # Fiyat fallback
                                if not _r["price"]:
                                    ip = item.get("price", {})
                                    if isinstance(ip, dict):
                                        _r["price"] = ip.get("sellingPrice") or ip.get("value") or ip.get("amount")
                                    elif isinstance(ip, (int, float)) and ip > 0:
                                        _r["price"] = ip
                                # Stok
                                for sk in stock_keys:
                                    sv = item.get(sk)
                                    if sv is not None and isinstance(sv, (int, float)) and sv >= 0:
                                        if min_inv is None or sv < min_inv:
                                            min_inv = int(sv)
                                        break
                            if min_inv is not None and _r["stock"] == -1:
                                _r["stock"] = 11 if min_inv > 10 else min_inv
                    # Reviews embedded in NEXT_DATA
                    for rev in prod.get("reviews", {}).get("data", []):
                        body = rev.get("reviewBody") or rev.get("comment") or ""
                        if len(body) > 15:
                            _r["reviews"].append(body)

                    # FAZ 4: Rating + Review Count (HB __NEXT_DATA__)
                    try:
                        rating_obj = prod.get("rating") if isinstance(prod.get("rating"), dict) else None
                        avg = (prod.get("averageRating")
                               or (rating_obj.get("average") if rating_obj else None)
                               or (rating_obj.get("score") if rating_obj else None))
                        if avg is not None:
                            try:
                                rv = float(avg)
                                if 0 < rv <= 5.0:
                                    _r["rating"] = round(rv, 2)
                            except (TypeError, ValueError):
                                pass
                        rc = (prod.get("reviewCount")
                              or prod.get("numberOfReviews")
                              or prod.get("totalReviewCount")
                              or (rating_obj.get("count") if rating_obj else None))
                        if rc is not None:
                            try:
                                ric = int(rc)
                                _r["review_count"] = ric if ric >= 0 else 0
                            except (TypeError, ValueError):
                                pass
                    except Exception as rate_err:
                        log.info(f"[HB-CFFI] rating parse skipped: {rate_err}")

                # Stok hâlâ -1 ise tüm __NEXT_DATA__ ağacında derin arama
                if _r["stock"] == -1:
                    deep = _parse_stock_from_json(nd)
                    if deep is not None and deep >= 0:
                        _r["stock"] = 11 if deep > 10 else deep
            except Exception as e:
                log.info("[HB-CFFI] __NEXT_DATA__ parse error:", e)

        # Son çare: sayfanın tüm HTML'inde JSON field regex taraması
        if _r["stock"] == -1:
            try:
                for fname in ['sellableStock', 'freeStock', 'availableQuantity',
                              'availableStock', 'stockQuantity', 'remainingStock']:
                    m = re.search(r'"' + fname + r'"\s*:\s*(\d+)', html)
                    if m:
                        v = int(m.group(1))
                        if 0 <= v <= 500:
                            _r["stock"] = 11 if v > 10 else v
                            break
            except Exception:
                pass
        # --- 2. ld+json structured data (fallback) ---
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(tag.string or "")
                items = ld if isinstance(ld, list) else ld.get("@graph", [ld])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if item.get("@type") == "Product":
                        _r["name"] = _r["name"] or item.get("name")
                        offers = item.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        if not _r["price"]:
                            _r["price"] = offers.get("price") or offers.get("lowPrice")
                        # HOTFIX 11.0: Satıcı — ld+json offers.seller.name. HB
                        # __NEXT_DATA__'dan vazgeçtiği için (artık yok) satıcı bilgisi
                        # yalnızca buradan geliyor. Önceki kod bu alanı hiç okumuyordu
                        # → "Satıcı: Bulunamadı". seller dict ({"name": "..."}) veya
                        # düz string olabilir; ikisini de karşıla.
                        if not _r.get("seller"):
                            seller_obj = offers.get("seller")
                            if isinstance(seller_obj, dict):
                                snm = seller_obj.get("name") or seller_obj.get("legalName")
                                if isinstance(snm, str) and len(snm.strip()) > 1:
                                    _r["seller"] = snm.strip()
                            elif isinstance(seller_obj, str) and len(seller_obj.strip()) > 1:
                                _r["seller"] = seller_obj.strip()
                        for rev in item.get("review", []):
                            body = rev.get("reviewBody", "") if isinstance(rev, dict) else ""
                            if len(body) > 15 and body not in _r["reviews"]:
                                _r["reviews"].append(body)
                        # FAZ 4: aggregateRating ld+json (rating boşsa fallback)
                        if _r["rating"] is None:
                            try:
                                ar = item.get("aggregateRating") or {}
                                rv = ar.get("ratingValue")
                                if rv is not None:
                                    rvf = float(rv)
                                    if 0 < rvf <= 5.0:
                                        _r["rating"] = round(rvf, 2)
                                rc = ar.get("reviewCount") or ar.get("ratingCount")
                                if rc is not None and not _r["review_count"]:
                                    _r["review_count"] = int(rc)
                            except (TypeError, ValueError):
                                pass
                    # HOTFIX 11.0: Standalone Review blokları. HB yorumları artık
                    # Product içinde DEĞİL, ayrı @type=Review ld+json blokları olarak
                    # geliyor. Önceki kod sadece Product.review'ı okuyordu → "0 Yorum".
                    elif item.get("@type") == "Review":
                        body = item.get("reviewBody") or item.get("description") or ""
                        if isinstance(body, str) and len(body) > 15 and body not in _r["reviews"]:
                            _r["reviews"].append(body)
            except Exception:
                pass

        # HOTFIX 10.7: GERÇEK BUYBOX SATICISI.
        # ld+json offers.seller çoğu üründe MARKAYI veriyor (örn "Samsung"),
        # Buybox'ı kazanan asıl satıcıyı (örn "BittiBitiyor") değil. HB sayfa-içi
        # gömülü JSON'da satışı yapan listing'in "merchantName" alanı bulunuyor;
        # ilk geçen merchantName = Buybox kazananı (canlı debug ile doğrulandı).
        # Daha doğru olduğu için ld+json seller'ı EZER. Promo/CTA metinleri elenir.
        try:
            mm = re.search(r'"merchantName"\s*:\s*"([^"]{2,60})"', html)
            if mm:
                buybox = mm.group(1).strip()
                if buybox and not re.search(
                    r'(satış\s*yap|mağazanı?\s*aç|hesap\s*oluştur)', buybox, re.IGNORECASE
                ):
                    _r["seller"] = buybox
        except Exception:
            pass

        # --- 3. SKU-based direct API (last resort for price) ---
        if not _r["price"]:
            sku_m = re.search(r"-pm?-([A-Z0-9]+)", src_url)
            if sku_m:
                sku = sku_m.group(1)
                try:
                    api_resp = cffi_requests.get(
                        f"https://www.hepsiburada.com/product-detail/{sku}",
                        headers={**HEADERS, "Accept": "application/json"},
                        impersonate="chrome110",
                        timeout=10
                    )
                    if api_resp.status_code == 200:
                        d = api_resp.json()
                        _r["name"] = _r["name"] or d.get("name")
                        listing = d.get("currentListing") or {}
                        price_obj = listing.get("price", {})
                        _r["price"] = price_obj.get("value") or price_obj.get("amount")
                    # --- 4. Hepsiburada Public Reviews API & JSON State Fallback ---
                    if fetch_reviews and not _r["reviews"]:
                        sku_m = re.search(r"-pm?-([A-Z0-9]+)", src_url)
                        if sku_m:
                            sku = sku_m.group(1)
                            
                            # A: Try public APIs
                            api_endpoints = [
                                f"https://user-content-gw-api.hepsiburada.com/api/v1/reviews/{sku}?page=0&size=50",
                                f"https://user-content-gw-api.hepsiburada.com/api/v1/reviews/product/{sku}?page=0&size=50",
                                f"https://hermes.hepsiburada.com/public/product/{sku}/comments?page=1&size=50"
                            ]
                            for api_url in api_endpoints:
                                try:
                                    api_resp = cffi_requests.get(
                                        api_url,
                                        headers={**HEADERS, "Accept": "application/json", "Origin": "https://www.hepsiburada.com"},
                                        impersonate="chrome110",
                                        timeout=7
                                    )
                                    if api_resp.status_code == 200:
                                        data = api_resp.json()
                                        def extract_reviews(obj):
                                            if isinstance(obj, dict):
                                                for k, v in obj.items():
                                                    if k in ["review", "comment", "reviewBody", "content"]:
                                                        if isinstance(v, str) and len(v) > 10 and v not in _r["reviews"]:
                                                            _r["reviews"].append(v)
                                                        elif isinstance(v, dict) and "review" in v:
                                                            extract_reviews(v)
                                                    else:
                                                        extract_reviews(v)
                                            elif isinstance(obj, list):
                                                for item in obj:
                                                    extract_reviews(item)
                                        extract_reviews(data)
                                        if _r["reviews"]:
                                            break
                                except Exception:
                                    pass
                            
                            # B: Fetch the HTML of the -yorumlari page directly as a last resort
                            if not _r["reviews"]:
                                try:
                                    y_url = src_url + "-yorumlari" if not src_url.endswith("-yorumlari") else src_url
                                    y_resp = cffi_requests.get(y_url, headers=HEADERS, impersonate="chrome110", timeout=8)
                                    if y_resp.status_code == 200:
                                        # 1. Look for React structured schema
                                        for match in re.finditer(r'\"reviewBody\"\s*:\s*\"(.*?)\"', y_resp.text):
                                            rev_txt = match.group(1).encode('utf-8').decode('unicode_escape')
                                            if len(rev_txt) > 10 and rev_txt not in _r["reviews"]:
                                                _r["reviews"].append(rev_txt)
                                        
                                        # 2. Try common DOM selectors if JSON Regex fails
                                        if not _r["reviews"]:
                                            y_soup = BeautifulSoup(y_resp.text, "lxml")
                                            for sel in ["span[itemprop='description']", "[data-test-id='review-text']", ".review-text", ".hermes-ReviewCard-module-3S6oE"]:
                                                for el in y_soup.select(sel):
                                                    txt = el.get_text(strip=True)
                                                    if len(txt) > 10 and txt not in _r["reviews"]:
                                                        _r["reviews"].append(txt)
                                except Exception:
                                    pass
                except Exception:
                    pass

        return _r

    # --- PRIMARY: Fetch product page ---
    try:
        resp = cffi_requests.get(url, headers=HEADERS, impersonate="chrome110", timeout=25)
        log.info(f"[HB-CFFI] Product page status: {resp.status_code}")
        if resp.status_code == 200:
            parsed = _parse_hb_html(resp.text, url)
            result.update(parsed)
    except Exception as e:
        log.info(f"[HB-CFFI] Product page fetch failed: {e}")

    # --- REVIEWS: Fetch the reviews page if needed and not already found ---
    if fetch_reviews and len(result["reviews"]) < 3:
        try:
            review_url = url.split("?")[0].rstrip("/") + "-yorumlari"
            rev_resp = cffi_requests.get(review_url, headers=HEADERS, impersonate="chrome110", timeout=25)
            log.info(f"[HB-CFFI] Reviews page status: {rev_resp.status_code}")
            if rev_resp.status_code == 200:
                rev_parsed = _parse_hb_html(rev_resp.text, review_url)
                # Merge reviews
                for r in rev_parsed["reviews"]:
                    if r not in result["reviews"]:
                        result["reviews"].append(r)
                # Also update name/price if still missing
                if not result["price"] and rev_parsed["price"]:
                    result["price"] = rev_parsed["price"]
                if not result["name"] and rev_parsed["name"]:
                    result["name"] = rev_parsed["name"]
        except Exception as e:
            log.info(f"[HB-CFFI] Reviews page fetch failed: {e}")

        # --- REVIEWS: Try AJAX review API endpoints ---
        if len(result["reviews"]) < 3:
            sku_m = re.search(r"-pm?-([A-Z0-9]+)", url)
            if sku_m:
                sku = sku_m.group(1)
                review_apis = [
                    f"https://www.hepsiburada.com/product/{sku}/reviews?page=0&pageSize=20",
                    f"https://www.hepsiburada.com/reviews/{sku}?pageSize=20",
                ]
                for api_url in review_apis:
                    try:
                        r = cffi_requests.get(
                            api_url,
                            headers={**HEADERS, "Accept": "application/json"},
                            impersonate="chrome110",
                            timeout=15
                        )
                        if r.status_code == 200:
                            data = r.json()
                            reviews_raw = (
                                data.get("reviews") or
                                data.get("data", {}).get("reviews") or
                                data.get("items") or []
                            )
                            for rv in reviews_raw:
                                body = rv.get("reviewBody") or rv.get("comment") or rv.get("text", "")
                                if len(body) > 15 and body not in result["reviews"]:
                                    result["reviews"].append(body)
                            if result["reviews"]:
                                log.info(f"[HB-CFFI] Got {len(result['reviews'])} reviews from review API")
                                break
                    except Exception:
                        pass

    log.info(f"[HB-CFFI] Final result — price: {result['price']}, name: {result['name']}, stock: {result['stock']}, reviews: {len(result['reviews'])}")
    return result if (result["price"] or result["name"]) else None


#: Gerçek, sabit Chrome/Mac user-agent — HB DataDome fingerprint tutarlılığı için
BMK_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _safe_goto(page, url, timeout=20000, wait_until='domcontentloaded'):
    """Resilient navigation wrapper.

    - DataDome sonsuz yükleme döngüsünü kırmak için wait_until='domcontentloaded'
      kullanır (tüm asset'leri/trackerları beklemez, HTML gelir gelmez döner).
    - Timeout 40s'den 20s'ye düşürüldü; uzun beklemeler Redis broker
      bağlantısını idle bırakıp Upstash tarafında da socket drop'a yol açıyordu.
    - TimeoutError yakalanır, worker crash olmaz; fallback zincirine devam edilir.
    Returns: True eğer navigasyon başarılıysa, False eğer timeout / error.
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeoutError
    except Exception:
        PWTimeoutError = Exception
    try:
        page.goto(url, timeout=timeout, wait_until=wait_until)
        return True
    except PWTimeoutError as te:
        log.info(f"[SafeGoto] ⏱️ Timeout ({timeout}ms) — {url[:80]} — sessiz fallback'e geçiliyor.")
        try:
            # En azından o ana kadar yüklenmiş DOM'u kullanalım
            page.evaluate("window.stop && window.stop();")
        except Exception:
            pass
        return False
    except Exception as e:
        log.info(f"[SafeGoto] ❌ Navigation error: {type(e).__name__}: {str(e)[:120]}")
        return False


# =========================================================================
# FAZ 3 — Hafif Marketplace Kazıyıcıları (N11, Çiçeksepeti, PttAVM)
# Sadece cloudscraper + BS4 + ld+json. ASLA Playwright/Selenium kullanmaz.
# Çıktı sözlüğü: {"price": float|None, "name": str|None,
#                 "rating": float|None, "review_count": int}
# =========================================================================

def _ldjson_extract(soup):
    """Bir BeautifulSoup nesnesinden ld+json structured data'yı parse edip
    Product / aggregateRating / offers alanlarını birleştirilmiş bir sözlüğe çıkarır.
    Hatasız ve idempotent — None değer döndürebilir."""
    import json as _json
    out = {"price": None, "name": None, "rating": None, "review_count": 0}
    try:
        for tag in soup.find_all("script", type="application/ld+json"):
            if not tag.string:
                continue
            try:
                ld = _json.loads(tag.string)
            except Exception:
                continue
            items = ld if isinstance(ld, list) else (
                ld.get("@graph", [ld]) if isinstance(ld, dict) else [ld]
            )
            for item in items:
                if not isinstance(item, dict):
                    continue
                t = item.get("@type")
                if t == "Product" or "aggregateRating" in item or "offers" in item:
                    if not out["name"]:
                        out["name"] = item.get("name") or out["name"]
                    offers = item.get("offers")
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    if isinstance(offers, dict) and not out["price"]:
                        p = offers.get("price") or offers.get("lowPrice")
                        if p is not None:
                            try:
                                pf = float(str(p).replace(",", "."))
                                if pf > 0:
                                    out["price"] = pf
                            except (TypeError, ValueError):
                                pass
                    ar = item.get("aggregateRating") or {}
                    if isinstance(ar, dict):
                        rv = ar.get("ratingValue")
                        if rv is not None and out["rating"] is None:
                            try:
                                rvf = float(str(rv).replace(",", "."))
                                if 0 < rvf <= 5.0:
                                    out["rating"] = round(rvf, 2)
                            except (TypeError, ValueError):
                                pass
                        rc = ar.get("reviewCount") or ar.get("ratingCount")
                        if rc is not None and not out["review_count"]:
                            try:
                                out["review_count"] = max(0, int(rc))
                            except (TypeError, ValueError):
                                pass
    except Exception as e:
        log.info(f"[Marketplace] ld+json parse skipped: {e}")
    return out


def _scrape_n11(url):
    """N11.com — hafif kazıyıcı. __NEXT_DATA__ + ld+json + DOM regex.
    Sadece cloudscraper kullanır."""
    import re as _re, json as _json
    import cloudscraper
    from bs4 import BeautifulSoup
    result = {"price": None, "name": None, "rating": None, "review_count": 0}
    try:
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'})
        resp = scraper.get(url, timeout=20, headers={
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        if resp.status_code != 200:
            log.info(f"[N11] HTTP {resp.status_code} for {url}")
            return result
        html = resp.text
        soup = BeautifulSoup(html, "lxml")

        # 1) ld+json (en güvenilir)
        ld = _ldjson_extract(soup)
        for k, v in ld.items():
            if v not in (None, 0, ""):
                result[k] = v

        # 2) __NEXT_DATA__ (modern N11 React/Next.js)
        if not result["price"]:
            nd_tag = soup.find("script", {"id": "__NEXT_DATA__"})
            if nd_tag and nd_tag.string:
                try:
                    nd = _json.loads(nd_tag.string)
                    pp = nd.get("props", {}).get("pageProps", {}) or {}
                    prod = pp.get("product") or pp.get("productInfo") or pp.get("data", {}).get("product") or {}
                    if isinstance(prod, dict):
                        if not result["name"]:
                            result["name"] = prod.get("title") or prod.get("name")
                        # Fiyat
                        for pkey in ('displayPrice', 'price', 'salePrice', 'listPrice', 'finalPrice'):
                            pv = prod.get(pkey)
                            if isinstance(pv, dict):
                                pv = pv.get("amount") or pv.get("value") or pv.get("price")
                            try:
                                pvf = float(pv) if pv is not None else 0
                                if pvf > 0:
                                    result["price"] = pvf
                                    break
                            except (TypeError, ValueError):
                                continue
                        # Puan/yorum
                        if result["rating"] is None:
                            r = prod.get("averageRating") or prod.get("ratingScore") or (
                                prod.get("rating", {}).get("average") if isinstance(prod.get("rating"), dict) else None
                            )
                            try:
                                rf = float(r) if r is not None else 0
                                if 0 < rf <= 5.0:
                                    result["rating"] = round(rf, 2)
                            except (TypeError, ValueError):
                                pass
                        if not result["review_count"]:
                            rc = prod.get("reviewCount") or prod.get("commentCount") or (
                                prod.get("rating", {}).get("count") if isinstance(prod.get("rating"), dict) else None
                            )
                            try:
                                rci = int(rc) if rc is not None else 0
                                if rci >= 0:
                                    result["review_count"] = rci
                            except (TypeError, ValueError):
                                pass
                except Exception as e:
                    log.info(f"[N11] __NEXT_DATA__ parse skipped: {e}")

        # 3) DOM fallback — fiyat
        if not result["price"]:
            for sel in (".newPrice ins", ".newPrice", ".unf-p-summary-price ins", "[itemprop='price']"):
                el = soup.select_one(sel)
                if el:
                    txt = el.get("content") or el.get_text(strip=True)
                    m = _re.search(r"[\d.,]+", txt or "")
                    if m:
                        try:
                            pf = float(m.group(0).replace(".", "").replace(",", "."))
                            if pf > 0:
                                result["price"] = pf
                                break
                        except ValueError:
                            pass

        # 4) DOM fallback — isim
        if not result["name"]:
            h1 = soup.find("h1")
            if h1:
                result["name"] = h1.get_text(strip=True)[:200]

        return result
    except Exception as e:
        log.info(f"[N11] scrape failed for {url}: {e}")
        return result


def _scrape_ciceksepeti(url):
    """Çiçeksepeti.com — hafif kazıyıcı. ld+json öncelikli + DOM fallback."""
    import re as _re
    import cloudscraper
    from bs4 import BeautifulSoup
    result = {"price": None, "name": None, "rating": None, "review_count": 0}
    try:
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'})
        resp = scraper.get(url, timeout=20, headers={
            "Accept-Language": "tr-TR,tr;q=0.9",
        })
        if resp.status_code != 200:
            log.info(f"[Çiçeksepeti] HTTP {resp.status_code} for {url}")
            return result
        soup = BeautifulSoup(resp.text, "lxml")

        # 1) ld+json
        ld = _ldjson_extract(soup)
        for k, v in ld.items():
            if v not in (None, 0, ""):
                result[k] = v

        # 2) DOM fallback — fiyat (Çiçeksepeti'nde sıkça kullanılan selektörler)
        if not result["price"]:
            for sel in (".product-price-amount", ".productPriceWrapper .priceAmount",
                        "[data-price]", ".product-price", "span[itemprop='price']"):
                el = soup.select_one(sel)
                if el:
                    txt = el.get("data-price") or el.get("content") or el.get_text(strip=True)
                    m = _re.search(r"[\d.,]+", txt or "")
                    if m:
                        try:
                            pf = float(m.group(0).replace(".", "").replace(",", "."))
                            if pf > 0:
                                result["price"] = pf
                                break
                        except ValueError:
                            pass

        # 3) DOM fallback — isim
        if not result["name"]:
            for sel in ("h1.product-name", "h1[itemprop='name']", "h1"):
                el = soup.select_one(sel)
                if el:
                    result["name"] = el.get_text(strip=True)[:200]
                    break

        return result
    except Exception as e:
        log.info(f"[Çiçeksepeti] scrape failed for {url}: {e}")
        return result


def _scrape_pttavm(url):
    """PttAVM.com — hafif kazıyıcı. ld+json öncelikli + DOM fallback."""
    import re as _re
    import cloudscraper
    from bs4 import BeautifulSoup
    result = {"price": None, "name": None, "rating": None, "review_count": 0}
    try:
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'})
        resp = scraper.get(url, timeout=20, headers={
            "Accept-Language": "tr-TR,tr;q=0.9",
        })
        if resp.status_code != 200:
            log.info(f"[PttAVM] HTTP {resp.status_code} for {url}")
            return result
        soup = BeautifulSoup(resp.text, "lxml")

        # 1) ld+json
        ld = _ldjson_extract(soup)
        for k, v in ld.items():
            if v not in (None, 0, ""):
                result[k] = v

        # 2) DOM fallback — fiyat (PttAVM tipik selektörleri)
        if not result["price"]:
            for sel in (".price-info .new-price", ".product-price .price",
                        "[itemprop='price']", ".new-price", ".price"):
                el = soup.select_one(sel)
                if el:
                    txt = el.get("content") or el.get_text(strip=True)
                    m = _re.search(r"[\d.,]+", txt or "")
                    if m:
                        try:
                            pf = float(m.group(0).replace(".", "").replace(",", "."))
                            if pf > 0:
                                result["price"] = pf
                                break
                        except ValueError:
                            pass

        # 3) DOM fallback — isim
        if not result["name"]:
            for sel in ("h1.product-name", "h1[itemprop='name']", "h1"):
                el = soup.select_one(sel)
                if el:
                    result["name"] = el.get_text(strip=True)[:200]
                    break

        return result
    except Exception as e:
        log.info(f"[PttAVM] scrape failed for {url}: {e}")
        return result


# =========================================================================
# FAZ 4 — SEO / Arama Sırası Takibi (Trendyol için keyword + URL eşleştirme)
# Hafif: cloudscraper + BS4. Browser açmaz.
# =========================================================================

def _extract_trendyol_product_id(url):
    """Trendyol URL'inden -p-XXXXXXX kalıbındaki ürün ID'sini çeker."""
    import re as _re
    if not url:
        return None
    m = _re.search(r'-p-(\d+)', url)
    return m.group(1) if m else None


def _decode_response_body(resp):
    """HOTFIX 1.19: HTTP yanıt gövdesini güvenle string'e çevir.
    curl_cffi bazen gzip raw byte'ları auto-decompress etmeden döndürüyor
    (\\x1f\\x8b... imzasıyla). Bu fonksiyon imzayı görürse manuel gzip.decompress yapar."""
    import gzip as _gzip
    try:
        body = getattr(resp, "content", None)
        if isinstance(body, (bytes, bytearray)) and len(body) >= 2:
            # Gzip magic number imzası
            if body[:2] == b"\x1f\x8b":
                try:
                    return _gzip.decompress(bytes(body)).decode("utf-8", errors="ignore")
                except Exception as gz_err:
                    log.info(f"[SEO _decode] gzip.decompress hata: {gz_err}")
            # Düz bytes — UTF-8 decode
            try:
                return bytes(body).decode("utf-8", errors="ignore")
            except Exception:
                pass
        # Son çare: resp.text
        txt = getattr(resp, "text", "") or ""
        # resp.text içinde de binary kalmış olabilir
        if txt.startswith("\x1f\x8b") and isinstance(body, (bytes, bytearray)):
            try:
                return _gzip.decompress(bytes(body)).decode("utf-8", errors="ignore")
            except Exception:
                return txt
        return txt
    except Exception as e:
        log.info(f"[SEO _decode] genel hata: {e}")
        try:
            return resp.text or ""
        except Exception:
            return ""


def _seo_fetch_html(url, label="Generic"):
    """HOTFIX 1.19: SEO arama sayfaları için unified HTTP getter.
    ÖNCELİK: curl_cffi (Chrome JA3/JA4 TLS taklidi — Akamai/PerimeterX'i deler).
    FALLBACK: cloudscraper.
    Gzip raw byte yanıtı tespit edilirse manuel _decode_response_body ile çözülür.

    Returns: (html_string, status_code, source_label) — başarısızsa ('', 0, 'fail').
    """
    # HOTFIX 1.19: "identity" yok, gerçek browser gibi gzip kabul eder.
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Chromium";v="110", "Not A(Brand";v="24", "Google Chrome";v="110"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "DNT": "1",
        "Connection": "keep-alive",
    }

    # 1) curl_cffi (asıl yol) — HOTFIX 1.22: sadece chrome110 (116 ve generic alias desteklenmiyor)
    try:
        from curl_cffi import requests as cffi_requests
        for profile in ("chrome110",):
            try:
                resp = cffi_requests.get(url, headers=headers, impersonate=profile, timeout=22)
                if resp.status_code == 200:
                    text = _decode_response_body(resp)
                    if text:
                        log.info(f"[SEO {label}] curl_cffi ({profile}) → 200 OK ({len(text)} byte)")
                        return (text, 200, f"curl_cffi:{profile}")
                log.info(f"[SEO {label}] curl_cffi ({profile}) HTTP {resp.status_code}")
            except ValueError as ve:
                log.info(f"[SEO {label}] curl_cffi profile '{profile}' desteklenmiyor: {ve}")
                continue
            except Exception as e:
                log.info(f"[SEO {label}] curl_cffi ({profile}) hata: {e}")
                continue
    except ImportError:
        log.info(f"[SEO {label}] curl_cffi yüklü değil, cloudscraper'a düşülüyor.")
    except Exception as e:
        log.info(f"[SEO {label}] curl_cffi global hata: {e}")

    # 2) cloudscraper fallback
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'})
        resp = scraper.get(url, headers=headers, timeout=22)
        if resp.status_code == 200:
            text = _decode_response_body(resp)
            if text:
                log.info(f"[SEO {label}] cloudscraper → 200 OK ({len(text)} byte)")
                return (text, 200, "cloudscraper")
        log.info(f"[SEO {label}] cloudscraper HTTP {resp.status_code}")
        return ("", resp.status_code, "cloudscraper-fail")
    except Exception as e:
        log.info(f"[SEO {label}] cloudscraper hata: {e}")
        return ("", 0, "fail")


# =========================================================================
# HOTFIX 1.21 — SEO MOTORU MİMARİ YENİDEN YAZIM
# Trendyol → Resmi public discovery-web-searchgw API (curl_cffi).
# Hepsiburada → Playwright + page.on("response") ağ dinleme (mobil UA).
# DOM seçicileri yok, wait_for_selector yok, HTML parse yok.
# =========================================================================

def _seo_launch_browser(p):
    """HOTFIX 1.20: SEO modülü için ortak Playwright browser launcher.
    Stealth + gerçekçi UA + TR locale. Caller close() sorumluluğunu alır."""
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = browser.new_context(
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        viewport={"width": 1440, "height": 900},
        locale="tr-TR",
        timezone_id="Europe/Istanbul",
        extra_http_headers={
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    context.set_default_navigation_timeout(25000)
    context.set_default_timeout(15000)
    page = context.new_page()
    # Fail-safe stealth
    try:
        import playwright_stealth
        if hasattr(playwright_stealth, 'stealth_sync'):
            playwright_stealth.stealth_sync(page)
        elif hasattr(playwright_stealth, 'Stealth'):
            playwright_stealth.Stealth().apply_stealth_sync(page)
    except Exception as e:
        log.info(f"[SEO] Stealth uygulanamadı (devam ediliyor): {e}")
    return browser, context, page


def _track_keyword_trendyol(keyword, target_url, max_pages=5):
    """HOTFIX 1.23: TRENDYOL — DOM LİNK TARAMA (birincil ve yegane yöntem).

    Strateji:
      1) Playwright ile www.trendyol.com/sr?q=... aç.
      2) Sayfa render olduğunda DOM'da ürün kartlarındaki <a href="/.../-p-NNNNN">
         linklerini sırasıyla (document order) topla, eşsiz ID listesi oluştur.
      3) target_id eşleşirse o sıradaki rank döner.
      4) HTML regex fallback (DOM beklenmedik biçimde değişirse).
      5) Hiçbir kaynaktan ürün çıkmazsa zarif çıkış (return 0,0) — bot ya da boş arama.

    XHR/JSON parse YOK — kararsızlık ve gereksiz kod karmaşıklığı bertaraf edildi.
    """
    import urllib.parse

    target_id = _extract_trendyol_product_id(target_url)
    if not target_id:
        log.info(f"[SEO Trendyol] Hedef URL'de ürün ID'si bulunamadı: {target_url}")
        return (0, 0)
    target_id_str = str(target_id).strip()

    encoded = urllib.parse.quote_plus(keyword.strip())

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.info("[SEO Trendyol] Playwright yüklü değil — modül kullanılamaz.")
        return (0, 0)

    browser = None
    context = None
    try:
        with sync_playwright() as p:
            browser, context, page = _seo_launch_browser(p)

            empty_streak = 0  # arka arkaya boş sayfa sayacı (sonsuz döngü koruması)

            for page_no in range(1, max_pages + 1):
                if page_no == 1:
                    search_url = f"https://www.trendyol.com/sr?q={encoded}"
                else:
                    search_url = f"https://www.trendyol.com/sr?q={encoded}&pi={page_no}"

                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2500)
                    # Lazy load için scroll (ürün kartları viewport'a girince hidrasyon olur)
                    for _ in range(3):
                        try:
                            page.evaluate("window.scrollBy(0, window.innerHeight);")
                        except Exception:
                            break
                        page.wait_for_timeout(600)
                    page.wait_for_timeout(1000)
                except Exception as nav_err:
                    log.info(f"[SEO Trendyol] Sayfa {page_no} navigasyon hatası: {nav_err}")
                    break

                # Bot koruması / captcha tespiti — zarif çıkış
                try:
                    title = page.title() or ""
                    if any(sig in title.lower() for sig in ("güvenlik", "captcha", "robot", "doğrula", "access denied", "forbidden")):
                        log.info(f"[SEO Trendyol] ⚠️ Bot koruması algılandı ({title!r}), döngüden çıkılıyor.")
                        break
                except Exception:
                    pass

                # ── BİRİNCİL VE YEGANE YÖNTEM: DOM LİNK TARAMA ──
                # Her ürün kartında <a href="/.../...-p-NNNNN"> linki bulunur.
                # document order = arama sıralaması.
                ordered_ids = []
                try:
                    ordered_ids = page.evaluate(r"""() => {
                        var seen = new Set();
                        var out = [];
                        var anchors = document.querySelectorAll('a[href*="-p-"]');
                        for (var i = 0; i < anchors.length; i++) {
                            var href = anchors[i].getAttribute('href') || '';
                            var m = href.match(/-p-(\d+)/);
                            if (m && m[1]) {
                                var id = m[1];
                                if (!seen.has(id)) {
                                    seen.add(id);
                                    out.push(id);
                                }
                            }
                        }
                        return out;
                    }""") or []
                except Exception as dom_err:
                    log.info(f"[SEO Trendyol] DOM link tarama hatası: {dom_err}")
                    ordered_ids = []

                log.info(f"[SEO Trendyol] '{keyword}' Sayfa {page_no} — Eşsiz ürün ID: {len(ordered_ids)}")

                if ordered_ids and target_id_str in ordered_ids:
                    rank = ordered_ids.index(target_id_str) + 1
                    log.info(f"[SEO Trendyol] '{keyword}' → ✅ EŞLEŞME: sayfa {page_no}, sıra {rank} (id={target_id_str})")
                    return (page_no, rank)

                # ── HTML regex fallback (DOM querySelector beklenmedik biçimde başarısızsa) ──
                if not ordered_ids:
                    try:
                        import re as _re_ty
                        html_src = page.content() or ""
                        regex_ids = []
                        seen_r = set()
                        for m in _re_ty.finditer(r'-p-(\d{4,})', html_src):
                            _id = m.group(1)
                            if _id not in seen_r:
                                seen_r.add(_id)
                                regex_ids.append(_id)
                        if regex_ids:
                            log.info(f"[SEO Trendyol] Sayfa {page_no} — HTML regex ID: {len(regex_ids)} (DOM boştu)")
                            if target_id_str in regex_ids:
                                rank = regex_ids.index(target_id_str) + 1
                                log.info(f"[SEO Trendyol] '{keyword}' → ✅ EŞLEŞME (HTML regex): sayfa {page_no}, sıra {rank}")
                                return (page_no, rank)
                            ordered_ids = regex_ids  # döngü bitiş kararı için say
                    except Exception as rex_err:
                        log.info(f"[SEO Trendyol] HTML regex hatası: {rex_err}")

                # Sonsuz döngü koruması: 2 sayfa üst üste boşsa bitir
                if not ordered_ids:
                    empty_streak += 1
                    log.info(f"[SEO Trendyol] Sayfa {page_no} — boş (streak={empty_streak}).")
                    if empty_streak >= 2:
                        log.info(f"[SEO Trendyol] Arda arda {empty_streak} boş sayfa → güvenli çıkış.")
                        break
                else:
                    empty_streak = 0

            log.info(f"[SEO Trendyol] '{keyword}' → ilk {max_pages} sayfada bulunamadı (target_id={target_id}).")
            return (0, 0)

    except Exception as e:
        log.info(f"[SEO Trendyol] global hata: {e}")
        return (0, 0)
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass


def _extract_hepsiburada_product_id(url):
    """Hepsiburada URL'inden -p-HBV/HBC... SKU'sunu çeker."""
    import re as _re
    if not url:
        return None
    # HB SKU formatı: -p-HBCV000... veya -p-HBV000... veya -p-HB...
    m = _re.search(r'-pm?-([A-Z0-9]+)', url)  # HOTFIX 11.0: -p- VE -pm- formatları
    return m.group(1) if m else None


def _track_keyword_hepsiburada(keyword, target_url, max_pages=5):
    """HOTFIX 11.1: HEPSİBURADA SEO — curl_cffi ile arama sırası tarama.

    HOTFIX 1.23'te bu modül "HB arama sayfaları DataDome ile korunuyor, residential
    proxy (~$30-50/ay) gerekir" gerekçesiyle kapatılıp sentinel (-1,-1) döndürüyordu.
    2026-06 canlı testi bu varsayımı ÇÜRÜTTÜ: HB arama sayfaları (/ara?q=...) artık
    curl_cffi Chrome TLS taklidiyle SORUNSUZ çekiliyor — DataDome bloğu yok, proxy
    gerekmez. Ürün sayfalarıyla aynı motor (bkz. _scrape_hepsiburada_cffi).

    Strateji (Trendyol mantığının curl_cffi/HTTP karşılığı — tarayıcı YOK):
      1) https://www.hepsiburada.com/ara?q=<kw>&sayfa=<n>  (n = 1..max_pages)
      2) Ürün linklerindeki -p-/-pm- SKU'larını document order'da topla
      3) target SKU eşleşirse o sayfadaki sıra döner → (page, rank)
      4) Bot/captcha tespiti → zarif çıkış (0, 0)
      5) Bulunamazsa (0, 0)

    Playwright'tan daha hafif/hızlı. Caller (page, rank)'i Trendyol ile aynı işler.

    NOT (bilinen yaklaşım): HB arama sayfasında sponsorlu/öneri kartları organik
    sonuçlara karışabilir; rank document-order'dır (Trendyol ile aynı kabul).
    """
    import urllib.parse
    import re as _re_hb

    target_sku = _extract_hepsiburada_product_id(target_url)
    if not target_sku:
        log.info(f"[SEO Hepsiburada] Hedef URL'de SKU bulunamadı: {target_url}")
        return (0, 0)
    target_sku = str(target_sku).strip().upper()

    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        log.info("[SEO Hepsiburada] curl_cffi yüklü değil — modül kullanılamaz.")
        return (0, 0)

    from bs4 import BeautifulSoup

    enc = urllib.parse.quote_plus(keyword.strip())
    HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
    }

    empty_streak = 0  # arka arkaya boş sayfa koruması (sonsuz döngü engeli)
    for page_no in range(1, max_pages + 1):
        search_url = f"https://www.hepsiburada.com/ara?q={enc}"
        if page_no > 1:
            search_url += f"&sayfa={page_no}"

        try:
            resp = cffi_requests.get(search_url, headers=HEADERS,
                                     impersonate="chrome110", timeout=20)
        except Exception as nav_err:
            log.info(f"[SEO Hepsiburada] Sayfa {page_no} istek hatası: {nav_err}")
            break

        if resp.status_code != 200:
            log.info(f"[SEO Hepsiburada] Sayfa {page_no} status={resp.status_code} — çıkılıyor.")
            break

        # Bot/captcha tespiti — zarif çıkış
        low = resp.text.lower()
        if any(sig in low for sig in ("datadome", "px-captcha", "geo.captcha",
                                      "robot musunuz", "access denied", "are you a human")):
            log.info(f"[SEO Hepsiburada] ⚠️ Bot koruması algılandı (sayfa {page_no}), çıkılıyor.")
            break

        # ── DOM document order'da SKU topla (birincil) ──
        ordered = []
        seen = set()
        try:
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.select('a[href]'):
                m = _re_hb.search(r'-p[m]?-([A-Z0-9]{8,})', a.get('href', '') or '')
                if m:
                    sku = m.group(1)
                    if sku not in seen:
                        seen.add(sku)
                        ordered.append(sku)
        except Exception as dom_err:
            log.info(f"[SEO Hepsiburada] DOM parse hatası: {dom_err}")

        # ── HTML regex fallback (DOM boşsa) ──
        if not ordered:
            for m in _re_hb.finditer(r'-p[m]?-([A-Z0-9]{8,})', resp.text):
                sku = m.group(1)
                if sku not in seen:
                    seen.add(sku)
                    ordered.append(sku)

        log.info(f"[SEO Hepsiburada] '{keyword}' Sayfa {page_no} — Eşsiz SKU: {len(ordered)}")

        if ordered and target_sku in ordered:
            rank = ordered.index(target_sku) + 1
            log.info(f"[SEO Hepsiburada] '{keyword}' → ✅ EŞLEŞME: sayfa {page_no}, "
                     f"sıra {rank} (sku={target_sku})")
            return (page_no, rank)

        if not ordered:
            empty_streak += 1
            if empty_streak >= 2:
                log.info(f"[SEO Hepsiburada] Arda arda {empty_streak} boş sayfa → güvenli çıkış.")
                break
        else:
            empty_streak = 0

    log.info(f"[SEO Hepsiburada] '{keyword}' → ilk {max_pages} sayfada bulunamadı (sku={target_sku}).")
    return (0, 0)


@celery.task
def check_keyword_trackers_task(tracker_ids=None):
    """FAZ 4 / HOTFIX 1.89: Tüm aktif KeywordTracker kayıtları için Trendyol/HB
    sırasını günceller. Saatlik veya istenirse manuel olarak tetiklenebilir.

    Args:
        tracker_ids (list[int] | None): None → tüm aktifler (saatlik beat akışı).
                                        Liste → sadece bu ID'ler (manuel anlık).
                                        `.delay([id1, id2])` ile çağrılabilir.
    """
    from app import app
    with app.app_context():
        check_keyword_trackers(app, tracker_ids=tracker_ids)


def check_keyword_trackers(app, tracker_ids=None):
    """KeywordTracker kayıtlarını tarar ve current_page/current_rank günceller.

    Args:
        app: Flask uygulama instance'ı.
        tracker_ids (list[int] | None): None → son 1 saatte kontrol edilmemiş aktif tüm kayıtlar.
                                        Liste → sadece bu ID'ler (yeni eklenen anlık kontrol için).
    """
    # ── HOTFIX 10.5: Notification + TrackedProduct import'ları EKSİKTİ ──
    # Bu fonksiyonun SEO sıralama değişim bildirimi üreten bloğunda (line ~2214)
    # Notification(...) ve TrackedProduct.query çağrıları vardı ama yukarıdaki
    # import setine dahil edilmemişti → NameError. Hata sessizce yutuluyordu
    # (`except Exception` + `log.info`), kullanıcı panelinde SEO bildirimi
    # ASLA görünmüyordu. Eksik isimler eklendi.
    from models import (
        db, KeywordTracker, SEOHistory, KeywordPool, Notification,
        TrackedProduct, get_tr_now,
    )
    from datetime import timedelta

    if tracker_ids:
        rows = KeywordTracker.query.filter(
            KeywordTracker.id.in_(tracker_ids),
            KeywordTracker.is_active == True
        ).all()
    else:
        threshold = get_tr_now() - timedelta(hours=1)
        # HOTFIX 1.91: KeywordPool.is_dormant=True olan trackers'ları atla
        rows = (KeywordTracker.query
                .outerjoin(KeywordPool, KeywordTracker.pool_id == KeywordPool.id)
                .filter(
                    KeywordTracker.is_active == True,
                    (KeywordPool.id.is_(None)) | (KeywordPool.is_dormant == False),
                    (KeywordTracker.last_checked == None) |
                    (KeywordTracker.last_checked < threshold)
                ).all())

    if not rows:
        return
    log.info(f"[SEO] {len(rows)} keyword tracker kontrol ediliyor...")

    for kt in rows:
        try:
            platform = (kt.platform or '').strip().lower()

            # HOTFIX 11.1: Trendyol (Playwright) + Hepsiburada (curl_cffi) — ikisi de
            # gerçek (page, rank) döner. (-1,-1) sentinel dalı artık tetiklenmez ama
            # geriye dönük güvenlik için aşağıda korundu.
            if platform == 'trendyol':
                page, rank = _track_keyword_trendyol(kt.keyword, kt.target_url, max_pages=5)
            elif platform == 'hepsiburada':
                page, rank = _track_keyword_hepsiburada(kt.keyword, kt.target_url, max_pages=5)
            else:
                log.info(f"[SEO] tracker {kt.id} — desteklenmeyen platform: {kt.platform}")
                continue

            # HOTFIX 1.23: Sentinel (-1,-1) "Bakımda" → previous değerleri KORU,
            # gerçek bir veri ölçümü olmadığı için delta hesabı bozulmasın.
            if page == -1 and rank == -1:
                kt.current_page = -1
                kt.current_rank = -1
                kt.last_checked = get_tr_now()
                db.session.commit()
                continue

            # Önceki değerleri kaydet (UI'da delta gösterimi için)
            prev_page_for_notif = kt.current_page or 0
            prev_rank_for_notif = kt.current_rank or 0
            kt.previous_page = prev_page_for_notif
            kt.previous_rank = prev_rank_for_notif
            kt.current_page = page
            kt.current_rank = rank
            kt.last_checked = get_tr_now()

            # ── HOTFIX 1.99: SEO Sıralama Değişim Bildirimi ──
            # Sadece gerçek değişiklik varsa bildirim üret (önceki ölçüm vardı + farklı).
            # İlk ölçümler (previous=0) için bildirim üretme — kullanıcı zaten ekledi.
            try:
                if prev_page_for_notif > 0 and prev_rank_for_notif > 0:
                    if (page != prev_page_for_notif) or (rank != prev_rank_for_notif):
                        new_overall = (page - 1) * 40 + rank if (page > 0 and rank > 0) else 0
                        old_overall = (prev_page_for_notif - 1) * 40 + prev_rank_for_notif
                        # Yön: 1. sıra en iyi → overall_rank küçülürse YÜKSELDİ
                        if new_overall == 0:
                            seo_emoji = '⚠️'
                            seo_kind  = 'kaybolundu'
                        elif new_overall < old_overall:
                            seo_emoji = '🏆' if new_overall <= 5 else '📈'
                            seo_kind  = 'yükseldi'
                        else:
                            seo_emoji = '📉'
                            seo_kind  = 'düştü'

                        # Ürün adı: TrackedProduct → product_name; yoksa URL slug
                        urn_ad = ''
                        try:
                            tp_lookup = TrackedProduct.query.filter_by(
                                user_id=kt.user_id, url=kt.target_url
                            ).first()
                            if tp_lookup and tp_lookup.product_name:
                                urn_ad = ' '.join(tp_lookup.product_name.split()[:5])
                                if len(tp_lookup.product_name.split()) > 5:
                                    urn_ad += '...'
                        except Exception:
                            pass
                        if not urn_ad:
                            try:
                                import re as _re_seo
                                m = _re_seo.search(r'/([^/]+)-p-\d+', kt.target_url or '')
                                if m:
                                    urn_ad = m.group(1).replace('-', ' ')[:50]
                            except Exception:
                                pass
                        if not urn_ad:
                            urn_ad = 'Ürün'

                        if new_overall == 0:
                            seo_msg = (
                                f'{seo_emoji} SEO Sıralaması Değişti: {urn_ad}, '
                                f'"{kt.keyword}" aramasında ilk 5 sayfada bulunamadı. '
                                f'(Eski: Sayfa {prev_page_for_notif}, Sıra {prev_rank_for_notif})'
                            )
                        else:
                            seo_msg = (
                                f'{seo_emoji} SEO Sıralaması Değişti: {urn_ad}, '
                                f'"{kt.keyword}" aramasında Sayfa {page}, Sıra {rank} oldu ({seo_kind})! '
                                f'(Eski: Sayfa {prev_page_for_notif}, Sıra {prev_rank_for_notif})'
                            )
                        # internal_link: SEO grafik sayfasında grup anchor
                        seo_internal = '/seo-graph'
                        if kt.group_id:
                            seo_internal = f'/seo-graph#group-{kt.group_id}'
                        db.session.add(Notification(
                            user_id=kt.user_id,
                            message=seo_msg,
                            link=kt.target_url,
                            internal_link=seo_internal,
                            category='seo',
                        ))
                        # HOTFIX 10.5: Başarılı bildirim üretimi log'u (görev gereği —
                        # bir daha sessizce kaybolmasın diye). Eski 'log.info' tek
                        # taraflıydı (sadece hata için), oysa pozitif sinyal de gerek.
                        log.info(
                            "[SEO Notif] user_id=%s tracker=%s '%s' %s→%s "
                            "(sayfa %s→%s, sıra %s→%s) bildirim oluşturuldu",
                            kt.user_id, kt.id, kt.keyword, old_overall, new_overall,
                            prev_page_for_notif, page, prev_rank_for_notif, rank,
                        )
            except Exception as _seo_notif_e:
                # SEO bildirim üretimi başarısızsa ana akış bozulmasın.
                # HOTFIX 10.5: log.info → log.exception (stack trace dahil). Eski
                # davranışta NameError gibi kritik hatalar SEVİYE INFO ile
                # geçiyordu, Sentry'ye düşmüyordu, sebebi hiç anlaşılmıyordu.
                log.exception(
                    "[SEO Notif] tracker %s bildirim oluşturma BAŞARISIZ: %s",
                    kt.id, _seo_notif_e,
                )
            # HOTFIX 1.91: Bağlı KeywordPool'a da yaz (paylaşımlı state)
            if kt.pool_id:
                try:
                    pool = KeywordPool.query.get(kt.pool_id)
                    if pool:
                        pool.current_page = page
                        pool.current_rank = rank
                        pool.last_checked = kt.last_checked
                except Exception:
                    pass
            # HOTFIX 1.84: Tarihsel kayıt — grafik için zaman serisi
            # overall_rank = global sıralama (sayfa 2, sıra 5 → 45.). 0 = bulunamadı.
            overall = ((page - 1) * 40 + rank) if (page > 0 and rank > 0) else 0
            try:
                db.session.add(SEOHistory(
                    keyword_tracker_id=kt.id,
                    page=page if page > 0 else 0,
                    rank=rank if rank > 0 else 0,
                    overall_rank=overall,
                ))
            except Exception as _hist_e:
                # Tarihsel kayıt başarısız olursa ana güncelleme bozulmasın
                log.info(f"[SEO] history yazma hatası tracker {kt.id}: {_hist_e}")
            db.session.commit()
        except Exception as e:
            log.info(f"[SEO] tracker {kt.id} hata: {e}")
            try:
                kt.last_checked = get_tr_now()
                db.session.commit()
            except Exception:
                db.session.rollback()


def check_blocked(page):
    """Check if page is blocked by anti-bot."""
    import time
    for _ in range(2):
        status = page.evaluate("""() => {
            var t = document.title.toLowerCase().trim(); 
            var b = document.body.innerText.toLowerCase();
            if (t.includes('robot') || t.includes('captcha') || b.includes('robot musunuz') || t === 'hepsiburada.com') {
                if (!document.querySelector('#product-name') && !document.querySelector('.product-name')) return 'BLOCKED';
            }
            if (document.querySelector('h1') === null && document.querySelector('img') === null) return 'NOT_LOADED';
            return 'OK';
        }""")
        if status == 'BLOCKED' or status == 'NOT_LOADED':
            time.sleep(3)
            page.reload()
            time.sleep(6)
        else:
            return status
    return status


def extract_product_name(page):
    """Extract product name from current page."""
    _, _, urun_ismi_temizle, _, _, _ = _import_bmk_utils()
    try:
        raw = page.evaluate("""() => {
            var d = window.location.hostname;
            if (d.includes('trendyol.com')) { var el = document.querySelector('.pr-new-br h1, h1.product-name'); if (el) return el.innerText; }
            if (d.includes('hepsiburada.com')) { var el = document.querySelector('#product-name, h1[itemprop="name"]'); if (el) return el.innerText; }
            var h1 = document.querySelector('h1'); if (h1) return h1.innerText;
            return document.title.split('|')[0].split('-')[0].trim();
        }""")
        return urun_ismi_temizle(raw)
    except Exception:
        return "İsim Bulunamadı"


def extract_seller_name(page):
    """Extract seller name from current page.

    HB özel: 'Satıcı: UMT TEDARIK' metni product-info-container içinde. Ayrıca
    __NEXT_DATA__ içinde `merchant.name` / `variantList[0].merchant.name`
    alanında da geçer. Bu fonksiyon önce DOM selector'larını, sonra text-based
    arama, en son JSON fallback yapar.

    HOTFIX 1.34: Lazy-load skeleton ekranlarda DOM/JS henüz merchant bölümünü
    render etmediği için tüm katmanlar boş dönüp "Marka Bulunamadı" çıkıyordu.
    Şimdi:
      1) merchant DOM elementlerinden BİRİ belirene kadar 5sn wait_for_selector
      2) Hâlâ yoksa kademeli scroll (lazy-loader tetikle) + tekrar dene
      3) Tüm Playwright katmanları başarısızsa, page.content()'i alıp
         extract_data_from_html() içindeki robust JSON walker'a teslim et
    """
    # ── 1) Merchant elementlerinin render edilmesini bekle (5sn) ──
    try:
        merchant_anchors = (
            ".merchant-box a, .seller-store a, .merchant-text, .seller-name, "
            "[data-testid='merchant-name'], [data-test-id='merchant-name'], "
            "[data-test-id='seller-name'], a[href*='/magaza/'], a[href*='/satici/']"
        )
        page.wait_for_selector(merchant_anchors, timeout=5000, state="attached")
    except Exception:
        # Render olmadıysa kademeli scroll + 1.5sn daha bekle
        try:
            for _ in range(3):
                page.evaluate("window.scrollBy(0, 600);")
                page.wait_for_timeout(500)
            page.wait_for_timeout(1500)
        except Exception:
            pass

    try:
        dom_result = page.evaluate(r"""() => {
            var s = '';
            // ── Trendyol selector'ları ──
            var ty = document.querySelector('.merchant-box a, .seller-store a, .merchant-text, .seller-name, [data-testid="merchant-name"]');
            if(ty) s = ty.innerText.trim();
            // ── Hepsiburada selector'ları (genişletilmiş) ──
            if(!s) {
                var hb = document.querySelector(
                    '[data-test-id="merchant-name"], [data-test-id="seller-name"], ' +
                    '[data-bind*="merchantName"], .merchantLink, ' +
                    'a[href*="/magaza/"], a[href*="/satici/"], ' +
                    '.seller-name, .merchant-name, #merchantName, ' +
                    'span[itemprop="seller"], div[itemprop="seller"] [itemprop="name"]'
                );
                if(hb) s = hb.innerText.trim();
            }
            // ── HB "Satıcı: XYZ" text-based fallback ──
            if(!s) {
                var all = document.querySelectorAll('div, span, p');
                for (var i = 0; i < all.length; i++) {
                    var el = all[i];
                    if (el.offsetParent === null) continue;
                    var txt = (el.innerText || '').trim();
                    // "Satıcı: UMT TEDARIK" yakalayıcı
                    var m = txt.match(/^Satıcı[:\s]+([A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü0-9\s&.\-']{1,60})$/);
                    if (m && m[1] && m[1].length > 1 && m[1].length < 60) {
                        s = m[1].trim();
                        break;
                    }
                    // Sadece "Satıcı:" sonra alt eleman
                    if (txt === 'Satıcı:' || txt.toLowerCase() === 'satıcı') {
                        var next = el.nextElementSibling || el.parentElement?.nextElementSibling;
                        if (next) {
                            var nt = (next.innerText || '').trim().split('\n')[0];
                            if (nt && nt.length > 1 && nt.length < 60) { s = nt; break; }
                        }
                    }
                }
            }
            // ── Marka fallback ──
            if(!s || s.length < 2) {
                var brand = document.querySelector('.pr-new-br a, .pr-new-br span, .brand-name, .product-brand, [data-test-id="brand-name"]');
                if(brand && brand.innerText.trim().length > 1) { s = "Marka: " + brand.innerText.trim(); }
            }
            if (s) {
                s = s.split('\n')[0]
                     .replace(/[0-9]+,[0-9]+.*/g, '')
                     .replace(/Takip et/gi, '')
                     .replace(/Satıcıya sor/gi, '')
                     .replace(/\s+/g, ' ')
                     .trim();
            }
            // HOTFIX 1.25: "Trendyol'da Satış Yap", "Mağazanı Aç", "Hepsiburada'da Satış Yap"
            // gibi promo/CTA metinleri satıcı ismi olarak kabul edilmez.
            var bogus = /(satış\s*yap|mağazanı\s*aç|mağaza\s*aç|hesap\s*oluştur|trendyol'?da|hepsiburada'?da|n11'?de)/i;
            if (s && bogus.test(s)) s = '';
            return (s && s.length > 1) ? s : '';
        }""")
        # HOTFIX 1.25: Python tarafında ikinci güvenlik filtresi —
        # JS regex'ten kaçan promo/CTA metinleri burada da elenir.
        _BOGUS_SELLER_RE = _re_hotfix.compile(
            r"(satış\s*yap|mağazanı\s*aç|mağaza\s*aç|hesap\s*oluştur|"
            r"trendyol'?da|hepsiburada'?da|n11'?de|sign\s*up|sell\s*on)",
            _re_hotfix.IGNORECASE,
        )
        if dom_result and len(dom_result) > 1 and not _BOGUS_SELLER_RE.search(dom_result):
            return dom_result

        # ── JSON Fallback: __NEXT_DATA__ / JSON-LD derinlikte satıcı ara ──
        json_result = page.evaluate(r"""() => {
            function deepFindSeller(obj, depth) {
                if (!obj || depth > 8) return null;
                if (typeof obj !== 'object') return null;
                // Preferred keys
                var keys = ['merchantName','sellerName','merchant','seller'];
                for (var k of keys) {
                    if (obj[k]) {
                        if (typeof obj[k] === 'string' && obj[k].length > 1) return obj[k];
                        if (typeof obj[k] === 'object') {
                            if (obj[k].name && typeof obj[k].name === 'string') return obj[k].name;
                            if (obj[k].legalName && typeof obj[k].legalName === 'string') return obj[k].legalName;
                        }
                    }
                }
                if (Array.isArray(obj)) {
                    for (var it of obj) {
                        var r = deepFindSeller(it, depth + 1);
                        if (r) return r;
                    }
                } else {
                    for (var kk in obj) {
                        var r = deepFindSeller(obj[kk], depth + 1);
                        if (r) return r;
                    }
                }
                return null;
            }
            // 1) __NEXT_DATA__
            var nd = document.getElementById('__NEXT_DATA__');
            if (nd) {
                try {
                    var j = JSON.parse(nd.innerText);
                    var r = deepFindSeller(j, 0);
                    if (r) return r;
                } catch(e) {}
            }
            // 2) JSON-LD scripts
            var lds = document.querySelectorAll('script[type="application/ld+json"]');
            for (var i = 0; i < lds.length; i++) {
                try {
                    var j = JSON.parse(lds[i].innerText);
                    var r = deepFindSeller(j, 0);
                    if (r) return r;
                } catch(e) {}
            }
            // 3) window.__INITIAL_STATE__ / __PRELOADED_STATE__
            try {
                if (window.__INITIAL_STATE__) {
                    var r = deepFindSeller(window.__INITIAL_STATE__, 0);
                    if (r) return r;
                }
                if (window.__PRELOADED_STATE__) {
                    var r = deepFindSeller(window.__PRELOADED_STATE__, 0);
                    if (r) return r;
                }
            } catch(e) {}
            return '';
        }""")
        # HOTFIX 1.33 (REGRESSION FIX): JSON katmanından gelen merchant.name ZATEN
        # kesin doğru kaynak. CTA filtresini buraya uygulamak gerçek satıcı adlarını
        # (içinde "trendyol" geçenler vb.) yanlışlıkla "Marka Bulunamadı"'ya çeviriyordu.
        # Eski çalışan davranış: JSON sonucunu olduğu gibi döndür.
        if json_result and len(json_result) > 1:
            return json_result.strip()
    except Exception as e:
        log.info(f"[extract_seller_name] error: {e}")

    # ── HOTFIX 1.34: Son katman — page.content() ile robust HTML extractor ──
    # Playwright DOM tarafı skeleton/lazy-load yüzünden boş kalsa bile sayfanın
    # raw HTML kaynağında __INITIAL_STATE__ JSON'u inline mevcut oluyor.
    # extract_data_from_html zaten dengeli-paranthese tarayıcısı + dinamik script
    # tarayıcısı + "Satıcı:" metin-komşu mantığını içeriyor → en güvenilir yol.
    try:
        page_url = ""
        try:
            page_url = page.url or ""
        except Exception:
            pass
        html = ""
        try:
            html = page.content()
        except Exception:
            pass
        if html:
            html_data = extract_data_from_html(html, page_url)
            seller = html_data.get("seller")
            brand = html_data.get("brand")
            # HOTFIX 1.35: label/etiket sahte değer filtresi — "Satıcı", "Mağaza"
            # gibi label metni seller olarak kabul edilmez.
            import re as _re_h35
            _label_only = _re_h35.compile(
                r'^\s*(?:satıcı|mağaza|seller|merchant|brand|marka)\s*[:\-]?\s*$',
                _re_h35.IGNORECASE,
            )

            def _valid_seller(s):
                return (
                    s and isinstance(s, str) and len(s.strip()) > 2
                    and not _label_only.match(s.strip())
                )

            if _valid_seller(seller):
                log.info(f"[extract_seller_name] 🛟 HTML extractor kurtardı: seller='{seller}'")
                return seller.strip()
            if _valid_seller(brand):
                log.info(f"[extract_seller_name] 🛟 HTML extractor brand fallback: brand='{brand}'")
                return f"Marka: {brand.strip()}"

        # HOTFIX 1.35: URL'deki merchantId param'ını son çare olarak göster.
        # Trendyol ürün URL'leri genelde ?merchantId=NNNN içerir; isim yerine kimlik
        # döndürmek "Marka Bulunamadı" yazmaktan iyidir.
        try:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(page_url or "").query)
            mid_list = qs.get("merchantId") or qs.get("merchantid") or []
            if mid_list and str(mid_list[0]).isdigit():
                mid = str(mid_list[0])
                log.info(f"[extract_seller_name] ℹ️ URL merchantId fallback: {mid}")
                return f"Mağaza #{mid}"
        except Exception:
            pass
    except Exception as html_fb_err:
        log.info(f"[extract_seller_name] HTML fallback hata: {html_fb_err}")

    # HOTFIX 1.29: Olay Yeri İncelemesi — Playwright satıcıyı bulamazsa o anki
    # sayfanın görüntüsünü kaydet. Sayfa gerçekten ürün sayfası mı, yoksa
    # Cloudflare/DataDome/CAPTCHA ekranı mı, gözle ayırt edebilelim.
    try:
        page.screenshot(path="debug_trendyol_error.png", full_page=True)
        log.info("[extract_seller_name] 📸 Satıcı bulunamadı — debug_trendyol_error.png kaydedildi")
    except Exception as ss_err:
        log.info(f"[extract_seller_name] screenshot atlandı: {ss_err}")
    return "Marka Bulunamadı"


def _extract_balanced_json_after(text, marker):
    """HOTFIX 1.41: marker'dan sonraki ilk '{' karakterinden başlayıp
    bracket sayımı ile dengeli JSON bloğunu döndür. Regex'in non-greedy
    `.+?;` yaklaşımı string içinde ';' veya '}' geçtiğinde erken kesiyordu;
    bu yardımcı string-aware bracket matching yapar."""
    if not text or not marker:
        return None
    idx = text.find(marker)
    if idx < 0:
        return None
    start = text.find('{', idx + len(marker))
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    end_cap = min(len(text), start + 5_000_000)  # 5MB güvenlik tavanı
    for i in range(start, end_cap):
        c = text[i]
        if esc:
            esc = False
            continue
        if c == '\\':
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def extract_data_from_html(html_content, url):
    """
    Reverse Engineering: Extract hidden JSON state from HTML using BS4.
    Used as fallback when Playwright is blocked or for non-JS scraping.

    HOTFIX 1.34: CSS selector'lere bağımlılığı azalt — önce regex ile
    `window.__INITIAL_STATE__` veya `window.__storeData` JSON'unu yakala,
    parse et, price/seller/brand'i doğrudan oradan al. CSS selector'lar
    sadece son çare olarak kalıyor.

    HOTFIX 1.41: Trendyol Next.js'e geçince `__INITIAL_STATE__` regex'i
    erken kesiyordu. Yeni yaklaşım: balanced-bracket çıkarıcı + Next.js
    `__NEXT_DATA__` desteği. Önce dengeli JSON dene, sonra regex fallback.
    """
    from bs4 import BeautifulSoup
    import json
    import re

    soup = BeautifulSoup(html_content, 'lxml')
    # FAZ 4: rating + review_count alanları eklendi (None/0 = bulunamadı)
    # HOTFIX 1.27: seller + brand alanları eklendi (Marka Bulunamadı bypass'ı için).
    # HOTFIX 1.34: content_id alanı eklendi (URL'den çekilir, API çağrısı için).
    data = {"price": "Bulunamadı", "stock": -1, "name": "İsim Bulunamadı",
            "rating": None, "review_count": 0,
            "seller": None, "brand": None, "content_id": None}

    # ── HOTFIX 1.34: content_id'yi URL'den çek (en güvenilir kaynak) ──
    # Trendyol URL formatı: .../<slug>-p-{contentId}?...
    # Bu ID, tüm API çağrıları için anahtardır.
    try:
        m_cid = re.search(r'-p-(\d{6,12})(?:[/?#&]|$)', url or "")
        if m_cid:
            data["content_id"] = m_cid.group(1)
    except Exception:
        pass

    # ── HOTFIX 1.34 / 1.41: Proaktif regex + balanced-bracket ile JSON state ──
    # Trendyol farklı versiyonlarda farklı isim kullanıyor:
    #  - Eski: window.__INITIAL_STATE__, window.__storeData
    #  - Next.js (yeni): <script id="__NEXT_DATA__">...</script>
    # Önce dengeli JSON çıkarıcı dener (string-içi ; ve } sorunu yok),
    # sonra regex fallback'e döner.
    try:
        proactive_state = None
        # 1) __NEXT_DATA__ — Next.js sayfaları (Trendyol'un yeni mimarisi)
        try:
            m_next = re.search(
                r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(\{[\s\S]*?\})</script>',
                html_content or ""
            )
            if m_next:
                try:
                    nd = json.loads(m_next.group(1))
                    # Next.js standart yolu: props.pageProps.{initialState|product|productDetail}
                    page_props = ((nd.get('props') or {}).get('pageProps') or {})
                    proactive_state = (
                        page_props.get('initialState')
                        or page_props.get('initialReduxState')
                        or page_props
                    )
                except Exception:
                    pass
        except Exception:
            pass

        # 2) Balanced-bracket extraction — string-içi ';' sorununu çözer
        if proactive_state is None:
            for marker in (
                'window.__INITIAL_STATE__',
                'window.__storeData',
                'window.__PRELOADED_STATE__',
                'window.__STATE__',
                'window.__NUXT__',
            ):
                if marker not in (html_content or ""):
                    continue
                blob = _extract_balanced_json_after(html_content, marker + '=')
                if not blob:
                    blob = _extract_balanced_json_after(html_content, marker + ' =')
                if not blob:
                    continue
                try:
                    proactive_state = json.loads(blob)
                    break
                except Exception:
                    try:
                        fix = re.sub(r'([{,])\s*([A-Za-z_][\w$]*)\s*:', r'\1"\2":', blob)
                        fix = fix.replace("'", '"')
                        proactive_state = json.loads(fix)
                        break
                    except Exception:
                        continue

        # 3) Son çare: eski non-greedy regex fallback (geriye dönük uyumluluk)
        if proactive_state is None:
            state_patterns = [
                r'window\.__INITIAL_STATE__\s*=\s*({.+?})\s*;\s*(?:</script>|window\.)',
                r'window\.__INITIAL_STATE__\s*=\s*({.+?})\s*;',
                r'window\.__storeData\s*=\s*({.+?})\s*;\s*(?:</script>|window\.)',
                r'window\.__storeData\s*=\s*({.+?})\s*;',
                r'window\.__PRELOADED_STATE__\s*=\s*({.+?})\s*;',
                r'window\.__NUXT__\s*=\s*({.+?})\s*;',
            ]
            for pat in state_patterns:
                m = re.search(pat, html_content or "", re.DOTALL)
                if m:
                    blob = m.group(1)
                    try:
                        proactive_state = json.loads(blob)
                        break
                    except Exception:
                        try:
                            fix = re.sub(r'([{,])\s*([A-Za-z_][\w$]*)\s*:', r'\1"\2":', blob)
                            fix = fix.replace("'", '"')
                            proactive_state = json.loads(fix)
                            break
                        except Exception:
                            continue

        if isinstance(proactive_state, dict):
            # HOTFIX 1.41: Trendyol Next.js'te product objesi farklı path'lerde
            # bulunabiliyor — geniş havuz tarayıcısı + DERİN tarama:
            prod = (proactive_state.get('product')
                    or (proactive_state.get('productDetail') or {}).get('product')
                    or proactive_state.get('detail')
                    or (proactive_state.get('initialReduxState') or {}).get('product')
                    or (proactive_state.get('pageProps') or {}).get('product')
                    or {})

            # Hâlâ boşsa, derin DFS ile 'price' içeren ilk dict'i bul
            if not (isinstance(prod, dict) and prod):
                def _deep_find_product(obj, depth=0):
                    if depth > 6 or not isinstance(obj, (dict, list)):
                        return None
                    if isinstance(obj, dict):
                        # Aday: 'price' anahtarı dict ise ve içinde discounted/selling varsa
                        pr = obj.get('price')
                        if isinstance(pr, dict) and any(
                            k in pr for k in ('discountedPrice', 'originalPrice', 'sellingPrice')
                        ):
                            return obj
                        if isinstance(pr, (int, float)) and pr > 0 and (obj.get('name') or obj.get('title')):
                            return obj
                        for v in obj.values():
                            r = _deep_find_product(v, depth + 1)
                            if r:
                                return r
                    elif isinstance(obj, list):
                        for it in obj[:50]:  # ilk 50 element ile sınırla
                            r = _deep_find_product(it, depth + 1)
                            if r:
                                return r
                    return None
                deep = _deep_find_product(proactive_state)
                if deep:
                    prod = deep

            if isinstance(prod, dict):
                # Price (HOTFIX 1.41: hem dict hem scalar varyantları destekle)
                if data["price"] == "Bulunamadı":
                    cp = prod.get('price')
                    if isinstance(cp, dict):
                        for k in ('discountedPrice', 'originalPrice', 'sellingPrice', 'productSalePrice'):
                            v = cp.get(k)
                            if isinstance(v, dict) and v.get('value'):
                                data["price"] = str(v['value'])
                                break
                            elif isinstance(v, (int, float)) and v > 0:
                                data["price"] = str(v)
                                break
                    elif isinstance(cp, (int, float)) and cp > 0:
                        data["price"] = str(cp)
                # name (HOTFIX 1.41: title fallback eklendi)
                if data["name"] == "İsim Bulunamadı":
                    nm = prod.get('name') or prod.get('title') or prod.get('productName')
                    if isinstance(nm, str) and len(nm.strip()) > 1:
                        data["name"] = nm.strip()
                # Seller (merchant.name)
                if not data.get("seller"):
                    mer = prod.get('merchant') or {}
                    if isinstance(mer, dict):
                        nm = mer.get('name') or mer.get('legalName')
                        if isinstance(nm, str) and len(nm.strip()) > 1:
                            data["seller"] = nm.strip()
                    if not data.get("seller"):
                        # variantList[0].merchant.name
                        vl = prod.get('variantList') or []
                        if isinstance(vl, list) and vl and isinstance(vl[0], dict):
                            vm = vl[0].get('merchant') or {}
                            if isinstance(vm, dict):
                                nm = vm.get('name') or vm.get('legalName')
                                if isinstance(nm, str) and len(nm.strip()) > 1:
                                    data["seller"] = nm.strip()
                # Brand
                if not data.get("brand"):
                    br = prod.get('brand') or {}
                    if isinstance(br, dict):
                        bn = br.get('name')
                        if isinstance(bn, str) and len(bn.strip()) > 1:
                            data["brand"] = bn.strip()
                    elif isinstance(br, str) and br.strip():
                        data["brand"] = br.strip()
                # Name
                if data["name"] == "İsim Bulunamadı":
                    nm = prod.get('name')
                    if isinstance(nm, str) and len(nm.strip()) > 3:
                        data["name"] = nm.strip()[:200]
    except Exception as proactive_err:
        log.info(f"[Worker] proaktif JSON state extraction skipped: {proactive_err}")

    def _safe_float(x):
        try:
            v = float(x)
            return v if 0 < v <= 5.0 else None
        except (TypeError, ValueError):
            return None

    def _safe_int(x):
        try:
            v = int(x)
            return v if v >= 0 else 0
        except (TypeError, ValueError):
            return 0

    try:
        if "trendyol.com" in url:
            for script in soup.find_all('script'):
                if 'window.__INITIAL_STATE__' in script.text:
                    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', script.text)
                    if match:
                        state = json.loads(match.group(1))
                        product = state.get('product', {})
                        if not product:
                             product = state.get('productDetail', {}).get('product', {})

                        if product:
                            data["name"] = product.get('name', data["name"])
                            cp = product.get('price', {})
                            if cp:
                                # Öncelik: discountedPrice (standart) > originalPrice > sellingPrice (Plus/Sepette)
                                dp = cp.get('discountedPrice', {}).get('value') if isinstance(cp.get('discountedPrice'), dict) else None
                                op = cp.get('originalPrice', {}).get('value') if isinstance(cp.get('originalPrice'), dict) else None
                                sp = cp.get('sellingPrice', {}).get('value') if isinstance(cp.get('sellingPrice'), dict) else None
                                data["price"] = str(dp or op or sp or 'Bulunamadı')

                            # HOTFIX 1.27: Trendyol satıcı / marka kesin çekimi
                            # product.merchant.name ve product.brand.name yolları
                            # HOTFIX 1.28: bogus CTA metni filtresi her assignment'a uygulandı.
                            _BOGUS_TY = re.compile(
                                r'(satış\s*yap|mağazanı\s*aç|mağaza\s*aç|hesap\s*oluştur|sign\s*up|sell\s*on)',
                                re.IGNORECASE,
                            )
                            def _ok(s):
                                return bool(s) and isinstance(s, str) and len(s.strip()) > 1 and not _BOGUS_TY.search(s)
                            try:
                                m_obj = product.get('merchant') or {}
                                if isinstance(m_obj, dict):
                                    m_name = m_obj.get('name') or m_obj.get('legalName')
                                    if _ok(m_name):
                                        data["seller"] = m_name.strip()
                                # variantList[0].merchant.name fallback
                                if not data.get("seller"):
                                    vl = product.get('variantList') or []
                                    if isinstance(vl, list) and vl:
                                        v0 = vl[0] if isinstance(vl[0], dict) else {}
                                        vm = v0.get('merchant') or {}
                                        if isinstance(vm, dict):
                                            vm_name = vm.get('name') or vm.get('legalName')
                                            if _ok(vm_name):
                                                data["seller"] = vm_name.strip()
                                # brand
                                b_obj = product.get('brand') or {}
                                if isinstance(b_obj, dict):
                                    b_name = b_obj.get('name')
                                    if _ok(b_name):
                                        data["brand"] = b_name.strip()
                                elif _ok(b_obj):
                                    data["brand"] = b_obj.strip()
                            except Exception as ms_err:
                                log.info(f"[Worker] TY merchant/brand parse skipped: {ms_err}")

                            # FAZ 4: Trendyol — ratingScore.averageRating + commentCount/totalRatingCount
                            try:
                                rs = product.get('ratingScore', {}) or {}
                                avg = rs.get('averageRating') or rs.get('avgRating') or product.get('averageRating')
                                rating_val = _safe_float(avg)
                                if rating_val is not None:
                                    data["rating"] = round(rating_val, 2)
                                # HOTFIX 1.25: ek key fallback'leri eklendi.
                                # Trendyol son güncellemesinde commentCount → ratingCount yer değiştiriyor.
                                rc = (product.get('commentCount')
                                      or product.get('totalCommentCount')
                                      or product.get('totalRatingCount')
                                      or product.get('ratingCount')
                                      or product.get('reviewCount')
                                      or rs.get('totalCount')
                                      or rs.get('commentCount')
                                      or rs.get('totalCommentCount')
                                      or rs.get('totalRatingCount'))
                                data["review_count"] = _safe_int(rc) if rc is not None else 0
                            except Exception as ty_rev_err:
                                log.info(f"[Worker] TY rating parse skipped: {ty_rev_err}")

                            # [DEVRE DIŞI] Stok çıkarımı uyku modunda — data["stock"] = -1 sabit.
                            data["stock"] = -1
                        break
        elif "hepsiburada.com" in url:
            for script in soup.find_all('script'):
                if 'window.__NEXT_DATA__' in script.text:
                    state = json.loads(script.text)
                    prod = state.get('props', {}).get('pageProps', {}).get('product', {})
                    if prod:
                        data["name"] = prod.get('name', data["name"])
                        listing = prod.get('currentListing', {})
                        if listing:
                            data["price"] = str(listing.get('price', {}).get('value', 'Bulunamadı'))
                            data["stock"] = -1

                        # FAZ 4: Hepsiburada — averageRating + reviewCount/numberOfReviews
                        try:
                            avg = (prod.get('averageRating')
                                   or prod.get('rating', {}).get('average') if isinstance(prod.get('rating'), dict) else prod.get('rating'))
                            rating_val = _safe_float(avg)
                            if rating_val is not None:
                                data["rating"] = round(rating_val, 2)
                            rc = (prod.get('reviewCount')
                                  or prod.get('numberOfReviews')
                                  or prod.get('totalReviewCount')
                                  or (prod.get('rating', {}).get('count') if isinstance(prod.get('rating'), dict) else None))
                            data["review_count"] = _safe_int(rc) if rc is not None else 0
                        except Exception as hb_rev_err:
                            log.info(f"[Worker] HB rating parse skipped: {hb_rev_err}")
                    break

        # ── FAZ 4: ld+json structured data (genel fallback — her iki platform için) ──
        # Hâlâ rating yoksa ld+json'dan aggregateRating çekmeyi dene.
        if data["rating"] is None:
            try:
                for tag in soup.find_all("script", type="application/ld+json"):
                    if not tag.string:
                        continue
                    try:
                        ld = json.loads(tag.string)
                    except Exception:
                        continue
                    items = ld if isinstance(ld, list) else (ld.get("@graph", [ld]) if isinstance(ld, dict) else [ld])
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        if item.get("@type") in ("Product", "AggregateRating") or "aggregateRating" in item:
                            ar = item.get("aggregateRating") or item
                            rv = _safe_float(ar.get("ratingValue"))
                            if rv is not None:
                                data["rating"] = round(rv, 2)
                            rc = ar.get("reviewCount") or ar.get("ratingCount")
                            if rc is not None:
                                data["review_count"] = _safe_int(rc)
                            if data["rating"] is not None:
                                break
                    if data["rating"] is not None:
                        break
            except Exception:
                pass

    except Exception as e:
        log.info(f"[Worker] Error extracting data from HTML: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # HOTFIX 1.26: BS4 PRICE FALLBACK ZİNCİRİ
    # __INITIAL_STATE__ / __NEXT_DATA__ JSON'u yoksa veya parse hatası varsa
    # CSS selector zinciri + JSON-LD offers.price ile fiyatı yakala.
    # ─────────────────────────────────────────────────────────────────────────
    try:
        if not data.get("price") or data["price"] == "Bulunamadı":
            price_selectors = [
                # Trendyol indirimli + standart fiyat
                ".prc-dsc",
                ".prc-slg",
                ".product-price-container .prc-dsc",
                ".pr-bx-w .prc-dsc",
                ".pr-bx-pr-dsc",
                "[data-testid='price-current-price']",
                "[data-test-id='price-current-price']",
                # HB
                "[data-test-id='default-price']",
                "[data-test-id='price-current-price']",
                "span.product-price",
                "div.product-price",
                # Generic
                "[itemprop='price']",
                "meta[itemprop='price']",
                ".price-current",
                ".current-price",
                ".product-price",
            ]
            for sel in price_selectors:
                el = soup.select_one(sel)
                if not el:
                    continue
                # meta etiketi → content; diğerleri → text
                raw = el.get("content") if el.name == "meta" else el.get_text(strip=True)
                if not raw:
                    continue
                # Türkçe TL formatı: "1.299,90 TL" → "1299.90"
                cleaned = (raw
                           .replace("₺", "").replace("TL", "")
                           .replace(".", "").replace(",", ".")
                           .strip())
                # Sadece sayı kalmasını bekliyoruz
                m = re.search(r"\d+(?:\.\d+)?", cleaned)
                if m:
                    data["price"] = m.group(0)
                    break

        # JSON-LD offers.price fallback
        if not data.get("price") or data["price"] == "Bulunamadı":
            for tag in soup.find_all("script", type="application/ld+json"):
                if not tag.string:
                    continue
                try:
                    ld = json.loads(tag.string)
                except Exception:
                    continue
                items = ld if isinstance(ld, list) else (ld.get("@graph", [ld]) if isinstance(ld, dict) else [ld])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    offers = item.get("offers") or {}
                    if isinstance(offers, list) and offers:
                        offers = offers[0]
                    if isinstance(offers, dict):
                        p = offers.get("price") or offers.get("lowPrice") or offers.get("highPrice")
                        if p:
                            data["price"] = str(p)
                            break
                if data.get("price") and data["price"] != "Bulunamadı":
                    break
    except Exception as price_fb_err:
        log.info(f"[Worker] price fallback skipped: {price_fb_err}")

    # ─────────────────────────────────────────────────────────────────────────
    # HOTFIX 1.26: BS4 NAME / TITLE FALLBACK ZİNCİRİ
    # Ürün ismi JSON'dan gelmediyse <h1>, og:title, JSON-LD .name vb. dene.
    # ─────────────────────────────────────────────────────────────────────────
    try:
        if not data.get("name") or data["name"] == "İsim Bulunamadı":
            name_selectors = [
                "h1.pr-new-br span",      # Trendyol — marka + ürün adı combo
                "h1.pr-new-br",
                "h1.product-name",
                "h1[class*='product-name']",
                "h1[class*='ProductName']",
                "[data-testid='product-name']",
                "[data-test-id='product-name']",
                "[data-test-id='title']",
                "h1.product-title",
                "h1",                      # son çare: sayfanın ilk h1'i
                "meta[property='og:title']",
            ]
            for sel in name_selectors:
                el = soup.select_one(sel)
                if not el:
                    continue
                raw = el.get("content") if el.name == "meta" else el.get_text(" ", strip=True)
                if raw and len(raw) > 3:
                    data["name"] = raw[:200]
                    break

        # JSON-LD product.name fallback
        if not data.get("name") or data["name"] == "İsim Bulunamadı":
            for tag in soup.find_all("script", type="application/ld+json"):
                if not tag.string:
                    continue
                try:
                    ld = json.loads(tag.string)
                except Exception:
                    continue
                items = ld if isinstance(ld, list) else (ld.get("@graph", [ld]) if isinstance(ld, dict) else [ld])
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        nm = item.get("name")
                        if nm:
                            data["name"] = str(nm)[:200]
                            break
                if data.get("name") and data["name"] != "İsim Bulunamadı":
                    break
    except Exception as name_fb_err:
        log.info(f"[Worker] name fallback skipped: {name_fb_err}")

    # HOTFIX 1.25: Son çare — review_count hâlâ 0 ise raw HTML üzerinde regex denemesi.
    # Trendyol bazen JSON'u page-render ile ayırıyor; "X Değerlendirme" / "X Yorum" gibi
    # görünür metinler regex'le yakalanır.
    try:
        if (not data.get("review_count")) and html_content:
            patterns = [
                r'(\d{1,3}(?:[.,]\d{3})*)\s*(?:Değerlendirme|Yorum|değerlendirme|yorum)',
                r'"reviewCount"\s*:\s*"?(\d+)',
                r'"ratingCount"\s*:\s*"?(\d+)',
                r'"commentCount"\s*:\s*"?(\d+)',
            ]
            for pat in patterns:
                m = re.search(pat, html_content)
                if m:
                    raw = m.group(1).replace('.', '').replace(',', '')
                    val = _safe_int(raw)
                    if val > 0:
                        data["review_count"] = val
                        break
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # HOTFIX 1.27: HARD REGEX FALLBACK — __INITIAL_STATE__ TÜM HTML'DE
    # Yukarıdaki <script> iterasyonu Trendyol'un yeni şablonunda __INITIAL_STATE__'i
    # bulamayabiliyor (DOM dışı, raw string olarak inline). Bu yüzden HTML'in
    # tamamında non-greedy regex'le yakalayıp parse ediyoruz.
    # ─────────────────────────────────────────────────────────────────────────
    try:
        if (not data.get("seller")) or (not data.get("brand")) or (data["price"] == "Bulunamadı"):
            m_state = re.search(
                r'window\.__INITIAL_STATE__\s*=\s*({.*?});\s*(?:</script>|window\.)',
                html_content,
                re.DOTALL,
            )
            if not m_state:
                # Daha gevşek alternatif (closing tag yoksa)
                m_state = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?})\s*;', html_content, re.DOTALL)
            if m_state:
                try:
                    state = json.loads(m_state.group(1))
                except Exception:
                    state = None
                if isinstance(state, dict):
                    prod = (state.get('product')
                            or (state.get('productDetail') or {}).get('product')
                            or {})
                    if isinstance(prod, dict) and prod:
                        # HOTFIX 1.28: bogus filtre — hard regex katmanına da
                        _BOGUS_HR = re.compile(
                            r'(satış\s*yap|mağazanı\s*aç|hesap\s*oluştur|sign\s*up|sell\s*on)',
                            re.IGNORECASE,
                        )
                        # Seller (merchant)
                        if not data.get("seller"):
                            mer = prod.get('merchant') or {}
                            if isinstance(mer, dict):
                                nm = mer.get('name') or mer.get('legalName')
                                if nm and isinstance(nm, str) and len(nm.strip()) > 1 and not _BOGUS_HR.search(nm):
                                    data["seller"] = nm.strip()
                            if not data.get("seller"):
                                vl = prod.get('variantList') or []
                                if isinstance(vl, list) and vl and isinstance(vl[0], dict):
                                    vm = vl[0].get('merchant') or {}
                                    if isinstance(vm, dict):
                                        nm = vm.get('name') or vm.get('legalName')
                                        if nm and isinstance(nm, str) and len(nm.strip()) > 1 and not _BOGUS_HR.search(nm):
                                            data["seller"] = nm.strip()
                        # Brand
                        if not data.get("brand"):
                            br = prod.get('brand') or {}
                            if isinstance(br, dict):
                                bn = br.get('name')
                                if bn and isinstance(bn, str) and len(bn.strip()) > 1 and not _BOGUS_HR.search(bn):
                                    data["brand"] = bn.strip()
                            elif isinstance(br, str) and br.strip() and not _BOGUS_HR.search(br):
                                data["brand"] = br.strip()
                        # Name (eğer hâlâ boşsa)
                        if data.get("name") in (None, "", "İsim Bulunamadı"):
                            nm = prod.get('name')
                            if nm:
                                data["name"] = str(nm)[:200]
                        # Price (hâlâ boşsa)
                        if data["price"] == "Bulunamadı":
                            cp = prod.get('price') or {}
                            if isinstance(cp, dict):
                                for k in ('discountedPrice', 'originalPrice', 'sellingPrice'):
                                    v = cp.get(k)
                                    if isinstance(v, dict) and v.get('value'):
                                        data["price"] = str(v['value'])
                                        break
    except Exception as hard_re_err:
        log.info(f"[Worker] hard __INITIAL_STATE__ regex skipped: {hard_re_err}")

    # Çift güvenlik: JSON-LD'de Product.brand.name son fallback
    try:
        if not data.get("brand"):
            for tag in soup.find_all("script", type="application/ld+json"):
                if not tag.string:
                    continue
                try:
                    ld = json.loads(tag.string)
                except Exception:
                    continue
                items = ld if isinstance(ld, list) else (ld.get("@graph", [ld]) if isinstance(ld, dict) else [ld])
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        b = item.get("brand")
                        if isinstance(b, dict) and b.get("name"):
                            data["brand"] = str(b["name"]).strip()
                            break
                        elif isinstance(b, str) and b.strip():
                            data["brand"] = b.strip()
                            break
                if data.get("brand"):
                    break
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # HOTFIX 1.28: ZIRH DELİCİ #1 — Dinamik <script> JSON tarayıcı
    # __INITIAL_STATE__ farklı isimlerle gelebiliyor (ör. window.__storeData,
    # __NUXT__, __INITIAL_DATA__) ya da hiç olmayabiliyor. Sayfadaki TÜM <script>
    # etiketlerini tarayıp içinde "merchant" / "brand" / "seller" anahtarları
    # geçen JSON-benzeri blokları bulup parse etmeye çalışıyoruz.
    # ─────────────────────────────────────────────────────────────────────────
    if not data.get("seller") or not data.get("brand"):
        try:
            target_kws = ("merchant", "seller", "brand")
            for stag in soup.find_all("script"):
                if data.get("seller") and data.get("brand"):
                    break
                txt = stag.string or stag.get_text() or ""
                if not txt or len(txt) < 50:
                    continue
                low = txt.lower()
                if not any(kw in low for kw in target_kws):
                    continue

                # Adım A: Dengeli süslü-paranteze sahip büyük JSON adaylarını çıkar.
                # Greedy değil — dengeli paranthese tracker ile her açılışı kapanışına eşle.
                candidates = []
                depth = 0
                start = -1
                for i, ch in enumerate(txt):
                    if ch == '{':
                        if depth == 0:
                            start = i
                        depth += 1
                    elif ch == '}' and depth > 0:
                        depth -= 1
                        if depth == 0 and start != -1:
                            blob = txt[start:i + 1]
                            # En az anahtar kelimelerden biri geçsin
                            blob_low = blob.lower()
                            if any(kw in blob_low for kw in target_kws) and len(blob) > 40:
                                candidates.append(blob)
                            start = -1
                            if len(candidates) >= 8:
                                break

                # Adım B: Adayları büyükten küçüğe sırala (büyük olanlar daha bağlamlı)
                candidates.sort(key=len, reverse=True)

                def _deep_find(obj, want):
                    """want: 'merchant' | 'brand' — derinlikte name string'i ara."""
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            kl = str(k).lower()
                            if want == "merchant" and kl in ("merchant", "seller"):
                                if isinstance(v, dict):
                                    nm = v.get("name") or v.get("legalName")
                                    if isinstance(nm, str) and len(nm.strip()) > 1:
                                        return nm.strip()
                                elif isinstance(v, str) and len(v.strip()) > 1:
                                    return v.strip()
                            if want == "merchant" and kl in ("merchantname", "sellername"):
                                if isinstance(v, str) and len(v.strip()) > 1:
                                    return v.strip()
                            if want == "brand" and kl == "brand":
                                if isinstance(v, dict):
                                    nm = v.get("name")
                                    if isinstance(nm, str) and len(nm.strip()) > 1:
                                        return nm.strip()
                                elif isinstance(v, str) and len(v.strip()) > 1:
                                    return v.strip()
                            if want == "brand" and kl in ("brandname",):
                                if isinstance(v, str) and len(v.strip()) > 1:
                                    return v.strip()
                            r = _deep_find(v, want)
                            if r:
                                return r
                    elif isinstance(obj, list):
                        for it in obj:
                            r = _deep_find(it, want)
                            if r:
                                return r
                    return None

                for cand in candidates:
                    if data.get("seller") and data.get("brand"):
                        break
                    parsed = None
                    try:
                        parsed = json.loads(cand)
                    except Exception:
                        # JS object literal olabilir — bilinen düzeltmeler
                        try:
                            fix = re.sub(r'([{,])\s*([A-Za-z_][\w$]*)\s*:', r'\1"\2":', cand)
                            fix = fix.replace("'", '"')
                            parsed = json.loads(fix)
                        except Exception:
                            parsed = None
                    if parsed is None:
                        continue
                    if not data.get("seller"):
                        s = _deep_find(parsed, "merchant")
                        # Yine sahte CTA metni mi kontrol et
                        if s and not re.search(r'(satış\s*yap|mağazanı\s*aç|hesap\s*oluştur)', s, re.I):
                            data["seller"] = s
                    if not data.get("brand"):
                        b = _deep_find(parsed, "brand")
                        if b:
                            data["brand"] = b
        except Exception as dyn_err:
            log.info(f"[Worker] dinamik script tarayıcı hata: {dyn_err}")

    # ─────────────────────────────────────────────────────────────────────────
    # HOTFIX 1.28: ZIRH DELİCİ #2 — "Satıcı:" metin-komşu mantığı (class-bağımsız)
    # CSS class'larına bağımlı kalmadan, "Satıcı:" / "Mağaza:" gibi etiket
    # metinlerinin yanındaki/altındaki anchor / span / div içeriğini çekiyoruz.
    # ─────────────────────────────────────────────────────────────────────────
    if not data.get("seller"):
        try:
            label_re = re.compile(r'^\s*(?:Satıcı|Mağaza|Satıcı Bilgileri)\s*:?\s*$', re.IGNORECASE)
            inline_re = re.compile(r'^\s*Satıcı\s*[:\-]\s*([A-Za-zÇĞİÖŞÜçğıöşü0-9 .&\-\']{2,80})\s*$', re.IGNORECASE)

            def _is_bogus(t):
                if not t:
                    return True
                t = t.strip()
                if len(t) < 2 or len(t) > 80:
                    return True
                # HOTFIX 1.35: label/etiket metinlerini reddet — komşu çekiminde
                # "Satıcı", "Mağaza", "Satıcı:" gibi label'ın kendisi seller olarak
                # döndürülüyordu (UI: "Satıcı: Satıcı"). Aynı zamanda "Sepete Ekle",
                # "Favorilere Ekle" gibi UI etiketlerini de eler.
                if re.match(r'^(?:satıcı|mağaza|satıcı bilgileri|seller|merchant|brand|marka)\s*[:\-]?\s*$', t, re.I):
                    return True
                if re.search(r'(satış\s*yap|mağazanı\s*aç|hesap\s*oluştur|takip\s*et|satıcıya\s*sor|'
                             r'sepete\s*ekle|favorilere\s*ekle|hemen\s*al|'
                             r'^değerlendir|^değerlendirme|^yorum)', t, re.I):
                    return True
                return False

            for el in soup.find_all(["span", "div", "p", "label", "strong", "b"]):
                if data.get("seller"):
                    break
                t = (el.get_text(" ", strip=True) or "")
                if not t:
                    continue
                # 1) "Satıcı: XYZ" tek satırda mı?
                m_inline = inline_re.match(t)
                if m_inline:
                    cand = m_inline.group(1).strip()
                    if not _is_bogus(cand):
                        data["seller"] = cand
                        break
                # 2) Sadece "Satıcı:" → komşu element
                if label_re.match(t):
                    # önce kardeşlere bak
                    for sib in list(el.next_siblings)[:5]:
                        if getattr(sib, "get_text", None):
                            cand = sib.get_text(" ", strip=True).split("\n")[0]
                            if not _is_bogus(cand):
                                data["seller"] = cand
                                break
                    if data.get("seller"):
                        break
                    # sonra parent'ın bir sonraki kardeşi
                    parent = el.parent
                    if parent is not None:
                        for sib in list(parent.next_siblings)[:5]:
                            if getattr(sib, "get_text", None):
                                cand = sib.get_text(" ", strip=True).split("\n")[0]
                                if not _is_bogus(cand):
                                    data["seller"] = cand
                                    break
                    if data.get("seller"):
                        break
                    # son çare: ilk anchor'ın text'i (mağaza linki)
                    a = el.find_next("a")
                    if a is not None:
                        cand = a.get_text(" ", strip=True)
                        if not _is_bogus(cand):
                            data["seller"] = cand
                            break
        except Exception as txt_err:
            log.info(f"[Worker] 'Satıcı:' metin-komşu fallback hata: {txt_err}")

    return data


def extract_price(page):
    """
    Kusursuz Fiyat Çekme: 
    1. Önce window.__INITIAL_STATE__ veya window.__NEXT_DATA__ objelerine bakar (Kesin sonuç).
    2. Bulamazsa en popüler DOM seçicilerini (Sepetteki fiyat dahil) tarar.
    3. Son çare olarak sayfadaki en büyük puntolu TL ibaresini arar.
    """
    try:
        # JSON State üzerinden kesin veri çekme (Reverse Engineering)
        price_from_state = page.evaluate(r"""() => {
            // Yardımcı: nested/flat fiyat objesinden sayısal değer çıkar
            function val(x) {
                if (x === null || x === undefined) return 0;
                if (typeof x === 'number' && x > 0) return x;
                if (typeof x === 'object') {
                    return (x.value || x.amount || x.discounted || x.selling || 0);
                }
                if (typeof x === 'string') { var f = parseFloat(x.replace(/[^\d.,]/g, '').replace(',','.')); return f > 0 ? f : 0; }
                return 0;
            }
            // Yardımcı: Trendyol price objesinden GERÇEK satış fiyatını çıkar
            // ÖNCELİK: sellingPrice (müşterinin ödediği) > discountedPrice > basketPrice > originalPrice (liste fiyatı — SON çare)
            function bestPrice(p) {
                if (!p) return 0;
                var sp = val(p.sellingPrice);
                var dp = val(p.discountedPrice);
                var bp = val(p.basketPrice);
                var op = val(p.originalPrice);
                // En düşük pozitif fiyat = müşterinin gerçekte ödediği
                var candidates = [sp, dp, bp].filter(function(v) { return v > 0; });
                if (candidates.length > 0) return Math.min.apply(null, candidates);
                // Son çare: originalPrice (indirim yoksa)
                return op > 0 ? op : 0;
            }

            try {
                var d = window.location.hostname;

                // ══════════ TRENDYOL ══════════
                if (d.includes('trendyol.com')) {
                    var s = window.__INITIAL_STATE__ || window.__STATE__;
                    if (s) {
                        // Fiyat bulma — birden fazla olası JSON path
                        var paths = [
                            s.product && s.product.detail && s.product.detail.price,
                            s.product && s.product.product && s.product.product.price,
                            s.product && s.product.selectedVariant && s.product.selectedVariant.price,
                            s.productDetail && s.productDetail.product && s.productDetail.product.price,
                            s.product && s.product.price,
                        ];
                        for (var i = 0; i < paths.length; i++) {
                            var bp = bestPrice(paths[i]);
                            if (bp > 0) return bp.toString();
                        }
                        // Fallback: merchantListings'deki en düşük fiyat
                        try {
                            var detail = (s.product && (s.product.detail || s.product.product || s.product)) || {};
                            var merchants = detail.merchantListings || detail.contents || [];
                            var minMerchant = 0;
                            for (var j = 0; j < merchants.length; j++) {
                                var mp = bestPrice(merchants[j].price || merchants[j]);
                                if (mp > 0 && (minMerchant === 0 || mp < minMerchant)) minMerchant = mp;
                            }
                            if (minMerchant > 0) return minMerchant.toString();
                        } catch(e) {}
                    }
                }

                // ══════════ HEPSİBURADA ══════════
                if (d.includes('hepsiburada.com')) {
                    var n = window.__NEXT_DATA__;
                    if (n) {
                        // Birden fazla olası path
                        var pp = (n.props && n.props.pageProps) || {};
                        var prod = pp.product || pp.productDetail || pp.initialProduct || {};
                        // Ana listing fiyatı
                        var listing = prod.currentListing || prod.listing || {};
                        var priceObj = listing.price || prod.price || {};
                        var hbp = val(priceObj.value) || val(priceObj.amount) || val(priceObj.sellingPrice) || val(priceObj.discountedPrice) || val(priceObj);
                        if (hbp > 0) return hbp.toString();
                        // Variantlar / listings array
                        var listings = prod.listings || prod.variantList || [];
                        for (var k = 0; k < listings.length; k++) {
                            var lp = listings[k].price || {};
                            var lpv = val(lp.value) || val(lp.amount) || val(lp);
                            if (lpv > 0) return lpv.toString();
                        }
                        // Deep recursive price search
                        function deepPrice(obj, depth) {
                            if (!obj || depth > 6) return 0;
                            if (typeof obj !== 'object') return 0;
                            if (obj.price) {
                                var v = val(obj.price);
                                if (v > 0) return v;
                                if (typeof obj.price === 'object') {
                                    var vv = val(obj.price.value) || val(obj.price.amount) || val(obj.price.sellingPrice);
                                    if (vv > 0) return vv;
                                }
                            }
                            if (Array.isArray(obj)) {
                                for (var ii = 0; ii < Math.min(obj.length, 10); ii++) {
                                    var r = deepPrice(obj[ii], depth + 1);
                                    if (r > 0) return r;
                                }
                            } else {
                                for (var kk in obj) {
                                    if (kk === 'reviews' || kk === 'comments' || kk === 'breadcrumb') continue;
                                    var r = deepPrice(obj[kk], depth + 1);
                                    if (r > 0) return r;
                                }
                            }
                            return 0;
                        }
                        var dp = deepPrice(pp, 0);
                        if (dp > 0) return dp.toString();
                    }
                    // JSON-LD fallback
                    try {
                        var lds = document.querySelectorAll('script[type="application/ld+json"]');
                        for (var li = 0; li < lds.length; li++) {
                            var j = JSON.parse(lds[li].innerText);
                            var items = j['@graph'] || (Array.isArray(j) ? j : [j]);
                            for (var it = 0; it < items.length; it++) {
                                if (items[it]['@type'] === 'Product') {
                                    var offers = items[it].offers;
                                    if (Array.isArray(offers)) offers = offers[0];
                                    if (offers) {
                                        var op = val(offers.price) || val(offers.lowPrice);
                                        if (op > 0) return op.toString();
                                    }
                                }
                            }
                        }
                    } catch(e) {}
                }
            } catch(e) {}
            return null;
        }""")
        if price_from_state: return price_from_state

        # DOM üzerinden seçici bazlı çekme
        return page.evaluate("""() => {
            var d = window.location.hostname;
            if (d.includes('trendyol.com')) {
                // ÖNCELİK 1: Standart satış fiyatı (.prc-dsc) — Sepette/Plus fiyatını ATLA
                var prc = document.querySelector('.prc-dsc, .product-price-container .prc-dsc');
                if (prc && prc.offsetParent !== null) {
                    var t = prc.innerText.trim().split('\\n')[0].trim();
                    if(t && /\\d/.test(t)) return t;
                }
                // ÖNCELİK 2: Üstü çizili olmayan fiyat span'ı
                var altPrice = document.querySelector('.prc-org, [data-testid="price-current-price"]');
                if (altPrice && altPrice.offsetParent !== null) {
                    var st = window.getComputedStyle(altPrice);
                    if (st.textDecorationLine !== 'line-through') {
                        var t = altPrice.innerText.trim().split('\\n')[0].trim();
                        if(t && /\\d/.test(t)) return t;
                    }
                }
                // SON ÇARE: Sepette fiyatı (hiçbir standart fiyat bulunamazsa)
                var bp = document.querySelector('.product-price-container .basket-discount, [data-testid="basket-price"]');
                if (bp && bp.offsetParent !== null) {
                    var t = bp.innerText.replace(/Sepette/i, '').trim().split('\\n')[0].trim();
                    if(t && /\\d/.test(t)) return t;
                }
            }
            if (d.includes('hepsiburada.com')) {
                var bp = document.querySelector('#product-price .basket-price, [data-test-id="price-basket-price"]');
                if (bp && bp.offsetParent !== null) {
                    var t = bp.innerText.replace(/Sepette/i, '').trim().split('\\n')[0].trim();
                    if(t && /\\d/.test(t)) return t;
                }
                var mp = document.querySelector('[data-test-id="price-current-price"], #offering-price');
                if (mp && mp.offsetParent !== null) { 
                    var t = mp.innerText.trim().split('\\n')[0].trim(); 
                    if(t && /\\d/.test(t)) return t; 
                }
            }
            
            // Son çare: Heuristic Heuristic (En büyük fontlu fiyat)
            var mx = 0; var res = 'Bulunamadı';
            document.querySelectorAll('*').forEach(el => {
                if (el.offsetParent !== null && el.children.length === 0) {
                    var txt = (el.innerText || '').trim();
                    if (txt && txt.length < 25 && /\\d/.test(txt) && (txt.includes('TL') || txt.includes('₺'))) {
                        var st = window.getComputedStyle(el);
                        if (st.textDecorationLine !== 'line-through') {
                            var sz = parseFloat(st.fontSize);
                            if (sz > mx) { mx = sz; res = txt; }
                        }
                    }
                }
            });
            return res;
        }""")
    except Exception:
        return "Bulunamadı"


_STOCK_REGEX_PATTERNS_PY = [
    r'stoklar[ıi]m[ıi]zda\s*(\d+)\s*adet\s*mevcut',
    r'stoklar[ıi]m[ıi]zda\s*(\d+)\s*adet',
    r'en\s*fazla\s*(\d+)\s*adet',
    r'sadece\s*(\d+)\s*adet',
    r'son\s*(\d+)\s*adet',
    r'(\d+)\s*adet(?:ten)?\s*(?:daha\s*)?(?:fazla|sipari[şs])',
    r'(\d+)\s*adet\s*(?:mevcut|kald[ıi]|var)',
]


def _parse_stock_from_text(text):
    """
    [DEVRE DIŞI] Stok takibi konsepti uyku modunda.
    Resmi API entegrasyonu sağlanana kadar bu yardımcı çağrıldığında
    herhangi bir tarama yapmaz ve None döner.
    """
    return None


def _parse_stock_from_json(obj, depth=0):
    """
    [DEVRE DIŞI] Stok takibi konsepti uyku modunda.
    JSON içinde stok arama davranışı kapatıldı; çağrıldığında her zaman None döner.
    """
    return None
    # -- Aşağıdaki orijinal mantık, resmi API ortaklığı kurulana kadar erişilemez. --
    if depth > 8 or obj is None:
        return None
    if isinstance(obj, dict):
        # Bilinen alan isimleri — Trendyol + Hepsiburada product/inventory API field'ları
        keys_priority = [
            # Gerçek stok alanları ÖNCE (bunlar sipariş limiti değil, gerçek envanter)
            'sellableStock', 'freeStock', 'availableStock', 'availableQuantity',
            'stockQuantity', 'remainingStock', 'listingStock',
            'stock', 'inventory', 'quantity', 'itemStock',
            # maxPurchasableQuantity / maxQuantity EN SONDA:
            # Trendyol'da bu alan genellikle sipariş limiti (=5) yansıtır,
            # gerçek envanter değil. Sadece başka alan bulunamazsa kullan.
            'maxQuantity', 'maxPurchasableQuantity',
        ]
        for k in keys_priority:
            if k in obj:
                v = obj[k]
                if isinstance(v, (int, float)) and 0 < v <= 500:
                    return int(v)
        # Ayrıca mesaj alanlarında regex
        for k in ['message', 'errorMessage', 'warning', 'description', 'text']:
            if k in obj and isinstance(obj[k], str):
                r = _parse_stock_from_text(obj[k])
                if r:
                    return r
        for v in obj.values():
            r = _parse_stock_from_json(v, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _parse_stock_from_json(item, depth + 1)
            if r:
                return r
    return None


# ══════════════════════════════════════════════════════════════════
# GLOBAL NETWORK STOCK INTERCEPTOR
# Bu mekanizma sayfa açılır açılmaz tüm product/inventory/basket API
# response'larını dinler ve yakaladığı stok değerlerini saklar.
# check_tracked_products pipeline'ı page.goto() ÖNCESİ attach eder,
# extract_stock() de bu havuzdan okur.
# ══════════════════════════════════════════════════════════════════

def attach_stock_network_interceptor(page):
    """
    [DEVRE DIŞI] Stok takibi konsepti uyku modunda.
    Network interceptor hiçbir response'u dinlemez; sadece boş bir havuz ataması
    yapar ki extract_stock / get_intercepted_stock çağrıları patlamasın.
    """
    try:
        page._bmk_stock_hits = []
    except Exception:
        pass
    return
    # -- Orijinal interceptor mantığı aşağıda uyku modunda kapsüllendi. --
    try:
        page._bmk_stock_hits = []
    except Exception:
        pass

    def _on_response(response):
        try:
            url = (response.url or '').lower()
            # Trendyol + Hepsiburada ilgili endpoint'ler
            interesting = [
                # Trendyol
                'product/detail', 'product-detail', '/api/product', 'inventory',
                '/productvariant', 'variant', 'stock', 'basket', 'cart', 'sepet',
                # Hepsiburada
                'product-detail', 'listing', 'productservice', 'merchant-listing',
                'cart-api', 'checkout'
            ]
            if not any(k in url for k in interesting):
                return
            if response.status >= 500:
                return
            # Büyük dosyaları atla
            try:
                clen = int(response.headers.get('content-length', '0'))
                if clen > 2_000_000:
                    return
            except Exception:
                pass
            ctype = ''
            try:
                ctype = (response.headers.get('content-type') or '').lower()
            except Exception:
                pass

            body_text = None
            body_json = None
            try:
                if 'json' in ctype:
                    body_json = response.json()
                else:
                    body_text = response.text()
            except Exception:
                try:
                    body_text = response.text()
                except Exception:
                    return

            hit = None
            if body_json is not None:
                hit = _parse_stock_from_json(body_json)
                if hit is None:
                    try:
                        import json as _json
                        body_text = _json.dumps(body_json, ensure_ascii=False)
                    except Exception:
                        pass
            if hit is None and body_text:
                # Önce text'te Türkçe toast regex
                hit = _parse_stock_from_text(body_text)
                # Sonra JSON field regex (text olarak gelmişse)
                if hit is None:
                    import re as _re2
                    for fname in ['sellableStock', 'freeStock', 'availableStock',
                                  'availableQuantity', 'maxPurchasableQuantity',
                                  'maxQuantity', 'stockQuantity', 'remainingStock',
                                  'listingStock']:
                        m = _re2.search(r'"' + fname + r'"\s*:\s*(\d+)', body_text)
                        if m:
                            try:
                                n = int(m.group(1))
                                if 0 < n <= 500:
                                    hit = n
                                    break
                            except Exception:
                                pass

            if hit and hit > 0:
                try:
                    page._bmk_stock_hits.append(int(hit))
                    log.info(f"[Worker] 🌐 Network stock intercept: {hit} ({url[:80]})")
                except Exception:
                    pass
        except Exception:
            pass

    try:
        page.on('response', _on_response)
        # Referansı sakla (sonra detach için — test ortamı hariç zaten nadir)
        page._bmk_stock_listener = _on_response
    except Exception:
        pass


def get_intercepted_stock(page):
    """
    [DEVRE DIŞI] Stok takibi konsepti uyku modunda.
    Interceptor havuzu asla doldurulmadığı için her zaman None döner.
    """
    return None


def extract_stock_active_trendyol(page):
    """
    [DEVRE DIŞI] Trendyol aktif stok kazıma fonksiyonu uyku modunda.
    Sepet simülasyonu, overlay bypass ve toast yakalama davranışı,
    resmi API ortaklığı kurulana kadar tamamen kapatılmıştır.
    Her çağrıda -1 döner (stok bilinmiyor).
    """
    return -1
    # -- Orijinal aktif kazıma mantığı aşağıda uyku modunda kapsüllendi. --
    """
    🎯 AKTİF KAZIMA v2 — SEPET AKIŞI SİMÜLASYONU + NETWORK INTERCEPTION

    Tudors gibi giyim ürünlerinde ürün sayfasındaki adet kutusu bir DROPDOWN
    (max '5+') — fill('99') üzerinde çalışmaz. Gerçek stok ancak:
      1. Ürün sepete eklendikten sonra
      2. Yan sepet panelindeki serbest-yazı input'a 99 yazıldığında
      3. Trendyol /cart/items veya /basket API'sine istek atar
      4. Yanıt: "stoklarımızda 7 adet mevcuttur" toast'ı + JSON payload

    Strateji:
    A) Beden seçimi (eğer varsa) → popüler ilk bedeni seç
    B) 'Sepete Ekle' butonuna tıkla
    C) Network response'ları dinle — addToBasket/updateBasket API'sinden maxQuantity
    D) Yan sepet panelindeki quantity input'u bul, 99 yaz
    E) Toast mesajını yakala, regex ile stok sayısını çıkar
    F) Temizlik: sepetten sil / sepeti boşalt

    Başarılı olursa gerçek stok (int) döner, olmazsa None → fallback.
    """
    # Global network interceptor attach_stock_network_interceptor() ile
    # page.goto() öncesinde bağlanmış olmalı. Burada sayfa yüklenirken
    # yakalanan stok ipuçlarını ilk önce kontrol ederiz.
    early_hit = get_intercepted_stock(page)
    if early_hit is not None and early_hit > 0:
        log.info(f"[Worker] ⚡ Trendyol: page-load network hit → {early_hit} adet")
        return 11 if early_hit > 10 else early_hit

    try:
        # ───────── A) Sayfa hazırlığı ─────────
        # NOT: Kullanıcı URL'lerine zaten varyant parametresi eklenmiş (&v=s gibi).
        # Beden/varyant tıklaması YAPILMIYOR — aksi hâlde seçim iptal olur.
        try:
            page.evaluate("window.scrollTo(0, 300);")
            page.wait_for_timeout(400)
        except Exception:
            pass

        # ───────── C) Sepete Ekle butonuna tıkla — NATIVE PLAYWRIGHT force=True ─────────
        # B2 kaldırıldı — Trendyol'un miktar UI'ı ÜRÜN SAYFASINDA değil,
        # SEPET PANELİNDE. Akış: Sepete Ekle → panel açılır → dropdown [1▼] →
        # "5+" seç → input'a dönüşür (içinde "5") → sil → "10" yaz → Enter → toast
        #
        # ⚠ TASARIM KARARI: JS Injection Trendyol React Virtual DOM'unu tetikleyemedi.
        # Playwright'ın native keyboard API'si browser-seviyesinde gerçek OS event'leri
        # gönderir — React bunları tanır. force=True overlay engellerini bypass eder.
        add_clicked = False
        add_selectors = [
            'button.add-to-basket-button',
            '.pb-add-to-basket button',
            '.add-to-basket button',
            'button[class*="AddToBasket"]',
            'button[class*="add-to-basket"]',
            '[data-test-id="addToCart"]',
            '[data-testid="add-to-cart"]',
            'button[class*="addToCart"]',
        ]
        for sel in add_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    loc.scroll_into_view_if_needed(timeout=1500)
                    loc.click(force=True, timeout=3000)
                    add_clicked = True
                    log.info(f"[Worker] 🛒 Sepete Ekle tıklandı: {sel}")
                    break
            except Exception:
                continue

        # Text-based fallback — innerText ile buton bul
        if not add_clicked:
            try:
                for text_pattern in ['Sepete Ekle', 'sepete ekle', 'Sepete ekle']:
                    loc = page.locator(f'button:has-text("{text_pattern}")').first
                    if loc.count() > 0:
                        loc.click(force=True, timeout=3000)
                        add_clicked = True
                        log.info(f"[Worker] 🛒 Sepete Ekle (text) tıklandı")
                        break
            except Exception:
                pass

        if add_clicked:
            # ── Sepet paneli animasyon senkronizasyonu ──
            # Trendyol'un sepet paneli slide-in animasyonu vardır. Bot çok hızlı ilerlerse
            # panel henüz render olmadan input'a yazılır — React event'i yutar, toast çıkmaz.
            # Strateji: önce panelin DOM'da belirmesini bekle, sonra animasyon için hard wait.

            # 1) Sepet panelinin (veya içindeki input'un) DOM'a girmesini bekle
            cart_panel_selectors = [
                '.basket-detail-container',
                '.mini-basket',
                '.side-basket',
                '[class*="basket-detail" i]',
                '[class*="miniBasket"]',
                '[class*="sideBasket"]',
                '[class*="CartPanel"]',
                '[class*="cart-panel" i]',
            ]
            panel_appeared = False
            for cps in cart_panel_selectors:
                try:
                    page.wait_for_selector(cps, state='visible', timeout=3000)
                    panel_appeared = True
                    log.info(f"[Worker] 🎯 Sepet paneli tespit edildi: {cps}")
                    break
                except Exception:
                    continue

            # 2) Animasyonun BİTMESİ için hard wait — panel visible olsa da slide-in devam ediyor
            if panel_appeared:
                page.wait_for_timeout(1200)   # Slide-in animasyon tamamlanma payı
            else:
                page.wait_for_timeout(2500)   # Panel tespit edilemedi, genel bekleme

        # ───────── D) SEPET PANELİ: Dropdown→"5+"→Input→"10"→Enter ZİNCİRİ ─────────
        #
        # Trendyol sepet panelindeki miktar alanı 2 formda gelir:
        #   FORM 1: <select> dropdown → seçenekler: 1, 2, 3, 4, 5+
        #           "5+" seçilince → dropdown KAYBOLUR → yerine serbest <input> gelir
        #           (içinde varsayılan "5" yazılı)
        #   FORM 2: Direkt <input type="number"> (nadir)
        #
        # React Bypass Stratejisi:
        #   D1) Dropdown bul → Playwright native select "5+" → React re-render bekle
        #   D2) Input bul (dropdown'dan dönüşmüş veya direkt)
        #   D3) force=True click → Ctrl+A → Backspace → type('10') → Enter
        #        (Native keyboard = React onChange tetiklenir)

        cart_qty = None  # Nihai Playwright locator

        # ── D1) Dropdown (select) → "5+" seçimi (page.evaluate ile DOM analiz, Playwright ile seç) ──
        try:
            dropdown_info = page.evaluate("""() => {
                var selects = document.querySelectorAll('select');
                for (var i = 0; i < selects.length; i++) {
                    var s = selects[i];
                    if (!s.offsetParent) continue;
                    var opts = s.options;
                    if (opts.length < 2 || opts.length > 20) continue;
                    var fivePlusVal = null;
                    var maxVal = null; var maxN = 0;
                    for (var j = 0; j < opts.length; j++) {
                        var txt = (opts[j].text || opts[j].value || '').trim();
                        if (/\\d+\\+/.test(txt)) { fivePlusVal = opts[j].value; }
                        var n = parseInt(txt.replace(/[^0-9]/g, ''));
                        if (!isNaN(n) && n > maxN) { maxN = n; maxVal = opts[j].value; }
                    }
                    if (fivePlusVal || maxVal) {
                        // CSS sınıfından benzersiz selector üret
                        s.setAttribute('data-bmk-dropdown', '1');
                        return {found: true, value: fivePlusVal || maxVal};
                    }
                }
                return {found: false};
            }""")
            if dropdown_info and dropdown_info.get('found'):
                sel_loc = page.locator('select[data-bmk-dropdown="1"]')
                if sel_loc.count() > 0:
                    sel_loc.select_option(value=dropdown_info['value'], timeout=2000)
                    log.info(f"[Worker] 🔽 Dropdown'dan '{dropdown_info['value']}' seçildi")
                    page.wait_for_timeout(1500)  # React re-render bekle
        except Exception as e:
            log.info(f"[Worker] Dropdown tespit/seçim hata: {e}")

        # ── D2) Input'u bul — Playwright locator zinciri ──
        cart_qty_selectors = [
            '[class*="basket" i] input[type="number"]',
            '[class*="Basket"] input[type="number"]',
            '[class*="cart-item" i] input[type="number"]',
            '[class*="CartItem"] input[type="number"]',
            '.mini-basket input[type="number"]',
            '.side-basket input[type="number"]',
            'input.quantity-input',
            'input.basket-quantity',
            # Dropdown→input dönüşümü sonrası: text input da olabilir
            '[class*="basket" i] input[type="text"]',
            '[class*="Basket"] input[type="text"]',
            '[class*="cart-item" i] input[type="text"]',
            '[class*="CartItem"] input[type="text"]',
            # Genel numeric input (sepet paneli sağ tarafta)
            'input[inputmode="numeric"]',
        ]
        for sel in cart_qty_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=300):
                    cart_qty = loc
                    log.info(f"[Worker] 📦 Sepet input bulundu: {sel}")
                    break
            except Exception:
                continue

        # Geniş fallback: JS ile işaretle, Playwright locator ile yakala
        if not cart_qty:
            try:
                found = page.evaluate("""() => {
                    var inputs = document.querySelectorAll('input[type="number"], input[type="text"], input[inputmode="numeric"]');
                    for (var i = 0; i < inputs.length; i++) {
                        var inp = inputs[i];
                        if (!inp.offsetParent) continue;
                        var rect = inp.getBoundingClientRect();
                        if (rect.width < 15 || rect.height < 15) continue;
                        var inBasket = inp.closest('[class*="basket" i], [class*="Basket"], [class*="cart" i], [class*="Cart"], [class*="side-" i]');
                        if (!inBasket && rect.left < window.innerWidth * 0.5) continue;
                        var val = (inp.value || '').trim();
                        if (/^\\d*$/.test(val)) {
                            inp.setAttribute('data-bmk-cart-qty', '1');
                            return true;
                        }
                    }
                    return false;
                }""")
                if found:
                    loc = page.locator('[data-bmk-cart-qty="1"]').first
                    if loc.count() > 0:
                        cart_qty = loc
                        log.info("[Worker] 📦 Geniş fallback ile input bulundu")
            except Exception:
                pass

        # ── D3) NATIVE PLAYWRIGHT — Input stabil → click → temizle → "10" → Enter ──
        if cart_qty:
            log.info("[Worker] ⌨️ Native Playwright: stabil bekleme → force click → temizle → '10' → Enter")
            try:
                # 0) Input animasyon bitişini garantile — ekstra 500ms bekleme
                #    (Dropdown→input dönüşümü de aynı slide-in gecikmesine tabi)
                page.wait_for_timeout(500)

                # 1) force=True ile tıkla — overlay/banner engelini bypass
                cart_qty.click(force=True, timeout=2000)
                page.wait_for_timeout(300)   # React focus event'in işlenmesi için

                # 2) Mevcut değeri TEMİZLE — çift yöntem (Ctrl+A → Backspace hem Linux hem macOS)
                page.keyboard.press('Control+a')
                page.wait_for_timeout(80)
                page.keyboard.press('Backspace')
                page.wait_for_timeout(80)
                # macOS ek güvencesi
                page.keyboard.press('Meta+a')
                page.wait_for_timeout(80)
                page.keyboard.press('Backspace')
                page.wait_for_timeout(80)

                # 3) "10" yaz — her tuş arasında 60ms (insan hızı, React onChange tetikler)
                page.keyboard.type('10', delay=60)
                page.wait_for_timeout(250)

                # 4) Enter → React form submit / sepet miktar güncellemesi
                page.keyboard.press('Enter')
                log.info("[Worker] ✅ '10' yazıldı + Enter gönderildi")

            except Exception as e:
                log.info(f"[Worker] ⚠ Native keyboard hata: {e} — Tab fallback deneniyor")
                try:
                    page.keyboard.press('Tab')
                except Exception:
                    pass

            # Toast'ın DOM'a render olması için 4 saniye bekle (Trendyol AJAX gecikmesi)
            # Eski 2500ms yetersizdi — React state güncellemesi + toast animasyonu için 4000ms
            page.wait_for_timeout(4000)

        else:
            log.info("[Worker] ⚠ Sepet panelinde input bulunamadı — toast/network'e geçiliyor")

        # ───────── G) DOM toast'ından stok — ÖNCE BU (F'den önce!) ─────────
        # KRİTİK SIRALAMA: Toast ÖNCE okunmalı. Sepet API yanıtı (Section F/network)
        # maxPurchasableQuantity=5 (sipariş limiti) içerir ve min(hits) yapısı nedeniyle
        # 5 döndürür — gerçek stok olan 7'yi YUTar. Toast her zaman kesin rakamdır.
        real_stock = page.evaluate("""() => {
            var toastSelectors = [
                '.toast-message', '.ty-toast', '[class*="toast"]',
                '[class*="Toast"]', '[class*="notification"]',
                '[class*="Notification"]', '[role="alert"]',
                '[role="status"]', '.error-message',
                '.stock-warning', '.low-stock-warning',
                '[class*="stockWarning"]', '[class*="stock-warning"]',
                '.basket-error', '[class*="BasketError"]',
                '[class*="alert"]', '[class*="Alert"]',
                '[class*="warning"]', '[class*="Warning"]'
            ];
            var collected = [];
            for (var i = 0; i < toastSelectors.length; i++) {
                var els = document.querySelectorAll(toastSelectors[i]);
                for (var j = 0; j < els.length; j++) {
                    var el = els[j];
                    // offsetParent kontrolü: gizli elementleri atla AMA toast'lar bazen
                    // sayfanın üstünde fixed position'da olur — hem visible hem fixed kontrol et
                    var style = window.getComputedStyle(el);
                    var isVisible = el.offsetParent !== null ||
                                    style.position === 'fixed' ||
                                    style.position === 'absolute';
                    if (!isVisible) continue;
                    var t = (el.innerText || el.textContent || '').trim();
                    if (t.length > 5) collected.push(t);
                }
            }
            // Son güvence: tüm body metni
            collected.push(document.body.innerText || '');

            var patterns = [
                /stoklar[ıi]m[ıi]zda\\s*(\\d+)\\s*adet\\s*mevcut/i,
                /stoklar[ıi]m[ıi]zda\\s*(\\d+)\\s*adet/i,
                /en\\s*fazla\\s*(\\d+)\\s*adet\\s*(?:sipari[şs]|ekle)/i,
                /en\\s*fazla\\s*(\\d+)\\s*adet/i,
                /sadece\\s*(\\d+)\\s*adet/i,
                /son\\s*(\\d+)\\s*(?:adet|[uü]r[uü]n)/i,
                /(\\d+)\\s*adet(?:ten)?\\s*(?:daha\\s*)?(?:fazla|sipari[şs])/i,
                /(\\d+)\\s*adet\\s*(?:mevcuttur|mevcut|kald[ıi]|var)/i
            ];

            for (var k = 0; k < collected.length; k++) {
                var text = collected[k];
                for (var p = 0; p < patterns.length; p++) {
                    var m = text.match(patterns[p]);
                    if (m) {
                        var n = parseInt(m[1]);
                        // Geçerli aralık: 1-500. 5'e eşit bile olsa döndür (toast = kesin)
                        if (n > 0 && n <= 500) return n;
                    }
                }
            }
            return null;
        }""")

        if real_stock and isinstance(real_stock, (int, float)) and real_stock > 0:
            rs = int(real_stock)
            log.info(f"[Worker] ✅ Sepet toast'ından stok: {rs}")
            _cleanup_cart(page)
            return 11 if rs > 10 else rs

        # ───────── F) Network'ten yakalanan stok — SONRA BU ─────────
        # Toast bulunamadıysa network interceptor'a bak.
        # UYARI: Sepet API yanıtı maxPurchasableQuantity=5 içerebilir (sipariş limiti ≠ gerçek stok).
        # Bu yüzden net_hit == 5 ise güvenme; toast yoksa None dön, ana fallback devreye girsin.
        net_hit = get_intercepted_stock(page)
        _cleanup_cart(page)
        if net_hit is not None and net_hit > 0 and net_hit != 5:
            log.info(f"[Worker] 🌐 Network'ten stok yakalandı: {net_hit}")
            return 11 if net_hit > 10 else net_hit

        return None
    except Exception as e:
        log.info(f"[Worker] Active stock scraping failed: {e}")
        try:
            _cleanup_cart(page)
        except Exception:
            pass
        return None


def _cleanup_cart(page):
    """Scraping sonrası sepet panelini temizler (sepetten sil veya kapat)."""
    try:
        page.evaluate("""() => {
            // Sepetten sil butonları
            var removeSelectors = [
                '.basket-item-remove', '.remove-item', '[class*="remove"]',
                'button[aria-label*="sil" i]', 'button[aria-label*="remove" i]',
                '[class*="delete-item"]', '[class*="deleteItem"]'
            ];
            for (var i = 0; i < removeSelectors.length; i++) {
                var btns = document.querySelectorAll(removeSelectors[i]);
                for (var j = 0; j < btns.length; j++) {
                    var b = btns[j];
                    if (!b.offsetParent) continue;
                    try { b.click(); } catch(e) {}
                }
            }
            // Sepet panelini kapat
            var closeSelectors = [
                '.basket-close', '.modal-close', '[class*="close-basket"]',
                '[aria-label*="kapat" i]', '[aria-label*="close" i]'
            ];
            for (var k = 0; k < closeSelectors.length; k++) {
                var cs = document.querySelectorAll(closeSelectors[k]);
                for (var l = 0; l < cs.length; l++) {
                    if (cs[l].offsetParent) { try { cs[l].click(); } catch(e) {} }
                }
            }
        }""")
        page.wait_for_timeout(400)
    except Exception:
        pass


def extract_stock_active_hepsiburada(page):
    """
    [DEVRE DIŞI] Hepsiburada aktif stok kazıma fonksiyonu uyku modunda.
    Sepet simülasyonu, overlay bypass ve toast yakalama davranışı,
    resmi API ortaklığı kurulana kadar tamamen kapatılmıştır.
    Her çağrıda -1 döner (stok bilinmiyor).
    """
    return -1
    # -- Orijinal aktif kazıma mantığı aşağıda uyku modunda kapsüllendi. --
    # A) İlk önce page-level interceptor'ı kontrol et
    early_hit = get_intercepted_stock(page)
    if early_hit is not None and early_hit > 0:
        log.info(f"[Worker] ⚡ HB: page-load network hit → {early_hit} adet")
        return 11 if early_hit > 10 else early_hit

    # Cookie banner / overlay'leri kapat
    try:
        page.evaluate("""() => {
            var sels = ['#onetrust-accept-btn-handler', '[id*="cookie"] button',
                        '.cookie-accept', '[class*="cookieAccept"]',
                        '.modal-close', '[class*="closeButton"]'];
            for (var i = 0; i < sels.length; i++) {
                var els = document.querySelectorAll(sels[i]);
                for (var j = 0; j < els.length; j++) { try { els[j].click(); } catch(e) {} }
            }
            document.querySelectorAll('.modal, .popup, [id*="onetrust"]').forEach(el => el.style.display='none');
        }""")
    except Exception:
        pass

    try:
        # B) __NEXT_DATA__ derin arama (DOM içinden)
        js_stock = page.evaluate("""() => {
            try {
                var n = window.__NEXT_DATA__;
                if (!n) {
                    var tag = document.getElementById('__NEXT_DATA__');
                    if (tag) { try { n = JSON.parse(tag.textContent); } catch(e) {} }
                }
                if (!n) return null;

                // Derin arama — HB özel alan isimleri
                var targets = ['sellableStock', 'freeStock', 'availableStock',
                               'availableQuantity', 'stockQuantity', 'remainingStock',
                               'listingStock', 'stock', 'inventory', 'quantity'];
                var found = [];
                function walk(obj, depth) {
                    if (depth > 8 || obj === null || obj === undefined) return;
                    if (typeof obj !== 'object') return;
                    if (Array.isArray(obj)) { for (var i = 0; i < obj.length; i++) walk(obj[i], depth+1); return; }
                    for (var k in obj) {
                        if (!obj.hasOwnProperty(k)) continue;
                        var v = obj[k];
                        if (targets.indexOf(k) >= 0 && typeof v === 'number' && v >= 0 && v <= 500) {
                            found.push(v);
                        } else if (typeof v === 'object') {
                            walk(v, depth+1);
                        }
                    }
                }
                walk(n, 0);
                if (found.length) {
                    var pos = found.filter(function(x){ return x > 0; });
                    if (pos.length) return Math.min.apply(null, pos);
                    return 0;
                }
                return null;
            } catch(e) { return null; }
        }""")
        if js_stock is not None and js_stock >= 0:
            log.info(f"[Worker] ✅ HB __NEXT_DATA__ derin arama: {js_stock}")
            return 11 if js_stock > 10 else int(js_stock)

        # B2) HB ürün sayfasında "Adet" input veya artı(+) butonu ara
        # Bazı HB sayfalarında sepete eklemeden önce miktar seçilebilir
        hb_qty_set = False
        try:
            hb_qty_set = page.evaluate("""() => {
                // Miktar input'u
                var qSels = [
                    '[data-test-id="quantity-input"]', 'input[name="quantity"]',
                    '[class*="quantity"] input', '[class*="Quantity"] input',
                    '.product-quantity input', 'input.qty-input'
                ];
                for (var i = 0; i < qSels.length; i++) {
                    var el = document.querySelector(qSels[i]);
                    if (el && el.offsetParent) {
                        var parent = el.closest('[class*="cart" i], [class*="basket" i]');
                        if (parent) continue;
                        try {
                            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                            setter.call(el, '10');
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return true;
                        } catch(e) {}
                    }
                }
                return false;
            }""")
        except Exception:
            pass
        if hb_qty_set:
            page.wait_for_timeout(500)

        # C) Sepete ekle butonunu tıkla — NATIVE PLAYWRIGHT force=True
        add_selectors = [
            '[data-test-id="addToCart"]',
            '[data-testid="add-to-cart"]',
            'button.add-to-cart',
            'button[class*="addToCart"]',
            'button[class*="AddToCart"]',
            '#addToCart',
            '.add-to-basket',
            'button[class*="add-to-basket"]',
            '[class*="addToCartButton"]',
            '[class*="AddToCartButton"]',
            'button[data-bind*="addToCart"]',
        ]
        add_clicked = False
        for sel in add_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    loc.scroll_into_view_if_needed(timeout=1500)
                    loc.click(force=True, timeout=3000)
                    add_clicked = True
                    log.info(f"[Worker] 🛒 HB Sepete Ekle: {sel}")
                    break
            except Exception:
                continue

        # Text fallback
        if not add_clicked:
            try:
                for txt in ['Sepete ekle', 'Sepete Ekle', 'sepete ekle', 'Sepete at']:
                    loc = page.locator(f'button:has-text("{txt}")').first
                    if loc.count() > 0:
                        loc.click(force=True, timeout=3000)
                        add_clicked = True
                        log.info(f"[Worker] 🛒 HB Sepete Ekle (text): {txt}")
                        break
            except Exception:
                pass

        if add_clicked:
            page.wait_for_timeout(2500)

        # Yakalanmış mı?
        mid_hit = get_intercepted_stock(page)
        if mid_hit is not None and mid_hit > 0:
            log.info(f"[Worker] 🌐 HB add-to-cart sonrası network hit: {mid_hit}")
            _cleanup_hb_cart(page)
            return 11 if mid_hit > 10 else mid_hit

        # D) Sepet quantity input'u bul — NATIVE PLAYWRIGHT force + keyboard
        cart_qty_selectors = [
            '.cart-item-quantity input',
            '.basket-item input[type="number"]',
            '.basket-item input[type="text"]',
            '.cart-item input[type="number"]',
            '[class*="cartItem"] input[type="number"]',
            '[class*="CartItem"] input[type="number"]',
            '[class*="basket"] input[type="number"]',
            '[class*="Basket"] input[type="number"]',
            'input[class*="quantity"]',
            'input[name="quantity"]',
            '[class*="QuantitySelector"] input',
            '[class*="quantitySelector"] input',
            '[data-test-id*="quantity"] input',
        ]
        cart_qty = None
        for sel in cart_qty_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=300):
                    cart_qty = loc
                    log.info(f"[Worker] 📦 HB sepet input: {sel}")
                    break
            except Exception:
                continue

        if cart_qty:
            try:
                cart_qty.click(force=True, timeout=2000)
                page.wait_for_timeout(150)
                page.keyboard.press('Control+a')
                page.wait_for_timeout(100)
                page.keyboard.press('Backspace')
                page.wait_for_timeout(100)
                page.keyboard.type('99', delay=50)
                page.wait_for_timeout(200)
                page.keyboard.press('Enter')
                log.info("[Worker] ⌨️ HB: '99' yazıldı + Enter")
            except Exception as e:
                log.info(f"[Worker] HB keyboard hata: {e}")
                try:
                    page.keyboard.press('Tab')
                except Exception:
                    pass
            page.wait_for_timeout(2000)
        elif add_clicked:
            # D2) Input bulunamadıysa "+" butonuna art arda bas (artırma fallback)
            # HB bazı ürünlerde input yerine +/- butonları koyar
            try:
                plus_stock = page.evaluate("""() => {
                    var plusSels = [
                        '[data-test-id="increase-quantity"]',
                        'button[class*="increase"]', 'button[class*="Increase"]',
                        'button[class*="plus"]', 'button[class*="Plus"]',
                        'button[aria-label*="artır" i]', 'button[aria-label*="increase" i]',
                        '[class*="quantity"] button:last-child',
                        '[class*="Quantity"] button:last-child',
                    ];
                    var plusBtn = null;
                    for (var i = 0; i < plusSels.length; i++) {
                        var el = document.querySelector(plusSels[i]);
                        if (el && el.offsetParent && !el.disabled) { plusBtn = el; break; }
                    }
                    if (!plusBtn) return null;

                    // 15 kez + butonuna bas (her click arası 100ms beklenecek)
                    var clickCount = 0;
                    var maxClicks = 15;
                    for (var c = 0; c < maxClicks; c++) {
                        if (plusBtn.disabled || plusBtn.classList.contains('disabled')) break;
                        try { plusBtn.click(); clickCount++; } catch(e) { break; }
                    }
                    return clickCount;
                }""")
                if plus_stock and plus_stock > 0:
                    log.info(f"[Worker] ➕ HB plus butonu {plus_stock} kez tıklandı")
                    page.wait_for_timeout(2000)
            except Exception:
                pass

        # E) Network hit kontrolü
        late_net = get_intercepted_stock(page)
        if late_net is not None and late_net > 0:
            log.info(f"[Worker] 🌐 HB network hit (post-injection): {late_net}")
            _cleanup_hb_cart(page)
            return 11 if late_net > 10 else late_net

        # F) Toast / error mesajı + DOM stock ipuçları
        toast_stock = page.evaluate("""() => {
            var sels = [
                '.toast-message', '[class*="toast"]', '[class*="Toast"]',
                '[class*="notification"]', '[role="alert"]', '[role="status"]',
                '.error-message', '[class*="error"]', '[class*="Error"]',
                '[class*="warning"]', '[class*="Warning"]',
                '.cart-error', '.basket-error', '[class*="cartError"]',
                // HB'ye özel modal/popup selectors
                '[class*="stockWarning"]', '[class*="StockWarning"]',
                '[class*="maxQuantity"]', '[class*="MaxQuantity"]',
                '.hb-toast', '[class*="hbToast"]',
                '[class*="InfoMessage"]', '[class*="infoMessage"]'
            ];
            var texts = [];
            for (var i = 0; i < sels.length; i++) {
                var els = document.querySelectorAll(sels[i]);
                for (var j = 0; j < els.length; j++) {
                    var el = els[j];
                    if (!el.offsetParent) continue;
                    var t = (el.innerText || el.textContent || '').trim();
                    if (t) texts.push(t);
                }
            }
            texts.push(document.body.innerText || '');
            var patterns = [
                /stokta\\s*sadece\\s*(\\d+)\\s*adet/i,
                /stokta\\s*(\\d+)\\s*adet/i,
                /(\\d+)\\s*adet\\s*stok/i,
                /en\\s*fazla\\s*(\\d+)\\s*adet/i,
                /maksimum\\s*(\\d+)\\s*adet/i,
                /maximum\\s*(\\d+)/i,
                /stoklar[ıi]m[ıi]zda\\s*(\\d+)\\s*adet/i,
                /sadece\\s*(\\d+)\\s*adet/i,
                /(\\d+)\\s*adet\\s*(?:mevcut|kald[ıi]|var)/i,
                /bu\\s+[üu]r[üu]nden\\s+en\\s+fazla\\s+(\\d+)/i,
                /sepetinize\\s+en\\s+fazla\\s+(\\d+)/i
            ];
            for (var k = 0; k < texts.length; k++) {
                for (var p = 0; p < patterns.length; p++) {
                    var m = texts[k].match(patterns[p]);
                    if (m) {
                        var n = parseInt(m[1]);
                        if (n > 0 && n <= 500) return n;
                    }
                }
            }
            return null;
        }""")

        _cleanup_hb_cart(page)

        if toast_stock and isinstance(toast_stock, (int, float)) and toast_stock > 0:
            rs = int(toast_stock)
            log.info(f"[Worker] ✅ HB sepet toast: {rs}")
            return 11 if rs > 10 else rs

        # Son şans: network
        final_net = get_intercepted_stock(page)
        if final_net is not None and final_net > 0:
            return 11 if final_net > 10 else final_net

        # G) DOM'dan direkt stok ipuçları — "Stok Adedi" tablosu veya benzeri
        try:
            dom_stock = page.evaluate("""() => {
                // "Stok Adedi" label'ının yanındaki değer
                var cells = document.querySelectorAll('th, td, dt, dd, span, div, li');
                for (var i = 0; i < cells.length; i++) {
                    var ct = (cells[i].innerText || '').trim().toLowerCase();
                    if (ct === 'stok adedi' || ct === 'stok' || ct.includes('stok adedi')) {
                        var next = cells[i].nextElementSibling;
                        if (next) {
                            var nt = (next.innerText || '').trim();
                            var num = parseInt(nt);
                            if (!isNaN(num) && num >= 0 && num <= 500) return num;
                        }
                        // parent row'dan
                        var row = cells[i].closest('tr, dl, div');
                        if (row) {
                            var rowText = (row.innerText || '').replace(ct, '').trim();
                            var m = rowText.match(/(\\d+)/);
                            if (m) {
                                var n = parseInt(m[1]);
                                if (n >= 0 && n <= 500) return n;
                            }
                        }
                    }
                }
                // "X adet" veya "X ürün" sayfada belirgin yerde
                var stockPill = document.querySelector('[class*="stockCount"], [class*="StockCount"], [class*="stock-count"]');
                if (stockPill) {
                    var m = (stockPill.innerText || '').match(/(\\d+)/);
                    if (m) {
                        var n = parseInt(m[1]);
                        if (n > 0 && n <= 500) return n;
                    }
                }
                return null;
            }""")
            if dom_stock is not None and isinstance(dom_stock, (int, float)) and dom_stock >= 0:
                rs = int(dom_stock)
                log.info(f"[Worker] ✅ HB DOM stok ipucu: {rs}")
                return 11 if rs > 10 else rs
        except Exception:
            pass

        # Son çare: add-to-cart butonu aktifse (clickable) → 10
        try:
            can_buy = page.evaluate("""() => {
                var sels = [
                    '[data-test-id="addToCart"]', '#addToCart', '.add-to-cart',
                    'button[class*="addToCart"]', 'button[class*="AddToCart"]'
                ];
                for (var i = 0; i < sels.length; i++) {
                    var b = document.querySelector(sels[i]);
                    if (b && !b.disabled && !b.classList.contains('disabled') && b.offsetParent) return true;
                }
                return false;
            }""")
            if can_buy:
                return 10  # satın alınabilir → en az 10
        except Exception:
            pass

        return None
    except Exception as e:
        log.info(f"[Worker] HB active scraping failed: {e}")
        try:
            _cleanup_hb_cart(page)
        except Exception:
            pass
        return None


def _cleanup_hb_cart(page):
    """HB sepetinden eklenen ürünü kaldırır."""
    try:
        page.evaluate("""() => {
            var sels = [
                '.cart-item-remove', '.basket-item-remove', '[class*="removeItem"]',
                '[class*="RemoveItem"]', '[aria-label*="sil" i]', '[aria-label*="kaldır" i]',
                '[aria-label*="remove" i]', '[class*="delete"]', '[class*="Delete"]'
            ];
            for (var i = 0; i < sels.length; i++) {
                var btns = document.querySelectorAll(sels[i]);
                for (var j = 0; j < btns.length; j++) {
                    var b = btns[j];
                    if (!b.offsetParent) continue;
                    try { b.click(); } catch(e) {}
                }
            }
        }""")
        page.wait_for_timeout(400)
    except Exception:
        pass


def extract_stock(page):
    """
    [DEVRE DIŞI] Ana stok çekme dispatcher'ı uyku modunda.
    Stok takibi konsepti, resmi API entegrasyonu sağlanana kadar tamamen kapalı.
    Her çağrıda -1 döner; çağrı noktaları bu değeri "bilinmiyor" olarak yorumlar.
    """
    return -1
    # -- Orijinal stok çekme mantığı aşağıda uyku modunda kapsüllendi. --
    # ══════════ ÖNCELİK -1: Page-load network interception ══════════
    try:
        early_net = get_intercepted_stock(page)
        if early_net is not None and early_net >= 0:
            log.info(f"[Worker] ⚡ Early network stock hit: {early_net} adet")
            return int(early_net) if early_net <= 10 else 11
    except Exception:
        pass

    # ══════════ ÖNCELİK 0: AKTİF KAZIMA — Platform bazlı ══════════
    try:
        current_url = page.url or ''
        if 'trendyol.com' in current_url:
            active_result = extract_stock_active_trendyol(page)
            if active_result is not None and active_result >= 0:
                log.info(f"[Worker] ✅ Trendyol aktif kazıma: {active_result} adet")
                return int(active_result)
        elif 'hepsiburada.com' in current_url:
            active_result = extract_stock_active_hepsiburada(page)
            if active_result is not None and active_result >= 0:
                log.info(f"[Worker] ✅ HepsiBurada aktif kazıma: {active_result} adet")
                return int(active_result)
    except Exception as e:
        log.info(f"[Worker] Active scraping skipped: {e}")

    try:
        result = page.evaluate("""() => {
            var domain = window.location.hostname;

            // ══════════ 1. TRENDYOL — AGRESİF FALLBACK ══════════
            if (domain.includes('trendyol.com')) {
                var exactStock = null;
                var tukeniyor_sinyal = false;

                // ──────────────────────────────────────────────────────────────────
                // ADIM 0 (KRİTİK): DOM "Son X ürün!" metni ÖNCE kontrol et
                // maxPurchasableQuantity = 5 yanıltıcıdır (Trendyol satın alma limiti).
                // Gerçek stok sayısı yalnızca DOM uyarı metninde kesin görünür.
                // ──────────────────────────────────────────────────────────────────
                try {
                    // 0a. Özel CSS class'lar (stok uyarı kutuları)
                    var stockWarnSels = [
                        '.low-stock-warning', '.stock-warning', '[class*="lowStock"]',
                        '[class*="stockWarning"]', '.pr-in-cn', '[class*="stock-alert"]',
                        '[class*="stockAlert"]', '[class*="critical"]', '.danger'
                    ];
                    for (var swi = 0; swi < stockWarnSels.length; swi++) {
                        var swEls = document.querySelectorAll(stockWarnSels[swi]);
                        for (var swj = 0; swj < swEls.length; swj++) {
                            var swTxt = (swEls[swj].innerText || swEls[swj].textContent || '').trim();
                            var swM = swTxt.match(/son\s*(\d+)\s*(?:adet|[uü]r[uü]n|tane|par[cç]a)/i);
                            if (swM) {
                                var swN = parseInt(swM[1]);
                                if (swN > 0 && swN <= 50) { exactStock = swN; break; }
                            }
                        }
                        if (exactStock !== null) break;
                    }

                    // 0b. Tüm sayfa metni — "Son X ürün/adet" pattern
                    if (exactStock === null) {
                        var bodyFull = (document.body.innerText || '').replace(/\s+/g, ' ');
                        var sonPatterns = [
                            /son\s*(\d+)\s*(?:adet|[uü]r[uü]n|tane|par[cç]a)/i,
                            /(?:stoklarımızda|stokta|sadece)\s*(\d+)\s*(?:adet|[uü]r[uü]n)/i
                        ];
                        for (var spi = 0; spi < sonPatterns.length; spi++) {
                            var spM = bodyFull.match(sonPatterns[spi]);
                            if (spM) {
                                var spN = parseInt(spM[1]);
                                if (spN > 0 && spN <= 50) { exactStock = spN; break; }
                            }
                        }
                    }
                    if (exactStock !== null) {
                        return exactStock > 10 ? 11 : exactStock;
                    }
                } catch(e) {}

                // 1a. __INITIAL_STATE__ — DERİN ARAMA
                // ÖNCELİK: inventory/stok gerçek sayıları önce, maxPurchasableQuantity EN SON
                // maxPurchasableQuantity = 5 → ŞÜPHELI (Trendyol satın alma limiti = 5 per order)
                // maxPurchasableQuantity < 5 → güvenilir (gerçek stok sayısı)
                try {
                    var s = window.__INITIAL_STATE__ || window.__STATE__;
                    if (s && s.product) {
                        // Birden fazla yapı yolunu tara
                        var pCandidates = [
                            s.product.detail,
                            s.product.product,
                            s.product
                        ];
                        for (var pci = 0; pci < pCandidates.length; pci++) {
                            var p = pCandidates[pci];
                            if (!p || typeof p !== 'object') continue;

                            // Ana seviye stok alanları — inventory ÖNCE, maxPurchasableQuantity SONDA
                            var topKeys = ['inventory', 'stock', 'stockQuantity', 'sellableStock',
                                           'availableQuantity', 'freeStock', 'availableStock',
                                           'remainingStock'];
                            for (var tki = 0; tki < topKeys.length; tki++) {
                                var tv = p[topKeys[tki]];
                                if (tv !== null && tv !== undefined && typeof tv === 'number' && tv >= 0) {
                                    exactStock = tv;
                                    break;
                                }
                            }
                            // maxPurchasableQuantity: < 5 ise güvenilir, >= 5 ise şüpheli (satın alma limiti)
                            if (exactStock === null) {
                                var mpqVal = p['maxPurchasableQuantity'];
                                if (mpqVal !== null && mpqVal !== undefined && typeof mpqVal === 'number') {
                                    if (mpqVal > 0 && mpqVal < 5) {
                                        exactStock = mpqVal;  // 1-4: kesin gerçek stok
                                    } else if (mpqVal >= 5) {
                                        exactStock = -99;  // sinyal: "en az 5 var, gerçek bilinmiyor" → 11
                                    }
                                }
                            }
                            if (exactStock !== null) break;

                            // selectedVariant — seçili beden/renk varyantının stoğu
                            var sv = p.selectedVariant || p.selectedOption || p.selectedSku || null;
                            if (sv && typeof sv === 'object') {
                                for (var svi = 0; svi < topKeys.length; svi++) {
                                    var svv = sv[topKeys[svi]];
                                    if (svv !== null && svv !== undefined && typeof svv === 'number' && svv >= 0) {
                                        exactStock = svv;
                                        break;
                                    }
                                }
                            }
                            if (exactStock !== null) break;

                            // price objesi içinde stock (bazı sayfalarda price.stock var)
                            var prObj = p.price || {};
                            if (typeof prObj === 'object' && prObj.stock !== undefined && typeof prObj.stock === 'number' && prObj.stock >= 0) {
                                exactStock = prObj.stock;
                                break;
                            }

                            // Varyant DERİN TARAMA — beden/renk varyantları (giyim için kritik)
                            var allVariantSources = [
                                p.allVariants, p.variants, p.variantList,
                                p.sizeVariants, p.colorVariants, p.options,
                                p.skus, p.skuList
                            ];
                            var min_inv = null;
                            for (var vsi = 0; vsi < allVariantSources.length; vsi++) {
                                var vars = allVariantSources[vsi];
                                if (!vars || !vars.length) continue;
                                for (var vi = 0; vi < vars.length; vi++) {
                                    var vv = vars[vi] || {};
                                    var vStock = null;
                                    for (var vki = 0; vki < topKeys.length; vki++) {
                                        var vkv = vv[topKeys[vki]];
                                        if (vkv !== null && vkv !== undefined && typeof vkv === 'number' && vkv >= 0) {
                                            vStock = vkv;
                                            break;
                                        }
                                    }
                                    var inStock = vv.inStock !== false && (vStock === null ? true : vStock > 0);
                                    if (inStock && vStock !== null && vStock >= 0) {
                                        if (min_inv === null || vStock < min_inv) min_inv = vStock;
                                    }
                                }
                            }
                            if (min_inv !== null) { exactStock = min_inv; break; }

                            // contents/merchantListings gibi iç içe yapılara bak
                            var merchants = p.merchantListings || p.contents || p.sellers || [];
                            var m_min = null;
                            for (var mi = 0; mi < merchants.length; mi++) {
                                var ml = merchants[mi] || {};
                                for (var mki = 0; mki < topKeys.length; mki++) {
                                    var mq = ml[topKeys[mki]];
                                    if (mq !== null && mq !== undefined && typeof mq === 'number' && mq > 0) {
                                        if (m_min === null || mq < m_min) m_min = mq;
                                        break;
                                    }
                                }
                            }
                            if (m_min !== null) { exactStock = m_min; break; }
                        }

                        if (exactStock !== null) {
                            // exactStock = -99: maxPurchasableQuantity >= 5 → "en az 5 var" sinyali
                            // Gerçek sayı bilinmiyor → 11 döndür (5 DEĞİL!)
                            if (exactStock === -99) return 11;
                            if (exactStock > 10) return 11;
                            return exactStock;
                        }
                    }
                } catch(e) {}

                // 1b. DOM: ESNEK REGEX + ÖZEL CLASS SELECTORS
                try {
                    // ÖNCE: .low-stock-warning, .stock-warning, .danger class'ları
                    var warnSelectors = [
                        '.low-stock-warning', '.stock-warning', '.stock-info-warning',
                        '[class*="lowStock"]', '[class*="low-stock"]', '[class*="stock-warning"]',
                        '[class*="stockWarning"]', '[class*="stock-info"]',
                        '.danger', '[class*="critical-stock"]', '[class*="limited"]'
                    ];
                    for (var si = 0; si < warnSelectors.length; si++) {
                        var els = document.querySelectorAll(warnSelectors[si]);
                        for (var ei = 0; ei < els.length; ei++) {
                            var el = els[ei];
                            if (!el.offsetParent) continue;
                            var txt = (el.innerText || el.textContent || '').trim();
                            if (!txt) continue;
                            tukeniyor_sinyal = true;
                            var m = txt.match(/(\\d+)\\s*(?:adet|ürün|tane)/i);
                            if (m) {
                                var f = parseInt(m[1]);
                                if (f > 0 && f <= 50) {
                                    if (f > 10) return 11;
                                    return f;
                                }
                            }
                        }
                    }

                    // ESNEK REGEX — tüm body text üzerinde
                    var body = (document.body.innerText || '').replace(/\\s+/g, ' ');
                    var flexPatterns = [
                        /(?:stoklarımızda|stoklarimizda|stokta|sadece)\\s*(\\d+)\\s*(?:adet|ürün|tane|parça)/i,
                        /son\\s*(\\d+)\\s*(?:adet|ürün|tane|parça)/i,
                        /(\\d+)\\s*(?:adet|ürün|tane)\\s*(?:kaldı|mevcut|var)/i,
                        /(\\d+)\\s*(?:adet|ürün)\\s*(?:ile\\s*)?sınırlı/i
                    ];
                    for (var i = 0; i < flexPatterns.length; i++) {
                        var mm = body.match(flexPatterns[i]);
                        if (mm) {
                            var ff = parseInt(mm[1]);
                            if (ff > 0 && ff <= 50) {
                                if (ff > 10) return 11;
                                return ff;
                            }
                        }
                    }

                    // Quantity input max değeri
                    var qtyInput = document.querySelector('input[type="number"][max], .quantity-selector input, [data-testid="quantity-input"]');
                    if (qtyInput) {
                        var maxVal = parseInt(qtyInput.getAttribute('max') || qtyInput.getAttribute('data-max'));
                        if (maxVal > 0 && maxVal <= 50) {
                            if (maxVal > 10) return 11;
                            return maxVal;
                        }
                    }

                    // "Tükeniyor!" sinyalleri
                    var lowerBody = body.toLowerCase();
                    if (lowerBody.includes('tükeniyor') || lowerBody.includes('tukeniyor') ||
                        lowerBody.includes('son ürünler') || lowerBody.includes('sınırlı stok') ||
                        lowerBody.includes('sinirli stok')) {
                        tukeniyor_sinyal = true;
                    }
                } catch(e) {}

                // 1c. SON ÇARE — sinyal var mı?
                // tukeniyor_sinyal → return 5 ARTIK YOK (yanıltıcı — maxPurchasableQuantity=5 ile çakışır)
                // Bunun yerine: 3 döndür (düşük stok sinyali var ama tam sayı bilinmiyor)
                if (tukeniyor_sinyal) return 3;

                // isBuyable varsa 10 döndür (takip aktif kalsın, -1 DEĞİL)
                try {
                    var s2 = window.__INITIAL_STATE__ || window.__STATE__;
                    if (s2 && s2.product) {
                        var p2 = s2.product.detail || s2.product;
                        if (p2.isBuyable !== false && p2.isSellable !== false) return 10;
                    }
                } catch(e) {}

                // Add-to-basket butonu varsa 10 döndür
                var addBtnT = document.querySelector('[class*="add-to-basket"], [data-test-id="addToCart"], button[class*="AddToBasket"]');
                if (addBtnT && !addBtnT.disabled) return 10;
            }

            // ══════════ 2. HEPSİBURADA — DERİN __NEXT_DATA__ STOK OKUMA ══════════
            if (domain.includes('hepsiburada.com')) {
                try {
                    var n = window.__NEXT_DATA__;
                    if (n) {
                        var pp = (n.props && n.props.pageProps) || {};
                        var prod = pp.product || pp.productDetail || pp.initialProduct || {};

                        // 2a. currentListing → inventory / stockQuantity / sellableStock ...
                        var listing = prod.currentListing || prod.listing || {};
                        var stockKeys = ['inventory','stockQuantity','sellableStock','freeStock',
                                         'availableQuantity','availableStock','remainingStock',
                                         'maxPurchasableQuantity','maxQuantity','stock','quantity'];
                        for (var sk = 0; sk < stockKeys.length; sk++) {
                            var sv = listing[stockKeys[sk]];
                            if (sv !== null && sv !== undefined && typeof sv === 'number' && sv >= 0) {
                                return sv > 10 ? 11 : sv;
                            }
                        }
                        if (listing.availabilityStatus === 1) return 11;

                        // 2b. variantList / listings array
                        var lists = prod.variantList || prod.listings || prod.variants || [];
                        var minInv = null;
                        for (var li = 0; li < lists.length; li++) {
                            var ll = lists[li] || {};
                            for (var sk2 = 0; sk2 < stockKeys.length; sk2++) {
                                var sv2 = ll[stockKeys[sk2]];
                                if (sv2 !== null && sv2 !== undefined && typeof sv2 === 'number' && sv2 >= 0) {
                                    if (minInv === null || sv2 < minInv) minInv = sv2;
                                    break;
                                }
                            }
                        }
                        if (minInv !== null) return minInv > 10 ? 11 : minInv;

                        // 2c. Derin recursive arama — tüm __NEXT_DATA__ ağacı
                        function deepStock(obj, depth) {
                            if (!obj || depth > 7) return null;
                            if (typeof obj !== 'object') return null;
                            for (var ski = 0; ski < stockKeys.length; ski++) {
                                var v = obj[stockKeys[ski]];
                                if (v !== null && v !== undefined && typeof v === 'number' && v >= 0 && v <= 500) return v;
                            }
                            if (Array.isArray(obj)) {
                                for (var i = 0; i < Math.min(obj.length, 10); i++) {
                                    var r = deepStock(obj[i], depth + 1);
                                    if (r !== null) return r;
                                }
                            } else {
                                for (var k in obj) {
                                    if (k === 'reviews' || k === 'comments' || k === 'breadcrumb' || k === 'seo') continue;
                                    var r = deepStock(obj[k], depth + 1);
                                    if (r !== null) return r;
                                }
                            }
                            return null;
                        }
                        var deepHit = deepStock(pp, 0);
                        if (deepHit !== null) return deepHit > 10 ? 11 : deepHit;

                        // 2d. Ürün Bilgileri tablosundaki "Stok Adedi" DOM fallback
                        try {
                            var cells = document.querySelectorAll('th, td, dt, dd, span, div');
                            for (var ci = 0; ci < cells.length; ci++) {
                                var ct = (cells[ci].innerText || '').trim().toLowerCase();
                                if (ct.includes('stok adedi') || ct === 'stok') {
                                    var next = cells[ci].nextElementSibling || cells[ci + 1];
                                    if (next) {
                                        var nt = (next.innerText || '').trim();
                                        // "20 adetten az" → 20
                                        var m = nt.match(/(\d+)/);
                                        if (m) {
                                            var sv = parseInt(m[1]);
                                            if (sv > 0 && sv <= 500) return sv > 10 ? 11 : sv;
                                        }
                                    }
                                }
                            }
                        } catch(e) {}

                        // Sepete Ekle butonu varsa en az 10
                        var addBtnHB = document.querySelector('[data-test-id="addToCart"], button[class*="addToCart"]');
                        if (addBtnHB && !addBtnHB.disabled) return 10;
                    }
                } catch(e) {}
            }

            // ══════════ 3. GENEL DOM ══════════
            var body = document.body.innerText.toLowerCase();
            if (body.includes('tükendi') || body.includes('stokta yok') || body.includes('satışta değil')) return 0;

            var addBtn = document.querySelector('.add-to-basket, [data-test-id="addToCart"], .add-to-cart, #addToCart');
            if (addBtn && !addBtn.disabled && !addBtn.classList.contains('disabled')) return 11;

            return -1;
        }""")
        return int(result) if result is not None else -1
    except Exception:
        return -1


# =========================================================================
# HOTFIX 1.13 — OMNİKANAL YORUM KAZIMA (Amazon, N11, Çiçeksepeti, PttAVM)
# =========================================================================

def _fetch_trendyol_reviews_api(url, max_pages=3, target_count=60):
    """HOTFIX 1.26: Trendyol yorum sayfalandırması.

    Trendyol'un public review API'leri (curl_cffi/cloudscraper) ile birden çok
    sayfa gezilir, sayfada ortalama ~30 yorum olduğundan 3 sayfa ≈ 60 yorum.
    Hem `public-mdc.trendyol.com` social-review hem de eski
    `public.trendyol.com/discovery-web-socialgw-service` endpoint'leri denenir.
    Hiçbiri çalışmazsa `?pi=1, ?pi=2 ...` HTML pagination'ına düşülür.

    Returns: List[str] — yorum metinleri (tekilleştirilmiş, en fazla `target_count`).
    """
    if not url or "trendyol.com" not in (url or "").lower():
        return []

    product_id = _extract_trendyol_product_id(url)
    if not product_id:
        log.info(f"[TY-Reviews] productId çıkarılamadı: {url}")
        return []

    headers = {
        "User-Agent": _rand_ua() if "_rand_ua" in globals() else
                       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.6",
        "Origin": "https://www.trendyol.com",
        "Referer": "https://www.trendyol.com/",
    }

    # HOTFIX 1.34: Endpoint listesi güncellendi — Trendyol'un mevcut canlı
    # storefront review API'si en başta. Eski endpoint'ler kullanıcı talebi
    # üzerine kalıyor (DNS atlama filtresi de kaldırıldı).
    api_templates = [
        # ★ CANLI (X-Ray ile keşfedildi 2026-05): apigw.trendyol.com
        "https://apigw.trendyol.com/discovery-storefront-trproductgw-service/api/review-read/product-reviews/detailed?contentId={pid}&page={p}&pageSize=20&channelId=1",
        # Eski public review API'leri (DNS şu an dead olabilir, ama deneriz)
        "https://public-mdc.trendyol.com/discovery-web-websfxsocialreviewrating-santral/api/v1/product-reviews?contentId={pid}&page={p}&size=30",
        "https://public.trendyol.com/discovery-web-socialgw-service/api/review/{pid}?page={p}&order=DESC&orderBy=Score",
        "https://public-mdc.trendyol.com/discovery-web-websfxsocialreviewrating-santral/api/v1/social/review-by-content-id?contentId={pid}&page={p}&size=30",
    ]

    collected = []
    seen = set()

    def _add(body):
        if not body or len(body) < 5:
            return
        key = body[:120].lower()
        if key in seen:
            return
        seen.add(key)
        collected.append(body)

    # 1) API yolları
    # HOTFIX 1.34: DNS dead-host atlama filtresi KALDIRILDI — kullanıcı talebi
    # üzerine her template her seferinde tam olarak deneniyor. Bağımsız çağrılar
    # arasında DNS değişebilir, eski cache hatalı olabilir; içerik gerçekten
    # ölmüşse de zarar yok, hata logları kalıyor.
    for template in api_templates:
        if len(collected) >= target_count:
            break
        api_ok = False
        for p in range(0, max_pages):
            api_url = template.format(pid=product_id, p=p)
            try:
                # HOTFIX 1.32: her API isteği için yeni profil + (varsa) proxy
                api_headers, api_profile = _build_browser_headers(referer="https://www.trendyol.com/")
                # API endpoint'lerinde sayfa türünden farklı header'lar
                api_headers["Accept"] = "application/json, text/plain, */*"
                api_headers["Sec-Fetch-Dest"] = "empty"
                api_headers["Sec-Fetch-Mode"] = "cors"
                api_headers["Sec-Fetch-Site"] = "same-site"
                api_headers["Origin"] = "https://www.trendyol.com"
                proxies_cfg = _get_proxy_for_requests()
                # Önce curl_cffi (Chrome JA3 taklidi)
                resp = None
                try:
                    from curl_cffi import requests as cffi_requests
                    resp = cffi_requests.get(
                        api_url, headers=api_headers, impersonate="chrome110",
                        timeout=15, proxies=proxies_cfg,
                    )
                except Exception:
                    resp = None
                # Düşmezse cloudscraper
                if resp is None or getattr(resp, "status_code", 0) != 200:
                    import cloudscraper
                    sc = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'})
                    if proxies_cfg:
                        sc.proxies.update(proxies_cfg)
                    resp = sc.get(api_url, headers=api_headers, timeout=15)
                if resp.status_code != 200:
                    continue
                try:
                    data = resp.json()
                except Exception:
                    continue
                # HOTFIX 1.34: Trendyol farklı sürümlerde farklı sarmalayıcılar:
                #   YENİ (apigw):                {"result":{"reviews":[ {comment, rate, ...} ]}}
                #   Eski (web-socialgw):         {"result":{"productReviews":{"content":[...]}}}
                #   Daha eski:                   {"result":{"content":[...]}}
                #   En eski:                     {"productReviews":{"content":[...]}}
                content = None
                r = data.get("result") if isinstance(data, dict) else None
                if isinstance(r, dict):
                    # YENİ apigw yapısı: result.reviews
                    if isinstance(r.get("reviews"), list):
                        content = r["reviews"]
                    else:
                        pr = r.get("productReviews") or r.get("review") or {}
                        if isinstance(pr, dict) and isinstance(pr.get("content"), list):
                            content = pr["content"]
                        elif isinstance(r.get("content"), list):
                            content = r["content"]
                if content is None and isinstance(data, dict):
                    pr2 = data.get("productReviews") or {}
                    if isinstance(pr2, dict) and isinstance(pr2.get("content"), list):
                        content = pr2["content"]
                if not isinstance(content, list):
                    continue
                api_ok = True
                for rev in content:
                    if not isinstance(rev, dict):
                        continue
                    body = (rev.get("comment") or rev.get("commentText")
                            or rev.get("review") or rev.get("reviewText") or "")
                    if isinstance(body, str):
                        _add(body.strip())
                if len(collected) >= target_count:
                    break
            except Exception as api_e:
                # HOTFIX 1.34: DNS-skip filtresi kaldırıldı — kullanıcı talebi.
                # Hata olduğu gibi loglanır, template'in kalan sayfaları yine
                # denenir. DNS gerçekten dead'se aynı hata tekrarlanır ama veri
                # akışını engelleyen kısıtlama yok artık.
                log.info(f"[TY-Reviews] API hata p{p}: {api_e}")
                continue
        if api_ok and collected:
            break  # Bu template iş gördü, diğerlerini atla

    # 2) HTML pagination fallback (`?pi=1, pi=2...`)
    if len(collected) < 10:
        try:
            base = url.split("?")[0].rstrip("/")
            if not base.endswith("/yorumlar"):
                base = base + "/yorumlar"
            for p in range(1, max_pages + 1):
                page_url = f"{base}?pi={p}"
                try:
                    import cloudscraper
                    sc = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'})
                    proxies_cfg2 = _get_proxy_for_requests()
                    if proxies_cfg2:
                        sc.proxies.update(proxies_cfg2)
                    pi_headers, _ = _build_browser_headers(referer=base)
                    resp = sc.get(page_url, headers=pi_headers, timeout=20)
                    if resp.status_code != 200:
                        continue
                    soup_p = BeautifulSoup(resp.text, "lxml")
                    # Olası selectorlar
                    for sel in (".comment-text p", ".rnr-com-tx", ".pr-rnr-cn p", "[class*='CommentText']"):
                        for el in soup_p.select(sel):
                            t = el.get_text(" ", strip=True)
                            if len(t) > 10:
                                _add(t)
                    if len(collected) >= target_count:
                        break
                except Exception as html_e:
                    log.info(f"[TY-Reviews] HTML pi={p} hata: {html_e}")
                    continue
        except Exception as html_glb:
            log.info(f"[TY-Reviews] HTML pagination global: {html_glb}")

    # ─────────────────────────────────────────────────────────────────────────
    # HOTFIX 1.27: HARD REGEX FALLBACK
    # API'ler 403/429 dönerse VEYA HTML pagination boş kalırsa, son çare olarak
    # ürünün ASIL sayfasını (URL) çekip:
    #   (a) window.__INITIAL_STATE__ JSON'undan reviews/comments dizilerini regex ile yakala
    #   (b) <div class="review-text"> ve benzeri DOM düğümlerini regex ile tara
    # En azından ilk sayfada görünen 10-15 yorumu zorla çekecek garanti yol.
    # ─────────────────────────────────────────────────────────────────────────
    if len(collected) < 10:
        try:
            import cloudscraper
            sc = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'})
            proxies_cfg3 = _get_proxy_for_requests()
            if proxies_cfg3:
                sc.proxies.update(proxies_cfg3)
            base_product = url.split("?")[0]
            urls_to_try = [base_product]
            # /yorumlar ekli versiyonu da dene — bazı sayfalarda yorumlar burada
            if "/yorumlar" not in base_product:
                urls_to_try.append(base_product.rstrip("/") + "/yorumlar")

            html_full = ""
            for try_url in urls_to_try:
                try:
                    hr_headers, _ = _build_browser_headers(referer="https://www.trendyol.com/")
                    rsp = sc.get(try_url, headers=hr_headers, timeout=20)
                    if rsp.status_code == 200 and rsp.text:
                        html_full = rsp.text
                        # __INITIAL_STATE__ regex
                        try:
                            import re as _re_h27
                            m_state = _re_h27.search(
                                r'window\.__INITIAL_STATE__\s*=\s*({.*?});\s*(?:</script>|window\.)',
                                html_full, _re_h27.DOTALL,
                            ) or _re_h27.search(
                                r'window\.__INITIAL_STATE__\s*=\s*({.+?})\s*;', html_full, _re_h27.DOTALL,
                            )
                            if m_state:
                                try:
                                    state = json.loads(m_state.group(1))
                                except Exception:
                                    state = None
                                # State içinde herhangi bir derinlikte review/comment dizisi ara
                                if isinstance(state, dict):
                                    def _walk(node, depth=0):
                                        if depth > 8 or len(collected) >= target_count:
                                            return
                                        if isinstance(node, dict):
                                            for k, v in node.items():
                                                lk = k.lower()
                                                # Trendyol farklı sürümlerde "comments", "reviews",
                                                # "productReviews", "productReviewState" kullanıyor.
                                                if lk in ("comments", "reviews", "reviewlist",
                                                          "commentlist", "productreviews", "content"):
                                                    if isinstance(v, list):
                                                        for it in v:
                                                            if isinstance(it, dict):
                                                                body = (it.get("comment") or it.get("commentText")
                                                                        or it.get("review") or it.get("reviewText")
                                                                        or it.get("text") or "")
                                                                if isinstance(body, str) and len(body.strip()) > 5:
                                                                    _add(body.strip())
                                                                    if len(collected) >= target_count:
                                                                        return
                                                _walk(v, depth + 1)
                                        elif isinstance(node, list):
                                            for it in node:
                                                _walk(it, depth + 1)
                                                if len(collected) >= target_count:
                                                    return
                                    _walk(state, 0)
                                    log.info(f"[TY-Reviews] HARD __INITIAL_STATE__ → {len(collected)} yorum")
                        except Exception as state_re_err:
                            log.info(f"[TY-Reviews] __INITIAL_STATE__ regex hata: {state_re_err}")

                        # DOM regex: <div class="review-text">...<p>...</p>... gibi
                        if len(collected) < 10:
                            try:
                                import re as _re_h27b
                                # En sık karşılaşılan class'lar (p tag'i içindeki body)
                                dom_patterns = [
                                    r'<p[^>]*class="[^"]*comment-text[^"]*"[^>]*>(.*?)</p>',
                                    r'<p[^>]*class="[^"]*review-text[^"]*"[^>]*>(.*?)</p>',
                                    r'<div[^>]*class="[^"]*review-text[^"]*"[^>]*>(.*?)</div>',
                                    r'<div[^>]*class="[^"]*comment-text[^"]*"[^>]*>(.*?)</div>',
                                    r'<p[^>]*class="[^"]*rnr-com-tx[^"]*"[^>]*>(.*?)</p>',
                                    r'<span[^>]*class="[^"]*CommentText[^"]*"[^>]*>(.*?)</span>',
                                    # itemprop fallback (schema.org)
                                    r'<[^>]+itemprop="reviewBody"[^>]*>(.*?)</[a-z]+>',
                                ]
                                for pat in dom_patterns:
                                    for m in _re_h27b.finditer(pat, html_full, _re_h27b.DOTALL | _re_h27b.IGNORECASE):
                                        # HTML tag'leri temizle
                                        raw = _re_h27b.sub(r'<[^>]+>', ' ', m.group(1))
                                        raw = _re_h27b.sub(r'\s+', ' ', raw).strip()
                                        if len(raw) > 10 and len(raw) < 2000:
                                            _add(raw)
                                            if len(collected) >= target_count:
                                                break
                                    if len(collected) >= target_count:
                                        break
                                log.info(f"[TY-Reviews] HARD DOM regex → {len(collected)} yorum (toplam)")
                            except Exception as dom_re_err:
                                log.info(f"[TY-Reviews] DOM regex hata: {dom_re_err}")

                        if len(collected) >= 10:
                            break
                except Exception as fetch_e:
                    log.info(f"[TY-Reviews] HARD fetch hata ({try_url}): {fetch_e}")
                    continue
        except Exception as hard_glb:
            log.info(f"[TY-Reviews] HARD fallback global: {hard_glb}")

    # ─────────────────────────────────────────────────────────────────────────
    # HOTFIX 1.28: ZIRH DELİCİ #2 — Playwright DOM-tabanlı agresif fallback
    # API'ler 403 dönerse VE hard regex de yetersiz kalırsa son seçenek olarak
    # gerçek bir Chromium başlatılır:
    #   • /yorumlar sayfasına gidilir
    #   • text="Daha Fazla", text="Tüm yorumları gör" gibi butonlar metin bazlı
    #     hedeflenip CSS class'ından bağımsız tıklanır
    #   • Sayfa lazy-load için kademeli scroll edilir
    #   • DOM'da görünen tüm yorum elemanları toplanır
    # ─────────────────────────────────────────────────────────────────────────
    # ── HOTFIX 1.34: PW agresif fallback ARTIK HER ZAMAN ÇALIŞIR ──
    # Eski "asyncio loop tespit edip atla" mantığı kaldırıldı.
    # Sync API'yi izole worker thread'inde çalıştırıyoruz — thread'in kendi
    # event loop'u yoktur → "Sync API inside asyncio loop" hatası imkansız.
    def _pw_aggressive_inner():
        """Tüm sync_playwright bloğu — izole thread'de çalıştırılır."""
        try:
            from playwright.sync_api import sync_playwright
            # HOTFIX 1.29: Stealth eklentisi (varsa) — playwright-stealth 1.0.6
            # `stealth_sync(page)` ile page'e enjekte edilir; webdriver bayrakları,
            # navigator.plugins, navigator.languages, chrome.runtime, WebGL fingerprint
            # gibi otomasyon ipuçlarını gizler.
            try:
                from playwright_stealth import stealth_sync as _ty_stealth_sync
            except Exception:
                _ty_stealth_sync = None

            target_url = url.split("?")[0].rstrip("/")
            if "/yorumlar" not in target_url:
                target_url = target_url + "/yorumlar"

            with sync_playwright() as pw:
                # HOTFIX 1.31: GEÇİCİ — DataDome headless tespitini atlamak için
                # tarayıcı görünür modda açılır. Stabilleşince yine True'ya dönülecek.
                # Env override desteği: HEADLESS_TY=1 ile prod'da gizleyebilirsin.
                _ty_headless_env = (os.environ.get("HEADLESS_TY", "0") or "0").strip()
                _ty_headless = _ty_headless_env not in ("0", "false", "no", "")

                # HOTFIX 1.32: Residential proxy (varsa) launch level'da bağlanır.
                pw_proxy_cfg = _get_proxy_for_playwright()
                if pw_proxy_cfg:
                    log.info(f"[TY-Reviews] 🛡️ Playwright proxy aktif: {pw_proxy_cfg.get('server')}")

                _launch_kwargs = {
                    "headless": _ty_headless,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-web-security",
                        "--disable-dev-shm-usage",
                        "--disable-accelerated-2d-canvas",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-infobars",
                        "--window-size=1920,1080",
                        "--start-maximized",
                    ],
                }
                if pw_proxy_cfg:
                    _launch_kwargs["proxy"] = pw_proxy_cfg

                # HOTFIX 1.29 + 1.31 + 1.32: Cloudflare/DataDome direnci için zenginleştirilmiş Chromium args + proxy
                browser = pw.chromium.launch(**_launch_kwargs)

                # HOTFIX 1.32: UA + Sec-Ch-Ua eşlemeli profil seç → header havuzunu üret.
                pw_headers, pw_profile = _build_browser_headers(referer="https://www.trendyol.com/")
                # Playwright extra_http_headers'a UA'yı dahil etmeyiz (user_agent param ile çakışır)
                _pw_extra = {k: v for k, v in pw_headers.items() if k.lower() != "user-agent"}

                context = browser.new_context(
                    user_agent=pw_profile["ua"],
                    locale="tr-TR",
                    timezone_id="Europe/Istanbul",
                    viewport={"width": 1366, "height": 900},
                    java_script_enabled=True,
                    # WebGL/permissions fingerprint
                    color_scheme="light",
                    extra_http_headers=_pw_extra,
                )
                # HOTFIX 1.29: Manuel stealth init — webdriver / plugins / chrome runtime sahteleştir
                try:
                    context.add_init_script("""
                        // navigator.webdriver → undefined
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        // chrome runtime mock
                        window.chrome = window.chrome || {};
                        window.chrome.runtime = window.chrome.runtime || {};
                        // plugins / languages doğal değerler
                        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                        Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR','tr','en-US','en']});
                        // permissions API anomalisini düzelt (CF bunu sorar)
                        const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
                        if (originalQuery) {
                            window.navigator.permissions.query = (parameters) => (
                                parameters.name === 'notifications'
                                    ? Promise.resolve({state: Notification.permission})
                                    : originalQuery(parameters)
                            );
                        }
                    """)
                except Exception as init_err:
                    log.info(f"[TY-Reviews] PW init script eklenemedi: {init_err}")

                page = context.new_page()
                # Eğer paket yüklü ise stealth_sync uygula (ek katman)
                if _ty_stealth_sync:
                    try:
                        _ty_stealth_sync(page)
                    except Exception as st_err:
                        log.info(f"[TY-Reviews] stealth_sync uygulanamadı: {st_err}")

                # ── HOTFIX 1.31: X-RAY OPERASYONU — Network Response Interception ──
                # DataDome shadow-ban senaryosunda DOM hiçbir zaman güncellenmese bile
                # XHR/Fetch yanıtları hâlâ ham JSON olarak ağdan geçiyor. page.on("response")
                # ile bu paketleri tarayıcı sayfada render edemese de YAKALARIZ.
                #
                # İlgilendiğimiz endpoint'ler (URL parçası eşleşmesi yeterli):
                #   • product-reviews            → yorum listesi
                #   • review-by-content-id       → yorum (mobile API)
                #   • /api/review/               → eski socialgw endpoint
                #   • merchant                   → satıcı detayı
                #   • social/                    → social-review
                #   • rating-summary             → toplu rating + yorum sayısı
                xray_state = {
                    "reviews": [],          # toplanan yorum metinleri (string)
                    "seller": None,         # ilk bulunan satıcı adı
                    "brand": None,          # ilk bulunan marka adı
                    "intercepted": 0,       # JSON yakalama sayacı (debug)
                    "blocked_403_429": 0,   # bloklanan istek sayacı (Shadow Ban göstergesi)
                }
                _seen_keys = set()  # tekrar ekleme engeli

                def _xray_walk(node, depth=0):
                    """JSON içinde dolaş, yorum/satıcı/marka isimlerini yakala."""
                    if depth > 9:
                        return
                    if isinstance(node, dict):
                        for k, v in node.items():
                            kl = str(k).lower()
                            # Yorum metni alanları
                            if kl in ("comment", "commenttext", "review", "reviewtext", "text") and isinstance(v, str):
                                t = v.strip()
                                if 5 <= len(t) <= 4000:
                                    key = t[:120].lower()
                                    if key not in _seen_keys:
                                        _seen_keys.add(key)
                                        xray_state["reviews"].append(t)
                            # Satıcı / merchant
                            if kl in ("merchant", "seller") and not xray_state["seller"]:
                                if isinstance(v, dict):
                                    nm = v.get("name") or v.get("legalName")
                                    if isinstance(nm, str) and len(nm.strip()) > 1:
                                        xray_state["seller"] = nm.strip()
                                elif isinstance(v, str) and len(v.strip()) > 1:
                                    xray_state["seller"] = v.strip()
                            if kl in ("merchantname", "sellername") and not xray_state["seller"]:
                                if isinstance(v, str) and len(v.strip()) > 1:
                                    xray_state["seller"] = v.strip()
                            # Marka
                            if kl == "brand" and not xray_state["brand"]:
                                if isinstance(v, dict):
                                    nm = v.get("name")
                                    if isinstance(nm, str) and len(nm.strip()) > 1:
                                        xray_state["brand"] = nm.strip()
                                elif isinstance(v, str) and len(v.strip()) > 1:
                                    xray_state["brand"] = v.strip()
                            _xray_walk(v, depth + 1)
                    elif isinstance(node, list):
                        for it in node:
                            _xray_walk(it, depth + 1)

                def _on_response(resp):
                    try:
                        rurl = (resp.url or "").lower()
                        rstatus = resp.status
                        # Shadow-ban telemetrisi
                        if rstatus in (403, 429) and any(kw in rurl for kw in ("trendyol", "review", "merchant")):
                            xray_state["blocked_403_429"] += 1
                            log.info(f"[X-Ray] ⛔ {rstatus} blok: {resp.url[:140]}")
                            return
                        if rstatus != 200:
                            return
                        # Hedef endpoint filtresi
                        kw_match = (
                            "product-reviews" in rurl
                            or "review-by-content-id" in rurl
                            or "/api/review/" in rurl
                            or "/review?" in rurl
                            or "rating-summary" in rurl
                            or "/social/" in rurl
                            or ("merchant" in rurl and ("public" in rurl or "/api/" in rurl))
                            or "websfxsocialreviewrating" in rurl
                        )
                        if not kw_match:
                            return
                        ctype = (resp.headers.get("content-type") or "").lower()
                        if "json" not in ctype:
                            return
                        try:
                            body = resp.json()
                        except Exception:
                            return
                        xray_state["intercepted"] += 1
                        _xray_walk(body, 0)
                        log.info(f"[X-Ray] 🔬 Yakalandı (#{xray_state['intercepted']}): {resp.url[:120]}  "
                              f"→ revs={len(xray_state['reviews'])} seller={xray_state['seller']!r}")
                    except Exception as on_err:
                        # Listener içi hata page lifecycle'ını bozmasın
                        try:
                            log.info(f"[X-Ray] handler hata: {on_err}")
                        except Exception:
                            pass

                try:
                    page.on("response", _on_response)
                    log.info("[X-Ray] response listener bağlandı.")
                except Exception as listen_err:
                    log.info(f"[X-Ray] listener bağlanamadı: {listen_err}")

                try:
                    # ── HOTFIX 1.30: Sabırlı Sayfa Yükleme Stratejisi ──
                    # 1) DOM iskeleti gelsin (hızlı)
                    page.goto(target_url, timeout=40000, wait_until="domcontentloaded")
                    # 2) Network'ün boşalmasını bekle (lazy resource'ları bitir)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        # bazı CDN'ler gerçek "idle" olmaz — sessizce devam
                        pass
                    # 3) JS render için kör 3sn bekleme (skeleton → gerçek içerik)
                    page.wait_for_timeout(3000)

                    # HOTFIX 1.29: Cloudflare/CAPTCHA tespiti
                    try:
                        title_ck = (page.title() or "").lower()
                        body_ck = (page.evaluate("() => (document.body && document.body.innerText) ? document.body.innerText.slice(0,2000) : ''") or "").lower()
                        captcha_signals = ("just a moment", "checking your browser",
                                           "captcha", "cf-challenge", "robot musunuz",
                                           "doğrulama", "access denied", "datadome")
                        if any(sig in title_ck or sig in body_ck for sig in captcha_signals):
                            try:
                                page.screenshot(path="debug_trendyol_error.png", full_page=True)
                                log.info("[TY-Reviews] ⛔ CAPTCHA/CF tespit edildi — debug_trendyol_error.png kaydedildi")
                            except Exception as ss_err:
                                log.info(f"[TY-Reviews] screenshot hata: {ss_err}")
                    except Exception:
                        pass

                    # Cookie / popup kapatıcı
                    try:
                        page.evaluate("""() => {
                            document.querySelectorAll('button').forEach(b => {
                                var t = (b.innerText||'').toLowerCase();
                                if (t.includes('kabul') || t.includes('tamam') || t.includes('reddet')) {
                                    try { b.click(); } catch(e){}
                                }
                            });
                        }""")
                    except Exception:
                        pass

                    # ── HOTFIX 1.30: İlk yorum elementinin DOM'da belirmesini ZORUNLU bekle ──
                    # Class'a sıkı bağımlı kalmamak için birden fazla selector denenir;
                    # en geç 8 saniye içinde herhangi biri ortaya çıkmazsa pas geçilir.
                    review_anchors = [
                        ".comment-text",
                        ".rnr-com-tx",
                        "[itemprop='reviewBody']",
                        "[class*='CommentText']",
                        "[class*='commentText']",
                        "div.review-text",
                        "p.review-text",
                    ]
                    anchor_found = False
                    for sel in review_anchors:
                        try:
                            page.wait_for_selector(sel, timeout=8000, state="attached")
                            anchor_found = True
                            log.info(f"[TY-Reviews] DOM hazır: '{sel}' selector'ı belirdi.")
                            break
                        except Exception:
                            continue
                    if not anchor_found:
                        log.info("[TY-Reviews] DOM'da yorum elementi henüz görünmedi — yine de scroll loop'una geçiliyor.")

                    # ── HOTFIX 1.30: İnsan-Benzeri Kademeli Scroll ──
                    # `scrollBy(0, document.body.scrollHeight)` tek hamlede dibe iniyordu;
                    # Trendyol'un IntersectionObserver tabanlı lazy-loader'ı bunu fark
                    # edip yorumları SKIP edebiliyor. Şimdi 500px / 500ms hız ile inilir,
                    # her 1500ms'de bir mini "okuma" duraklaması verilir.
                    def _human_scroll_step():
                        """Aşamalı 500px scroll + insan ritmi mola."""
                        try:
                            for _step in range(8):  # 500px × 8 = 4000px (bir ekran ~3000px)
                                page.evaluate("window.scrollBy(0, 500);")
                                page.wait_for_timeout(450 + (50 if _step % 3 == 0 else 0))
                            # Mini okuma molası
                            page.wait_for_timeout(900)
                        except Exception:
                            pass

                    # Agresif loop: scroll + "Daha Fazla" tıkla — max ~12 tur
                    last_count = 0
                    for i in range(12):
                        # HOTFIX 1.30: insan-benzeri kademeli scroll (sıçrama yok)
                        _human_scroll_step()

                        # Metin bazlı buton tıklatma (CSS class'ı umursamıyoruz)
                        try:
                            page.evaluate(r"""() => {
                                var keywords = ['daha fazla', 'tüm yorumları gör', 'tümünü gör',
                                                'devamını gör', 'sonraki', 'load more', 'show more'];
                                var els = document.querySelectorAll('button, a, span, div, li');
                                for (var i = 0; i < els.length; i++) {
                                    var el = els[i];
                                    if (!el || el.offsetParent === null) continue;
                                    var t = (el.innerText || '').toLowerCase().trim();
                                    if (t.length === 0 || t.length > 60) continue;
                                    if (t.includes('yap') || t.includes('soru') ||
                                        t.includes('giriş') || t.includes('kayıt')) continue;
                                    for (var k = 0; k < keywords.length; k++) {
                                        if (t.indexOf(keywords[k]) !== -1) {
                                            try { el.scrollIntoView({block:'center'}); el.click(); return true; } catch(e){}
                                        }
                                    }
                                }
                                return false;
                            }""")
                        except Exception:
                            pass

                        page.wait_for_timeout(900)

                        # DOM'dan tüm yorumları topla
                        try:
                            new_texts = page.evaluate(r"""() => {
                                var sels = [
                                    '.comment-text p',
                                    'p.comment-text',
                                    '.rnr-com-tx',
                                    '[class*="CommentText"]',
                                    '[class*="commentText"]',
                                    '[itemprop="reviewBody"]',
                                    '.pr-rnr-cn p',
                                    'div.review-text',
                                    'p.review-text',
                                ];
                                var out = [];
                                for (var i = 0; i < sels.length; i++) {
                                    var els = document.querySelectorAll(sels[i]);
                                    for (var j = 0; j < els.length; j++) {
                                        var t = (els[j].innerText || '').trim();
                                        if (t.length >= 10 && t.length < 2000) out.push(t);
                                    }
                                }
                                return out;
                            }""")
                            if isinstance(new_texts, list):
                                for t in new_texts:
                                    _add(t)
                        except Exception as ext_err:
                            log.info(f"[TY-Reviews] PW extract hata: {ext_err}")

                        if len(collected) >= target_count:
                            break
                        # Sayım değişmediyse 2 tur sonra çık (yorum bitmiş)
                        if len(collected) == last_count and i >= 4:
                            break
                        last_count = len(collected)

                    # ── HOTFIX 1.31: X-Ray sonuçlarını DOM toplamasıyla birleştir ──
                    # Network'ten yakalanan yorumlar DOM'da hiç render edilmemiş olsa bile
                    # geçerli AI veri seti oluşturur. Tekrarlar zaten _add() içinde elenir.
                    if xray_state["reviews"]:
                        before = len(collected)
                        for t in xray_state["reviews"]:
                            _add(t)
                        gained = len(collected) - before
                        log.info(f"[X-Ray] 🔄 DOM ↔ network birleştirme: +{gained} yorum "
                              f"(toplam yakalanan paket: {xray_state['intercepted']}, "
                              f"403/429 blok: {xray_state['blocked_403_429']})")
                    # Satıcı/marka da yakalandıysa logla — caller bu bilgiyi kullanmasa bile
                    # debug için görünür olsun (ileride seller/brand'i return ederiz).
                    if xray_state["seller"] or xray_state["brand"]:
                        log.info(f"[X-Ray] 🎯 Yan ürün: seller={xray_state['seller']!r} brand={xray_state['brand']!r}")

                    # HOTFIX 1.29: Olay Yeri İncelemesi — döngü bittiğinde hâlâ
                    # 0 yorum yakalandıysa sayfanın o anki halini ekran görüntüsü
                    # olarak diske al. Captcha mı, gerçekten boş yorum mu, gözle gör.
                    if len(collected) == 0:
                        try:
                            page.screenshot(path="debug_trendyol_error.png", full_page=True)
                            log.info(f"[TY-Reviews] 📸 0 yorum — debug_trendyol_error.png kaydedildi "
                                  f"(intercept: {xray_state['intercepted']}, blocked: {xray_state['blocked_403_429']})")
                        except Exception as ss_err:
                            log.info(f"[TY-Reviews] son screenshot hata: {ss_err}")
                except Exception as pw_inner:
                    # HOTFIX 1.29: Beklenmedik hata olursa bile screenshot dene
                    try:
                        page.screenshot(path="debug_trendyol_error.png", full_page=True)
                        log.info(f"[TY-Reviews] PW navigation hata + screenshot alındı: {pw_inner}")
                    except Exception:
                        log.info(f"[TY-Reviews] PW navigation hata (screenshot başarısız): {pw_inner}")
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass
                    try:
                        browser.close()
                    except Exception:
                        pass
            log.info(f"[TY-Reviews] PW agresif → {len(collected)} yorum (toplam)")
        except ImportError:
            log.info("[TY-Reviews] Playwright yüklü değil, agresif fallback atlandı.")
        except Exception as pw_err:
            log.info(f"[TY-Reviews] PW agresif fallback hata: {pw_err}")

    # ── HOTFIX 1.34: izole thread'de Playwright çalıştır ──
    if len(collected) < 10:
        try:
            _run_in_isolated_thread(_pw_aggressive_inner)
        except Exception as thread_err:
            log.info(f"[TY-Reviews] PW thread sarmalayıcı hata: {thread_err}")

    log.info(f"[TY-Reviews] {url} → {len(collected)} yorum toplandı (target {target_count}).")
    return collected[:target_count]


def _scrape_reviews_omnichannel(url, html_content):
    """
    Kombine/Yorum Analizi için farklı platformlardan yorum metinlerini
    DOM seviyesinde toplar. Sadece HTML'den çalışır — ekstra HTTP istemez.

    Desteklenen: amazon.com.tr, n11.com, ciceksepeti.com, pttavm.com.
    Trendyol/HB için ayrı (mevcut) Playwright/cffi yolları kullanılmaya devam eder.
    """
    from bs4 import BeautifulSoup
    if not html_content:
        return []

    try:
        soup = BeautifulSoup(html_content, 'html.parser')
    except Exception:
        return []

    reviews = []
    u = (url or "").lower()

    try:
        # 1. AMAZON TR
        if "amazon.com.tr" in u or "amzn.eu" in u:
            # Amazon yorumları .review-text-content span > div tipinde gelir
            for el in soup.select('.review-text-content span'):
                text = el.get_text(" ", strip=True)
                if len(text) > 10:
                    reviews.append(text)
            # Yedek selektör (mobil/eski layout)
            if not reviews:
                for el in soup.select('[data-hook="review-body"] span'):
                    text = el.get_text(" ", strip=True)
                    if len(text) > 10:
                        reviews.append(text)

        # 2. N11
        elif "n11.com" in u:
            elements = soup.select('.comment-content') or soup.select('.review-text') \
                       or soup.select('[class*="commentText"]') or soup.select('[class*="reviewText"]')
            for el in elements:
                t = el.get_text(" ", strip=True)
                if len(t) > 10:
                    reviews.append(t)

        # 3. ÇİÇEKSEPETİ
        elif "ciceksepeti.com" in u:
            elements = soup.select('.product-review__content') or soup.select('.review-text') \
                       or soup.select('[class*="reviewContent"]') or soup.select('[class*="comment-text"]')
            for el in elements:
                t = el.get_text(" ", strip=True)
                if len(t) > 10:
                    reviews.append(t)

        # 4. PTTAVM
        elif "pttavm.com" in u:
            elements = soup.select('.comment-text') or soup.select('.evaluation-text') \
                       or soup.select('[class*="commentBody"]') or soup.select('[class*="reviewItem"]')
            for el in elements:
                t = el.get_text(" ", strip=True)
                if len(t) > 10:
                    reviews.append(t)
    except Exception as e:
        log.info(f"[Omnichannel] Review parse error for {url}: {e}")

    # Tekrarları çıkar, en güncel 30 yorumu döndür
    seen, unique = set(), []
    for r in reviews:
        key = r[:120].lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)
        if len(unique) >= 30:
            break
    return unique


def _fetch_reviews_html_lightweight(url):
    """Yorum kazıma için sayfanın HTML'ini browser AÇMADAN getirir.
    Amazon → curl_cffi (Chrome TLS taklidi, anti-bot dirençli).
    Diğerleri (N11/ÇS/PttAVM) → cloudscraper.

    Returns: html_string or None
    """
    u = (url or "").lower()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        if "amazon.com.tr" in u or "amzn.eu" in u:
            # AMAZON ÖZEL SAVUNMA HATTI: curl_cffi ile Chrome JA3 taklidi
            try:
                from curl_cffi import requests as cffi_requests
                resp = cffi_requests.get(url, headers=headers, impersonate="chrome110", timeout=20)
                if resp.status_code == 200:
                    log.info(f"[Amazon-CFFI] Reviews HTML alındı ({len(resp.text)} bytes)")
                    return resp.text
                log.info(f"[Amazon-CFFI] HTTP {resp.status_code}")
            except ImportError:
                log.info("[Amazon] curl_cffi yok — cloudscraper'a düşülüyor.")
            except Exception as cffi_e:
                log.info(f"[Amazon-CFFI] hata: {cffi_e}")
        # Diğerleri (veya Amazon cffi başarısız olursa) → cloudscraper
        import cloudscraper
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'mobile': False, 'platform': 'windows'})
        resp = scraper.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.text
        log.info(f"[Lightweight Reviews] HTTP {resp.status_code} for {url}")
        return None
    except Exception as e:
        log.info(f"[Lightweight Reviews] hata: {e}")
        return None


def extract_reviews(page, url, is_trendyol, is_hepsiburada):
    """Extract reviews from platform using the improved extraction logic.

    HOTFIX 1.13: Trendyol/HB dışındaki desteklenen platformlar (Amazon, N11,
    Çiçeksepeti, PttAVM) için browser-DOM yerine hafif HTTP + DOM parse yolu
    kullanılır. Diğer platformlar için mevcut Playwright tabanlı akış aynen sürer.
    """
    import time

    # ── HOTFIX 1.13: Yeni nesil platformlar için hafif yol ──
    u = (url or "").lower()

    # ── HOTFIX 1.33 (REGRESSION FIX): Trendyol API early-exit KALDIRILDI ──
    # Önceden HOTFIX 1.26 ile Playwright ATLAYIP API'yi birincil yol yapmıştık.
    # Pratikte bu, eski "çalışan ve 60+ yorum getiren" Playwright-DOM yolunu
    # eziyor; API yanıtı 1-14 yorumda tıkalı kaldığı için ≥5 koşulu Playwright'ı
    # tamamen devre dışı bırakıyordu. Eski stabil davranışa dönüldü:
    #   1) Trendyol → Playwright DOM scroll + extract (birincil — eskiden çalışan)
    #   2) Yetersizse → _fetch_trendyol_reviews_api (sadece secondary fallback)

    is_omnichannel_lite = (
        ("amazon.com.tr" in u) or ("amzn.eu" in u)
        or ("n11.com" in u) or ("ciceksepeti.com" in u) or ("pttavm.com" in u)
    )
    if is_omnichannel_lite:
        try:
            html = _fetch_reviews_html_lightweight(url)
            if html:
                revs = _scrape_reviews_omnichannel(url, html)
                if revs:
                    log.info(f"[Omnichannel] {url} → {len(revs)} yorum (hafif yol)")
                    return revs
        except Exception as e:
            log.info(f"[Omnichannel] hata: {e}")
        # Boş döndüyse, hata mesajı yerine sessizce boş liste — analyze_reviews_with_ai
        # zaten "İşe yarar yorum bulunamadı" yolunu yönetiyor.
        return []

    raw_data_set = set()

    # Navigate to reviews
    if is_trendyol:
        if "/yorumlar" not in page.url:
            try:
                page.goto(page.url.split('?')[0] + "/yorumlar", timeout=40000)
                page.wait_for_timeout(6000)
                page.evaluate("window.scrollBy(0, 2000);")
                page.wait_for_timeout(2000)
            except Exception:
                pass
    elif is_hepsiburada:
        # ══════════ HEPSİBURADA TAB-BASED REVIEW NAVIGATION ══════════
        # Yeni istihbarat: HB ürün sayfasında yorumlar AYRI URL'de DEĞİL,
        # "Değerlendirmeler" sekmesinin içinde (aynı sayfada, tab switch).
        # Strateji:
        #   1) Sayfada kalan modal/cookie popup'ları kapat
        #   2) Sayfayı aşağıya scroll et (tab container'ı görünür yap)
        #   3) "Değerlendirmeler" sekmesini BUL ve TIKLA (hem text hem a/tab)
        #   4) Review card'ların render olmasını wait_for_selector ile bekle
        #   5) Lazy load için tekrar scroll
        try:
            # 1) Modal/cookie/popup temizle
            try:
                page.evaluate("""() => {
                    document.querySelectorAll('.modal, .popup, [id*="onetrust"], [class*="cookie"], [class*="CookieBanner"]').forEach(el => { try{ el.style.display='none'; } catch(e){} });
                    document.querySelectorAll('button').forEach(b => {
                        var t = (b.innerText||'').toLowerCase();
                        if (t.includes('kabul') || t.includes('tamam')) { try{ b.click(); }catch(e){} }
                    });
                }""")
            except: pass

            # 2) Tab container'ı viewport'a getirmek için orta seviye scroll
            page.evaluate("window.scrollTo(0, Math.floor(document.body.scrollHeight * 0.45));")
            page.wait_for_timeout(1200)

            # 3) "Değerlendirmeler" tab'ını bul ve tıkla — çok yönlü hedefleme
            tab_clicked = page.evaluate(r"""() => {
                // Önce text bazlı kesin hedefleme
                var candidates = Array.from(document.querySelectorAll(
                    'a, button, div[role="tab"], li[role="tab"], span[role="tab"], ' +
                    '[data-test-id*="review"], [data-test-id*="Review"], ' +
                    '[data-test-id*="tab"], [id*="review"], [id*="Review"], ' +
                    '[class*="tab"], [class*="Tab"]'
                ));
                for (var i = 0; i < candidates.length; i++) {
                    var el = candidates[i];
                    if (!el || el.offsetParent === null) continue;
                    var txt = (el.innerText || '').toLowerCase().trim().split('\n')[0];
                    // "Değerlendirmeler (61)" eşleşsin, "Değerlendirme yap" eşleşmesin
                    if (txt.length > 60) continue;
                    if (txt.includes('yap') || txt.includes('soru') || txt.includes('cevap')) continue;
                    if (txt.startsWith('değerlendirme') || txt.startsWith('değerlendirmeler') ||
                        txt.startsWith('yorumlar') || /^değerlendirmeler?\s*\(\d+\)$/.test(txt)) {
                        try {
                            el.scrollIntoView({behavior: 'smooth', block: 'center'});
                            el.click();
                            return true;
                        } catch(e) {}
                    }
                }
                // Fallback: a[href="#yorumlar"] veya data-test-id="reviews-tab"
                var direct = document.querySelector(
                    'a[href*="#yorum"], a[href*="#review"], ' +
                    '[data-test-id="reviews-tab"], [data-test-id="review-tab"], ' +
                    '#reviews-tab, #review-tab'
                );
                if (direct && direct.offsetParent !== null) {
                    try {
                        direct.scrollIntoView({behavior: 'smooth', block: 'center'});
                        direct.click();
                        return true;
                    } catch(e) {}
                }
                return false;
            }""")
            log.info(f"[Worker] HB 'Değerlendirmeler' tab clicked: {tab_clicked}")

            # 4) Review card'ların render olmasını bekle (max 6s)
            try:
                page.wait_for_selector(
                    '[data-test-id*="review"], [class*="ReviewCard"], [class*="hermes-Review"], [itemprop="review"], .hermes-ReviewCard-module-3S6oE, [class*="rnr-"] p',
                    timeout=6000,
                    state='attached'
                )
            except Exception:
                pass

            # 5) Lazy load trigger — aşamalı scroll
            for _ in range(4):
                page.evaluate("window.scrollBy(0, 1200);")
                page.wait_for_timeout(900)
        except Exception as e:
            log.info(f"[Worker] HB Review Navigation Error: {e}")

    # Login trap escape
    if "giris" in page.url.lower() or "login" in page.url.lower():
        try:
            page.goto(url, timeout=40000)
            page.wait_for_timeout(5000)
            page.evaluate("window.scrollBy(0, 4000);")
            page.wait_for_timeout(2000)
        except Exception:
            pass

    # Extraction loop
    for step in range(25):
        if step == 5 and len(raw_data_set) == 0 and is_trendyol and "/yorumlar" in page.url:
            try:
                page.goto(url, timeout=40000)
                page.wait_for_timeout(5000)
                page.evaluate("window.scrollBy(0, 3000);")
                page.wait_for_timeout(2000)
            except Exception:
                pass

        if is_trendyol:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        else:
            page.evaluate("window.scrollBy(0, 1500);")

        # Expand truncated reviews
        if is_trendyol:
            try:
                page.evaluate("""() => {
                    document.querySelectorAll('a, button, span').forEach(el => {
                        var txt = (el.innerText || "").toLowerCase().trim();
                        if (txt === 'devamını oku' || txt === 'devamini oku' || txt === 'daha fazla' ||
                            (el.className && el.className.toLowerCase().includes('read-more'))) {
                            try { el.click(); } catch(e) {}
                        }
                    });
                }""")
            except Exception:
                pass

        # Pagination clicker
        js_click = f"""() => {{
            var targetPage = '{(step + 2)}'; var clicked = false;
            document.querySelectorAll('button, a, div, span, li').forEach(b => {{
                if(clicked) return; if(b.offsetParent === null) return;
                var t = (b.innerText || "").toLowerCase().trim();
                var c = (b.className || "").toLowerCase();
                var p = (b.parentElement?.className || "").toLowerCase();
                if(t.includes('yap') || t.includes('soru') || t.includes('giriş') || t.includes('kayıt')) return;
                if(t.includes('daha fazla') || t === 'tüm yorumları gör' || t === 'sonraki' || t === 'devamını gör' || t === 'ileri' || t === '>' || t === 'load more') {{
                    try {{ b.click(); clicked = true; }} catch(e){{}}
                }}
                else if (t === targetPage && (c.includes('page') || c.includes('pagination') || p.includes('pagination'))) {{
                    try {{ b.click(); clicked = true; }} catch(e){{}}
                }}
            }});
            if(!clicked) {{
                var lmb = document.querySelector('[data-test-id*="show-more"], [data-test-id*="load-more"], [class*="showMore"], [class*="load-more"]');
                if(lmb && lmb.offsetParent !== null) {{ try {{ lmb.click(); }} catch(e){{}} }}
            }}
        }}"""
        try:
            page.evaluate(js_click)
        except Exception:
            pass

        page.wait_for_timeout(1500)

        # Extract reviews with Trendyol-specific selectors
        js_extractor = """() => {
            var res = [];
            var domain = window.location.hostname;
            var isTrendyol = domain.includes('trendyol.com');
            var isHB = domain.includes('hepsiburada.com');
            var isReviewPage = window.location.href.includes('yorumlar') || window.location.href.includes('-yorumlari');
            // ── HB Tab-based review extraction (aggressive) ──
            if (isHB) {
                var hbSelectors = [
                    '[class*="ReviewCard"] p',
                    '[class*="hermes-Review"] p',
                    '.hermes-ReviewCard-module-3S6oE',
                    '[class*="hermes-"] [class*="comment"]',
                    '[data-test-id="review-text"]',
                    '[data-test-id*="review"] p',
                    '[itemprop="reviewBody"]',
                    '[itemprop="description"]',
                    'div[class*="review"] p',
                    'div[class*="Review"] p'
                ];
                var hbFound = false;
                hbSelectors.forEach(sel => {
                    document.querySelectorAll(sel).forEach(el => {
                        if (el.offsetParent === null) return;
                        var txt = (el.innerText || '').trim();
                        // HB'de kısa yorumlar da olabilir ("Harika bir ürün")
                        if (txt.length >= 10 && txt.length < 2000) {
                            // Arayüz metinleri filtrele
                            var lc = txt.toLowerCase();
                            if (lc.includes('bu değerlendirme faydalı') || lc.includes('bildir') ||
                                lc.includes('satıcısından aldı') || lc === 'tüm değerlendirmeler') return;
                            hbFound = true;
                            res.push(txt);
                        }
                    });
                });
                // Fallback: ReviewCard container'ının text content'i
                if (!hbFound) {
                    document.querySelectorAll('[class*="ReviewCard"], [class*="hermes-Review"], [itemprop="review"]').forEach(card => {
                        if (card.offsetParent === null) return;
                        var txt = (card.innerText || '').trim();
                        if (txt.length >= 10 && txt.length < 2000) res.push(txt);
                    });
                }
                return res;
            }
            if (isTrendyol) {
                var selectors = ['.comment-text', '.comment-content', '[class*="comment-text"]', '[class*="comment-content"]', '[class*="CommentText"]', '[itemprop="reviewBody"]', '[itemprop="description"]'];
                var found = false;
                selectors.forEach(sel => { document.querySelectorAll(sel).forEach(el => {
                    if (el.offsetParent !== null) { var txt = el.innerText.trim(); if (txt.length > 15 && txt.length < 2000) { found = true; res.push(txt); } }
                }); });
                if (!found && isReviewPage) {
                    document.querySelectorAll('div, p, span').forEach(el => {
                        if (el.offsetParent !== null && el.children.length <= 2) {
                            var txt = el.innerText.trim();
                            if (txt.length > 25 && txt.length < 1500 && !txt.includes('Satıcı:') && !txt.includes('Beğen') && !txt.includes('Şikayet Et') && !/^\\d+ kişi/.test(txt)) { res.push(txt); }
                        }
                    });
                }
            } else if (isReviewPage) {
                document.querySelectorAll('p, span, div.comment-text, div[itemprop="reviewBody"]').forEach(el => {
                    if (el.offsetParent !== null) { var txt = el.innerText.trim(); if(txt.length > 25 && txt.length < 2000) res.push(txt); }
                });
            } else {
                var nodes = document.querySelectorAll('.comment-text, .rnr-com-tx, [itemprop="reviewBody"], div[class*="ReviewCard"] p, div[class*="hermes-"] p, [data-test-id*="review"] p, [class*="review-text"], [class*="rnr-"] p, div[class*="review"] p');
                nodes.forEach(el => { if (el.offsetParent !== null) { var txt = el.innerText.trim(); if(txt.length > 25 && txt.length < 2000) res.push(txt); } });
            }
            return res;
        }"""
        try:
            page_texts = page.evaluate(js_extractor)
            if page_texts:
                _, _, _, _, _, banned_phrases = _import_bmk_utils()  # call once per page, not per text
                # HB'de kısa yorumlar normal ("Harika bir ürün" = 3 kelime).
                min_words = 2 if is_hepsiburada else 5
                for text in page_texts:
                    clean_text = " ".join(text.split())
                    if len(clean_text.split()) >= min_words and clean_text not in raw_data_set:
                        if not any(banned in clean_text.lower() for banned in banned_phrases):
                            raw_data_set.add(clean_text)
        except Exception:
            pass

    # ── HOTFIX 1.33: Trendyol için API güvenlik ağı (yalnızca Playwright boş kaldıysa) ──
    # Eski (çalışan) davranış birincil yol Playwright. Eğer Playwright DOM hiç
    # yorum getirmediyse SECONDARY olarak API helper'ını dene; API'nin tek
    # başına yetersiz olduğu durumlarda yine boş döner — ama kötü senaryoda
    # 0 yerine 1-15 yorum gelmesi yine de kazanç.
    if is_trendyol and len(raw_data_set) == 0:
        try:
            ty_api_revs = _fetch_trendyol_reviews_api(url, max_pages=3, target_count=60)
            if ty_api_revs:
                log.info(f"[Trendyol] Playwright 0 → API güvenlik ağı: {len(ty_api_revs)} yorum")
                for t in ty_api_revs:
                    if t and t not in raw_data_set:
                        raw_data_set.add(t)
        except Exception as ty_safety_err:
            log.info(f"[Trendyol] API güvenlik ağı hatası: {ty_safety_err}")

    return list(raw_data_set)


# =========================================================================
# HOTFIX 1.14 — AI JSON sanitize + tag builder yardımcıları
# AI bazen 'basarili' / 'sikayet' listelerine kaçak metin parçaları (örn. '\n,
# "genel":') döndürebiliyor. Bu helper'lar her zaman temiz string listesi
# döndürür; non-string item'leri filtreler, çok kısa parçaları atar.
# =========================================================================

def _sanitize_ai_list(value, max_items=8, min_chars=4):
    """AI'dan gelen listeyi temizle: sadece düzgün string item'leri tut."""
    if value is None:
        return []
    if isinstance(value, str):
        # Tek string halinde gelmiş — ayraç bul
        parts = [p.strip() for p in value.replace('\\n', '\n').split('\n') if p.strip()]
        if len(parts) <= 1:
            parts = [p.strip() for p in value.split('|') if p.strip()]
        value = parts
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, str):
            # Kaçak dict/list/None — atla
            continue
        s = item.strip().strip('"').strip("'").strip(',').strip()
        # JSON sızıntı imzaları — bu kalıpları içeren maddeleri reddet
        if not s or len(s) < min_chars:
            continue
        if any(sig in s.lower() for sig in (
            '"basarili"', '"sikayet"', '"genel"', '"basarili" :', '"sikayet" :', '"genel" :',
            '\\n', '],\n', '}, "', "', '", '"\n ]', '"\n}', '\\"genel\\"',
        )):
            continue
        # Çok fazla noktalama (raw JSON parça imzası)
        if s.count('"') >= 3 or s.count('}') >= 1 or s.count(']') >= 1:
            continue
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _stringify_ai_field(value):
    """Düz metin alanını güvenli string'e indir (list ise birleştir, None ise '')."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(x).strip() for x in value if isinstance(x, str) and x.strip())
    if isinstance(value, dict):
        # Beklenmeyen dict → key:value flatten
        return " ".join(f"{k}: {v}" for k, v in value.items() if isinstance(v, (str, int, float)))
    return str(value).strip()


def _build_review_tags(items, dot_color):
    """AI listesini sanitize edip <ul><li> rozet listesi olarak HTML'e dönüştür."""
    items = _sanitize_ai_list(items)
    if not items:
        return "<div style='color:var(--muted);font-size:13px;font-style:italic;padding:8px 0;'>—</div>"
    html = "<ul style='padding-left:0;margin:0;list-style:none;color:var(--text-soft);font-size:14px;line-height:1.6;'>"
    for it in items:
        html += (
            f"<li style='margin-bottom:10px;display:flex;align-items:flex-start;gap:8px;'>"
            f"<span style='color:{dot_color};font-size:18px;line-height:1;'>•</span>"
            f"<span>{it}</span></li>"
        )
    html += "</ul>"
    return html


def analyze_reviews_with_ai(reviews, api_key, url=None):
    """Use Groq AI to analyze reviews.

    HOTFIX 1.13: `url` parametresi opsiyonel — verilirse platform-bilinçli analiz yapılır
    (Amazon müşterisi kargo hızına, Çiçeksepeti müşterisi paketlemeye duyarlıdır vb.).

    HOTFIX 1.25: Multi-tenant fallback — kullanıcı kendi anahtarını girmediyse,
    Setting tablosundaki sistem anahtarına, oradan da .env GROQ_API_KEY'e geçer.
    Yeni kayıtlı kullanıcılar "API Anahtarı eksik" hatası almadan analiz çalıştırabilir.
    """
    api_key = _resolve_groq_key(api_key)
    if not api_key:
        return {"error": "API Anahtarı eksik veya geçersiz. Lütfen Ayarlar'dan geçerli bir Groq API Anahtarı GİRİN ya da admin sistem anahtarı tanımlasın."}

    # Platform tespiti (URL'den)
    platform_label = None
    platform_hint = ""
    try:
        u = (url or "").lower()
        if "trendyol.com" in u:
            platform_label = "Trendyol"
            platform_hint = "Trendyol müşterisi: kargo hızı, kapıda iade kolaylığı ve fiyat-performansa duyarlıdır."
        elif "hepsiburada.com" in u:
            platform_label = "Hepsiburada"
            platform_hint = "Hepsiburada müşterisi: orijinal ürün garantisi, fatura/garanti süreçleri ve teslimat uyumluluğuna duyarlıdır."
        elif "n11.com" in u:
            platform_label = "N11"
            platform_hint = "N11 müşterisi: ürün açıklaması ile gerçek ürün uyumu ve mağaza güvenirliğine duyarlıdır."
        elif "ciceksepeti.com" in u:
            platform_label = "Çiçeksepeti"
            platform_hint = "Çiçeksepeti müşterisi: paketleme estetiği, teslim zamanlaması (özel günler) ve ürünün tazeliğine duyarlıdır."
        elif "pttavm.com" in u:
            platform_label = "PttAVM"
            platform_hint = "PttAVM müşterisi: yerli üretim vurgusu, kurumsal güvenilirlik ve devlet destekli kargo süreçlerine duyarlıdır."
        elif "amazon.com" in u or "amzn.eu" in u:
            platform_label = "Amazon"
            platform_hint = "Amazon müşterisi: kargo hızı (Prime), iade kolaylığı, ürün açıklaması doğruluğu ve global marka güvenine duyarlıdır."
    except Exception:
        pass

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        temiz_metinler = [r.replace('"', "'").replace('\n', ' ') for r in reviews]
        review_count_actual = len(temiz_metinler)

        # HOTFIX 1.13: Platform-aware prompt
        platform_block = ""
        if platform_label:
            platform_block = (
                f"\n        PLATFORM BAĞLAMI: Bu yorumlar {platform_label} pazar yerinden geliyor. "
                f"{platform_hint} Analizini bu kitle dinamiğine göre kalibre et — örneğin Amazon "
                f"yorumlarında kargo hızı odaklı şikayet daha kritikken, Çiçeksepeti'nde paketleme "
                f"estetiği aynı kritiklikte değerlendirilmelidir.\n"
            )
        else:
            platform_block = (
                "\n        PLATFORM BAĞLAMI: Yorumlar farklı Türk pazar yerlerinden olabilir "
                "(Trendyol/Hepsiburada/N11/Çiçeksepeti/PttAVM/Amazon). Her platformun müşteri "
                "kitlesi farklıdır; tonlamadan platformu sezerek analizini buna göre kalibre et.\n"
            )

        # ── HOTFIX 1.27: Halüsinasyon Kesici (system prompt + low-data uyarısı) ──
        # Düşük yorum sayısında LLM uydurma yapmaması için hem rolü kısıtla, hem de
        # kullanıcı promptuna "veri yetersiz" senaryosunu ekle.
        system_prompt = (
            "Sen bir e-ticaret yorum analizi asistanısın. KESİN KURAL: SADECE sana metin "
            "olarak iletilen yorumların İÇİNDE açıkça yer alan bilgileri analiz edersin. "
            "Genel-geçer bilgi, ürün kategorisine dair varsayım, başka kullanıcı deneyimi "
            "veya benzer ürünlerden kıyas EKLEMEZSİN. Asla uydurma örnek, kurgusal müşteri "
            "yorumu veya halüsinasyon üretmezsin. "
            "DİKKAT: Sana verilen yorum sayısı az ise (örneğin 1-5 arası), ASLA uydurma "
            "veya genel geçer bilgiler ekleme. Sadece sana metin olarak iletilen "
            "yorumların içindeki bilgileri analiz et. Çıkarım yapacak yeterli veri yoksa "
            "'Yeterli veri sağlanamadığı için detaylı analiz yapılamamıştır' de. "
            "Çıktı yalnızca istenen JSON şemasında olur; serbest metin / madde dışı içerik üretmezsin."
        )

        low_data_block = ""
        if review_count_actual <= 5:
            low_data_block = (
                f"\n        ⚠️ DÜŞÜK VERİ MODU: Toplam {review_count_actual} yorum sağlandı. "
                f"Bu sayı detaylı analiz için yetersizdir. \"basarili\" ve \"sikayet\" listelerine "
                f"SADECE açıkça metinde geçen bilgileri yaz; kalan slotları doldurmak için "
                f"uydurma yapma — boş bırak veya az sayıda madde dön. \"genel\" alanında "
                f"yeterli veri olmadığını şu cümle ile belirt: "
                f"\"Yeterli veri sağlanamadığı için detaylı analiz yapılamamıştır.\"\n"
            )

        # HOTFIX 1.40: AI'ya iletilen veride negatif kelime tespiti yap — varsa
        # "sikayet" listesini zorla doldurması için açık talimat gönder.
        _neg_signals = ['kırık','kötü','iade','eksik','defolu','yırtık','koptu','kalitesiz','çöp',
                        'berbat','maalesef','sorun','sıkıntı','farklı','zarar','plastik','yamuk',
                        'bozuldu','çizik','ucuz duruyor','beklediğim gibi değil','memnun değil',
                        'pişman','geri gönder','küçük geldi','büyük geldi','renk farkı','yanlış',
                        'beğenmedim','hayal kırıklığı','hatalı','kullanışsız','dayanıksız']
        _joined_text = ' '.join(temiz_metinler).lower()
        _neg_hits = [w for w in _neg_signals if w in _joined_text]
        sikayet_zorunlu_block = ""
        if _neg_hits:
            sikayet_zorunlu_block = (
                f"\n        ⚠️ ZORUNLU ŞİKAYET TESPİTİ: Yorum metinlerinde şu negatif "
                f"sinyaller AÇIKÇA tespit edildi: {', '.join(_neg_hits[:8])}. "
                f"Bu nedenle \"sikayet\" listesi BOŞ DÖNEMEZ — bu sinyallere dayalı "
                f"olarak metinde geçen en az 1, mümkünse 2-4 farklı somut şikayet "
                f"maddesi çıkar (örn: kalite, beden, renk, kargo, paketleme, vb. "
                f"hangi konu varsa). Yine de UYDURMA yapma — sadece metinde GEÇEN "
                f"şikayetleri özetle.\n"
            )

        prompt = f"""Aşağıdaki metinler e-ticaret müşteri yorumlarıdır. Arayüz yazılarını yoksay.
        {platform_block}{low_data_block}{sikayet_zorunlu_block}
        ÇOK ÖNEMLİ KATI KURALLAR:
        1. "basarili" anahtarına ÜRÜNDE müşterilerin GERÇEKTEN BEĞENDİĞİ ve metinde AÇIKÇA YER ALAN, birbirinden farklı en fazla 5 özelliği yaz. Yorum metninde geçmeyen hiçbir şeyi ekleme. Veri yetersizse 1-2 madde dön ya da boş [] bırak. Liste (Array) formatında dön.
        2. "sikayet" anahtarına müşterilerin metinde AÇIKÇA dile getirdiği farklı şikayetleri en fazla 5 maddede yaz. Yukarıda ⚠️ ZORUNLU ŞİKAYET TESPİTİ bloğu varsa o blokta belirtilen kurala mutlaka uy. Şikayet yoksa VE yukarıda zorunluluk uyarısı yoksa boş [] dön; uydurma yapma. Liste (Array) formatında dön.
        3. "genel" anahtarına yorumlar yeterli ise genel müşteri memnuniyetini 2 cümle ile özetle; yorumlar 1-5 adet arası ise "Yeterli veri sağlanamadığı için detaylı analiz yapılamamıştır" yaz. Düz yazı (string) dön.
        JSON formatı TEK BİR OBJE olmalıdır: {{"basarili": ["...", "..."], "sikayet": ["...", "..."], "genel": "..."}}
        Toplam yorum sayısı: {review_count_actual}
        Metinler: {' | '.join(temiz_metinler)}"""

        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.1,
            max_tokens=1500,
            response_format={"type": "json_object"}
        )
        raw_content = response.choices[0].message.content or ""

        # HOTFIX 1.14: JSON parse hardening — AI ara sıra kaçak ',' veya '\n' bırakıyor.
        parsed = None
        try:
            parsed = json.loads(raw_content)
        except Exception:
            # Olası kaçak: trailing comma, fazladan satır. Regex ile ilk { ... } bloğunu yakala
            import re as _re
            m = _re.search(r'\{[\s\S]*\}', raw_content)
            if m:
                candidate = m.group(0)
                # Trailing comma temizliği
                candidate = _re.sub(r',\s*([}\]])', r'\1', candidate)
                try:
                    parsed = json.loads(candidate)
                except Exception as e2:
                    log.info(f"[Worker] AI JSON repair failed: {e2}")
                    parsed = None

        if not isinstance(parsed, dict):
            return {"error": "AI yanıtı geçerli JSON döndürmedi."}

        # HOTFIX 1.14: Liste alanlarını sanitize et — kaçak '\n' / ']' / '},' parçaları temizlenir
        return {
            "basarili": _sanitize_ai_list(parsed.get("basarili"), max_items=8),
            "sikayet":  _sanitize_ai_list(parsed.get("sikayet"),  max_items=8),
            "genel":    _stringify_ai_field(parsed.get("genel")),
        }
    except Exception as e:
        log.info(f"[Worker] AI analysis error: {e}")
        return {"error": f"Yapay Zeka Hatası: {str(e)}"}


# =========================================================================
# HEADLESS REPORT GENERATORS
# =========================================================================

def run_price_headless(urls, api_key):
    """Run price analysis and return HTML report string."""
    from models import get_tr_now
    from groq import Groq
    from datetime import datetime as dt
    fiyati_temizle, standard_fiyat_formati, urun_ismi_temizle, marka_adi_bul, get_domain, _ = _import_bmk_utils()

    # HOTFIX 1.25: User → Setting → .env fallback chain.
    api_key = _resolve_groq_key(api_key)
    client = Groq(api_key=api_key) if api_key else None
    sonuclar = []
    referans_url = urls[0]

    base_cost = 0.0
    clean_urls = []
    for u in urls:
        if str(u).startswith('__COST__:'):
            try: base_cost = float(str(u).split('__COST__:')[1])
            except: pass
        else:
            clean_urls.append(u)
    urls = clean_urls
    referans_url = urls[0] if urls else ""

    import random
    import json
    from playwright.sync_api import sync_playwright
    import playwright_stealth

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Human enumeration user-agent and viewport
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            viewport={"width": random.randint(1366, 1920), "height": random.randint(768, 1080)},
            extra_http_headers={
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            }
        )
        page = context.new_page()

        # FAIL-SAFE STEALTH UYGULAMASI (HOTFIX 1.40: pozitif tanı logu)
        _stealth_applied = False
        try:
            if hasattr(playwright_stealth, 'stealth_sync'):
                playwright_stealth.stealth_sync(page)
                _stealth_applied = "stealth_sync"
            elif hasattr(playwright_stealth, 'Stealth'):
                playwright_stealth.Stealth().apply_stealth_sync(page)
                _stealth_applied = "Stealth().apply_stealth_sync"
            else:
                playwright_stealth.stealth(page) # Legacy structure
                _stealth_applied = "stealth (legacy)"
        except Exception as stealth_e:
            log.info(f"⚠️ [Worker/Price] Stealth uygulanamadı, normal devam ediliyor: {stealth_e}")
        if _stealth_applied:
            log.info(f"🥷 [Worker/Price] Stealth aktif: {_stealth_applied}")

        for idx, url in enumerate(urls):
            url = url.strip()
            if not url:
                continue

            platform_name = marka_adi_bul(url)
            fiyat = "Bulunamadı"
            urun_ismi = "İsim Bulunamadı"
            durum = "OK"

            # ── FAZ 3.5 / HOTFIX 1.40: Desteklenmeyen platformlar artık SESSİZCE atılmıyor ──
            # Daha önce: continue ile listeden silinerek 5 ürün → 1 ürün gözüküyordu.
            # Yeni davranış: placeholder kayıt eklenir, raporda "⚠️ DESTEKLENMİYOR" rozeti görünür.
            _ul_check = url.lower()
            if not ("trendyol.com" in _ul_check or "hepsiburada.com" in _ul_check):
                sonuclar.append({
                    "Platform": platform_name or "Bilinmeyen",
                    "UrunAdi": "Bu platform fiyat analizinde desteklenmiyor (yalnızca Trendyol & Hepsiburada)",
                    "RawFiyat": "Desteklenmiyor",
                    "CleanFiyat": 0.0,
                    "URL": url,
                    "Durum": "NOT_LOADED"
                })
                continue

            try:
                if "hepsiburada.com" not in url:
                    page.goto(url, timeout=40000)
                    # HOTFIX 1.40: Bot tespitini yumuşatmak için +%20 bekleme
                    page.wait_for_timeout(random.randint(2400, 4800))

                    # Scroll slightly to simulate human
                    page.mouse.wheel(0, random.randint(300, 700))
                    page.wait_for_timeout(random.randint(600, 1800))

                    durum = check_blocked(page)

                if durum == "OK":
                    try:
                        page.evaluate("document.querySelectorAll('.modal, .popup, [id*=\"onetrust\"]').forEach(el => el.style.display='none');")
                    except Exception:
                        pass
                    
                    if "hepsiburada.com" in url:
                        # Extract via JSON-LD payload
                        try:
                            json_dumps = page.evaluate("""() => {
                                return Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                                            .map(el => el.innerText);
                            }""")
                            
                            found_json = False
                            for j in json_dumps:
                                try:
                                    data = json.loads(j)
                                    if isinstance(data, dict):
                                        if data.get('@type') == 'Product' or (data.get('@context') == 'https://schema.org' and '@graph' in data):
                                            items = data.get('@graph', [data])
                                            for item in items:
                                                if item.get('@type') == 'Product':
                                                    found_json = True
                                                    n = item.get('name')
                                                    if n: urun_ismi = urun_ismi_temizle(n)
                                                    
                                                    offers = item.get('offers', {})
                                                    val = None
                                                    if isinstance(offers, dict):
                                                        val = offers.get('price')
                                                    elif isinstance(offers, list) and len(offers) > 0:
                                                        val = offers[0].get('price')
                                                        
                                                    if val: fiyat = str(val)
                                    elif isinstance(data, list):
                                        for item in data:
                                            if isinstance(item, dict) and item.get('@type') == 'Product':
                                                found_json = True
                                                n = item.get('name')
                                                if n: urun_ismi = urun_ismi_temizle(n)
                                                
                                                offers = item.get('offers', {})
                                                val = None
                                                if isinstance(offers, dict):
                                                    val = offers.get('price')
                                                if val: fiyat = str(val)
                                except Exception:
                                    pass
                                    
                            if fiyat == "Bulunamadı" or not found_json:
                                # Fallback to direct DOM for price if JSON fails
                                urun_ismi = extract_product_name(page)
                                fiyat = extract_price(page)
                        except Exception as json_e:
                            log.info("JS Extraction failed:", json_e)
                            urun_ismi = extract_product_name(page)
                            fiyat = extract_price(page)
                    else:
                        # Standard Trendyol extraction
                        urun_ismi = extract_product_name(page)
                        fiyat = extract_price(page)
                
                # Fallback to Cloudscraper if failed or blocked (Not for Hepsiburada)
                if durum != "OK" or fiyat == "Bulunamadı" or urun_ismi_temizle(urun_ismi) == "İsim Bulunamadı":
                    import cloudscraper
                    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})
                    resp = scraper.get(url, timeout=20)
                    if resp.status_code == 200:
                        data = extract_data_from_html(resp.text, url)
                        if data["price"] != "Bulunamadı":
                            fiyat = data["price"]
                            durum = "OK" # Overwrite status if fallback worked
                            if data["name"] != "İsim Bulunamadı":
                                urun_ismi = data["name"]

                # 3. HEPSİBURADA CİDDİ ÇÖZÜM: curl_cffi (Playwright'ı atlar ve doğrudan Chrome TLS taklidi yapar)
                if "hepsiburada.com" in url:
                    try:
                        cffi_data = _scrape_hepsiburada_cffi(url, fetch_reviews=False)
                        if cffi_data:
                            if cffi_data.get("price"):
                                fiyat = str(cffi_data["price"])
                                durum = "OK"
                            if cffi_data.get("name") and urun_ismi == "İsim Bulunamadı":
                                urun_ismi = cffi_data["name"]
                            if cffi_data.get("stock", -1) != -1:
                                pass  # stock is not tracked in price analysis
                            log.info(f"[Worker] HB cffi success — price: {fiyat}")
                    except Exception as cffi_e:
                        log.info(f"[Worker] HB cffi failed in price analysis: {cffi_e}")

                # Fallback #2: Legacy HepsiBurada direct API
                if "hepsiburada.com" in url and (fiyat == "Bulunamadı" or not fiyat or durum != "OK"):
                    try:
                        hb_data = _hepsiburada_api_fallback(url)
                        if hb_data:
                            if hb_data.get("price"):
                                fiyat = str(hb_data["price"])
                                durum = "OK"
                            if hb_data.get("name") and urun_ismi == "İsim Bulunamadı":
                                urun_ismi = hb_data["name"]
                            log.info(f"[Worker] HB API fallback success in price analysis: {fiyat}")
                    except Exception as hb_e:
                        log.info(f"[Worker] HB API fallback failed in price analysis: {hb_e}")


                sonuclar.append({
                    "Platform": platform_name,
                    "UrunAdi": urun_ismi,
                    "RawFiyat": fiyat.strip() if fiyat else "Bulunamadı",
                    "CleanFiyat": fiyati_temizle(fiyat) if fiyat else 0.0,
                    "URL": url,
                    "Durum": durum
                })
            except Exception as e:
                log.info(f"[Worker] Exception in run_price_headless for {url}: {e}")
                import traceback
                traceback.print_exc()
                sonuclar.append({
                    "Platform": platform_name, "UrunAdi": "Hata", "RawFiyat": f"Hata: {str(e)[:50]}",
                    "CleanFiyat": 0.0, "URL": url, "Durum": "ERROR"
                })

    # Generate AI summary
    ai_ozet = ""
    if client and sonuclar:
        analiz = []
        for s in sonuclar:
            if s["URL"] == referans_url and s["Durum"] == "OK":
                analiz.append(f"🎯 REFERANS ({s['Platform']}): {s['CleanFiyat']} TL - {s['UrunAdi']}")
        for s in sonuclar:
            if s["URL"] != referans_url and s["Durum"] == "OK":
                analiz.append(f"🔗 COMPETITOR ({s['Platform']}): {s['CleanFiyat']} TL - {s['UrunAdi']}")
        if analiz:
            try:
                response = client.chat.completions.create(
                    messages=[{"role": "user", "content": f"Analyze pricing. Verify if products match. Write 2-3 sentence Turkish summary.\nData: {' | '.join(analiz)}"}],
                    model="llama-3.3-70b-versatile", temperature=0.3, max_tokens=300
                )
                ai_ozet = response.choices[0].message.content.strip()
            except Exception:
                ai_ozet = "⚠️ AI Analizi Başarısız."

    # Build HTML
    resmi_fiyat = 0.0
    for s in sonuclar:
        if s["URL"] == referans_url and s["Durum"] == "OK":
            resmi_fiyat = s["CleanFiyat"]
            break

    cards = ""
    for s in sonuclar:
        price_float = s["CleanFiyat"]
        formatted_price = standard_fiyat_formati(price_float)
        badge = "✅ EŞİT"
        # HOTFIX 1.39: Sade CSS var — fallback dizgesi yok
        bg_col = "var(--success-bg)"
        text_col = "var(--success)"

        if s["Durum"] == "BLOCKED":
            badge = "⛔ GÜVENLİK"
            bg_col = "var(--danger)"
            text_col = "#fff"
            formatted_price = "Güvenlik Duvarı"
        elif s["Durum"] == "ERROR":
            badge = "⚠️ HATA"
            bg_col = "var(--danger)"
            text_col = "#fff"
            formatted_price = "Sistem Hatası"
        elif s["Durum"] == "NOT_LOADED" or s["RawFiyat"] == "Bulunamadı":
            badge = "⚠️ OKUNAMADI"
            bg_col = "var(--muted)"
            text_col = "#fff"
            formatted_price = "Bulunamadı"
        elif s["URL"] != referans_url:
            if price_float <= 0:
                badge = "⚠️ OKUNAMADI"
                bg_col = "var(--muted)"
                text_col = "#fff"
            elif price_float < resmi_fiyat:
                badge = f"🔻 DÜŞÜK (-{standard_fiyat_formati(resmi_fiyat - price_float)})"
                bg_col = "var(--danger)"
                text_col = "#fff"
            elif price_float > resmi_fiyat:
                badge = f"🔺 YÜKSEK (+{standard_fiyat_formati(price_float - resmi_fiyat)})"
                bg_col = "var(--success)"
                text_col = "#fff"

        is_ref = s['URL'] == referans_url
        if is_ref:
            bg_col = "var(--title)"
            text_col = "#fff"

        cards += f"""
        <div style="border-bottom:1px solid var(--border);padding:24px 0;display:grid;grid-template-columns:1fr 180px 140px;gap:20px;align-items:center;">
            <div style="display:flex;flex-direction:column;gap:8px;">
                <div style="font-weight:700;font-size:18px;color:{'var(--info)' if is_ref else 'var(--text)'};">{s['Platform']} {'<span style="font-size:11px;color:var(--info-soft);font-weight:normal;letter-spacing:1px;">(BAZ ALINAN)</span>' if is_ref else ''}</div>
                <div style="font-size:13px;color:var(--muted);line-height:1.4;">{str(s['UrunAdi'])[:70]}...</div>
            </div>
            <div style="font-size:22px;font-weight:800;color:var(--text);text-align:right;">{formatted_price}</div>
            <div style="text-align:right;">
                <a href='{s['URL']}' target='_blank' style='display:inline-block;margin-bottom:10px;font-size:13px;font-weight:600;color:var(--muted);text-decoration:none;transition:color 0.2s;' onmouseover='this.style.color="var(--text)"' onmouseout='this.style.color="var(--muted)"'>Ürüne Git ↗</a>
                <br><span style="display:inline-block;font-size:11px;font-weight:800;color:{text_col};background:{bg_col};padding:4px 10px;border-radius:99px;letter-spacing:0.5px;border:1px solid rgba(0,0,0,0.08);">{badge}</span>
            </div>
        </div>"""

    buybox_html = ""
    if base_cost > 0 and sonuclar:
        try:
            our_price = next((s['CleanFiyat'] for s in sonuclar if s['URL'] == referans_url and s['Durum'] == 'OK'), 0.0)
            competitors = [s['CleanFiyat'] for s in sonuclar if s['URL'] != referans_url and s['Durum'] == 'OK' and s['CleanFiyat'] > 0]
            cheapest_comp = min(competitors) if competitors else 0.0
            
            if our_price > 0:
                profit = our_price - base_cost
                margin = (profit / our_price) * 100 if our_price > 0 else 0
                # HOTFIX 1.39: Tek CSS var — hem progress bar fill hem metin için
                profit_color = "var(--success)" if profit >= 0 else "var(--danger)"
                profit_text = f"+{profit:,.2f}" if profit >= 0 else f"{profit:,.2f}"

                # BuyBox Target (1 kuruş cheaper than cheapest comp, or our price if no comp)
                buybox_target = (cheapest_comp - 0.01) if (cheapest_comp > 0 and cheapest_comp < our_price) else our_price
                buybox_profit = buybox_target - base_cost
                
                cost_ratio = min(100, max(0, (base_cost / our_price) * 100))
                profit_ratio = 100 - cost_ratio if profit >= 0 else 0
                
                # SVG Icon
                bb_svg = '''<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.9 5.8a2 2 0 0 1-1.2 1.2L3 12l5.8 1.9a2 2 0 0 1 1.2 1.2L12 21l1.9-5.8a2 2 0 0 1 1.2-1.2L21 12l-5.8-1.9a2 2 0 0 1-1.2-1.2L12 3Z"/></svg>'''
                
                # ---- Cost Guard: Determine if Buy Box recommendation is a loss ----
                buybox_is_loss = buybox_target < base_cost
                
                if buybox_is_loss:
                    buybox_card_bg = "var(--danger-bg)"
                    buybox_card_border = "var(--danger)"
                    buybox_badge_color = "var(--danger)"
                    buybox_badge_bg = "var(--danger-bg)"
                    buybox_badge_text = "⚠️ ZARAR UYARISI"
                    buybox_icon = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color:var(--danger);"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>'
                    buybox_message = f"Buy Box'ı kazanmak için fiyatın <b style='font-size:19px; color:var(--danger);'>{buybox_target:,.2f} ₺</b> seviyesine inmesi gerekiyor. Ancak bu tutar birim maliyetinizin ({base_cost:,.2f} ₺) altında kaldığı için bu üründe rekabete girmeniz <b style='color:var(--danger);'>zarar etmenize</b> yol açacaktır. Lütfen tedarik maliyetlerinizi gözden geçirin."
                    buybox_footer = f"→ Bu fiyatta tahmini <b style='color:var(--danger);'>zararınız</b>: <span style='color:var(--danger); font-weight:800; font-size:16px;'>{buybox_profit:,.2f} ₺</span>"
                else:
                    buybox_card_bg = "var(--grad)"
                    buybox_card_border = "var(--border)"
                    buybox_badge_color = "var(--title)"
                    buybox_badge_bg = "var(--link-bg)"
                    buybox_badge_text = "BUY BOX TAVSİYESİ"
                    buybox_icon = ''
                    buybox_message = f"Fiyatı <b style='font-size:19px; color:var(--success);'>{buybox_target:,.2f} ₺</b> seviyesine çekerseniz Buy Box'ı kazanma ihtimaliniz <b style='color:var(--title);'>%85</b> olarak öngörülüyor."
                    buybox_footer = f"→ Bu fiyatta tahmini kârınız: <span style='color:var(--success); font-weight:800; font-size:16px;'>{buybox_profit:,.2f} ₺</span>"

                buybox_html = f'''
                <div style="background:transparent; border:1px solid var(--border); border-radius:16px; padding:24px; margin-bottom:30px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; flex-wrap:wrap; gap:16px;">
                        <div>
                            <div style="font-size:20px; font-weight:800; color:var(--text); display:flex; align-items:center; gap:10px;">
                                <span style="color:var(--title);display:flex;">{bb_svg}</span> AI Fiyatlandırma ve Buy Box Stratejisi
                            </div>
                            <div style="color:var(--muted); font-size:14px; margin-top:6px;">Birim Maliyet ve Rekabet Analizi</div>
                        </div>
                    </div>
                    
                    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:20px;">
                        <div style="background:var(--ai-bg); padding:24px; border-radius:16px; border:1px solid var(--ai-border); position:relative; overflow:hidden;">
                            <div style="font-size:12px; font-weight:800; color:var(--muted); margin-bottom:16px; text-transform:uppercase; letter-spacing:1px;">Kâr Marjı Durumu</div>
                            
                            <div style="display:flex; justify-content:space-between; margin-bottom:12px; font-weight:600; font-size:14px; color:var(--text); border-bottom:1px solid var(--border); padding-bottom:10px;">
                                <span style="opacity:0.8;">Maliyet: {base_cost:,.2f} ₺</span>
                                <span>Satış: {our_price:,.2f} ₺</span>
                            </div>
                            
                            <div style="height:14px; background:var(--border); border-radius:99px; overflow:hidden; display:flex; margin-bottom:16px;">
                                <div style="width:{cost_ratio}%; background:var(--muted);"></div>
                                <div style="width:{profit_ratio}%; background:{profit_color};"></div>
                            </div>

                            <div style="font-size:20px; font-weight:800; color:{profit_color}; text-align:right;">
                                Net Kâr: {profit_text} ₺ <span style="font-size:14px; opacity:0.8;">(%{margin:.1f})</span>
                            </div>
                        </div>
                        
                        <div style="background:{buybox_card_bg}; padding:24px; border-radius:16px; border:1px solid {buybox_card_border}; display:flex; flex-direction:column; justify-content:center; position:relative; overflow:hidden;">
                            <div style="display:flex; align-items:center; gap:10px; margin-bottom:16px;">
                                {buybox_icon}
                                <span style="font-weight:800; color:{buybox_badge_color}; background:{buybox_badge_bg}; padding:6px 14px; border-radius:99px; font-size:11px; letter-spacing:0.5px; border:1px solid {buybox_card_border};">{buybox_badge_text}</span>
                            </div>
                            <div style="font-size:16px; line-height:1.6; color:var(--text);">
                                {buybox_message}
                            </div>
                            <div style="margin-top:16px; font-size:14px; color:var(--muted); font-weight:600; padding-top:16px; border-top:1px solid var(--border);">
                                {buybox_footer}
                            </div>
                        </div>
                    </div>
                </div>
                '''


        except Exception as e:
            log.info(f"[Worker] Error calculating buybox: {e}")

    if ai_ozet:
        import re
        ai_formatted = re.sub(r'(\d+[\.,]\d+\s*TL|\%?\d+)', r'<b style="color:var(--text-strong);">\1</b>', ai_ozet)
        ai_formatted = ai_formatted.replace(chr(10), '<br><br>')
        ai_html = f"""<div style='background:var(--grad);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border:1px solid var(--border);border-radius:24px;padding:32px;margin-bottom:30px;color:var(--text);line-height:1.8;box-shadow:0 20px 40px -10px rgba(139,92,246,0.15);'><div style='display:flex;align-items:center;gap:12px;margin-bottom:20px;color:var(--title);'><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/><path d="M5 3v4"/><path d="M19 17v4"/><path d="M3 5h4"/><path d="M17 19h4"/></svg><b style='font-size:20px;letter-spacing:0.5px;'>AI Karşılaştırma Stratejisi</b></div><div style='font-size:15px;letter-spacing:0.3px;color:var(--text);'>{ai_formatted}</div></div>"""
    else:
        ai_html = ""

    theme_html = _report_theme_block()
    return f"""<html><head><meta charset="utf-8"><link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;700;800&display=swap" rel="stylesheet">{theme_html}<style>.container{{max-width:1000px;}}</style></head>
    <body><div class="container"><div style="color:var(--title);font-weight:bold;letter-spacing:2px;font-size:13px;margin-bottom:5px;">BMK VERİ ODAKLI DANIŞMANLIK HİZMETİ</div>
    <h1 style="margin:0 0 30px 0;font-size:36px;">Ürün Ücret Karşılaştırma Raporu</h1>{buybox_html}{ai_html}{cards}
    <div style="text-align:center;margin-top:40px;color:var(--report-color);font-size:14px;">Rapor Tarihi: {get_tr_now().strftime('%d.%m.%Y %H:%M')}</div></div></body></html>"""


def run_review_headless(urls, api_key):
    """Run review analysis and return HTML report string."""
    from models import get_tr_now
    from datetime import datetime as dt
    fiyati_temizle, standard_fiyat_formati, urun_ismi_temizle, marka_adi_bul, get_domain, _ = _import_bmk_utils()

    # HOTFIX 1.25: api_key fallback (User → Setting → .env). Aşağıda
    # analyze_reviews_with_ai çağrılarına resolved key geçer; "eksik anahtar"
    # hatası kullanıcı kendi key'ini girmediyse de fırlatılmaz.
    api_key = _resolve_groq_key(api_key)

    sonuclar = []
    base_cost = 0.0
    clean_urls = []
    for u in urls:
        if str(u).startswith('__COST__:'):
            try: base_cost = float(str(u).split('__COST__:')[1])
            except: pass
        else:
            clean_urls.append(u)
    urls = clean_urls
    referans_url = urls[0] if urls else ""

    import random
    import json
    from playwright.sync_api import sync_playwright
    import playwright_stealth

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            viewport={"width": random.randint(1366, 1920), "height": random.randint(768, 1080)},
            extra_http_headers={
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            }
        )
        page = context.new_page()
        
        # FAIL-SAFE STEALTH UYGULAMASI (HOTFIX 1.40: pozitif tanı logu)
        _stealth_applied = False
        try:
            if hasattr(playwright_stealth, 'stealth_sync'):
                playwright_stealth.stealth_sync(page)
                _stealth_applied = "stealth_sync"
            elif hasattr(playwright_stealth, 'Stealth'):
                playwright_stealth.Stealth().apply_stealth_sync(page)
                _stealth_applied = "Stealth().apply_stealth_sync"
            else:
                playwright_stealth.stealth(page) # Legacy structure
                _stealth_applied = "stealth (legacy)"
        except Exception as stealth_e:
            log.info(f"⚠️ [Worker/Review] Stealth uygulanamadı, normal devam ediliyor: {stealth_e}")
        if _stealth_applied:
            log.info(f"🥷 [Worker/Review] Stealth aktif: {_stealth_applied}")

        for idx, url in enumerate(urls):
            url = url.strip()
            if not url:
                continue

            platform_name = marka_adi_bul(url)
            domain = get_domain(url)
            is_trendyol = "trendyol.com" in url
            is_hepsiburada = "hepsiburada.com" in url

            # ── FAZ 3.5 / HOTFIX 1.40: Desteklenmeyen platformlar artık SESSİZCE atılmıyor ──
            # Amazon/N11/Çiçeksepeti/PttAVM dinamik (GraphQL/JS) yorum yüklediği için
            # statik scrape boş döner — ama kullanıcının raporunda hâlâ görünür olmaları gerekir.
            if not (is_trendyol or is_hepsiburada):
                logo_url = f"https://icon.horse/icon/{domain}" if domain else ""
                sonuclar.append({
                    "Platform": platform_name,
                    "Logo": logo_url,
                    "Satici": "—",
                    "SellerRating": None,
                    "UrunAdi": "Bu platform yorum analizinde desteklenmiyor (yalnızca Trendyol & Hepsiburada).",
                    "URL": url,
                    "Analiz": "<div style='color:var(--muted);text-align:center;padding:30px;line-height:1.7;'>📭 Bu pazaryeri için derin yorum analizi şu anda devre dışıdır.<br><span style='font-size:12px;'>Trendyol &amp; Hepsiburada bağlantıları için tam analiz çalışmaktadır.</span></div>",
                    "Sayi": 0
                })
                continue

            urun_ismi = "İsim Bulunamadı"
            satici = "Bulunamadı"
            yorum_ozeti = ""
            incelenen = 0
            olumlu = 0
            olumsuz = 0
            durum = "OK"

            try:
                if not is_hepsiburada:
                    page.goto(url, timeout=40000)
                    # HOTFIX 1.34: Network idle'ı bekle ki merchant XHR'ları tamamlansın
                    try:
                        page.wait_for_load_state("networkidle", timeout=12000)
                    except Exception:
                        pass
                    # HOTFIX 1.40: Bot tespitini yumuşatmak için +%20 bekleme
                    page.wait_for_timeout(random.randint(2400, 4800))

                    # Mouse Scroll human enum
                    page.mouse.wheel(0, random.randint(300, 700))
                    page.wait_for_timeout(random.randint(960, 2160))

                    durum = check_blocked(page)

                if durum == "OK":
                    try:
                        page.evaluate("document.querySelectorAll('.modal, .popup, [id*=\"onetrust\"]').forEach(el => el.style.display='none');")
                    except Exception:
                        pass
                    
                    raw_data_set = set()
                    
                    if is_trendyol:
                        urun_ismi = extract_product_name(page)
                        satici = extract_seller_name(page)

                        # ─── HOTFIX 1.38: X-Ray Network Interceptor (mevcut page'e bağla) ───
                        # Trendyol'un yeni frontend'i SSR HTML'inde hiç review JSON ya da
                        # bilinen class göndermiyor — review verisi tamamen client-side XHR.
                        # apigw / public-sdc Cloudflare-block / DNS-dead. Çözüm: page'in
                        # YAPTIĞI XHR'ları intercept et. DOM render başarısız olsa bile
                        # network paketleri yakalanırsa veri kazanılır.
                        xray_state = {"reviews": [], "seller": None, "brand": None,
                                      "intercepted": 0, "blocked": 0}
                        _seen_xray = set()

                        def _xray_walk(node, depth=0):
                            if depth > 9:
                                return
                            if isinstance(node, dict):
                                for k, v in node.items():
                                    kl = str(k).lower()
                                    if kl in ("comment", "commenttext", "review",
                                              "reviewtext", "text") and isinstance(v, str):
                                        t = v.strip()
                                        if 5 <= len(t) <= 4000:
                                            key = t[:120].lower()
                                            if key not in _seen_xray:
                                                _seen_xray.add(key)
                                                xray_state["reviews"].append(t)
                                    if kl in ("merchant", "seller") and not xray_state["seller"]:
                                        if isinstance(v, dict):
                                            nm = v.get("name") or v.get("legalName")
                                            if isinstance(nm, str) and len(nm.strip()) > 1:
                                                xray_state["seller"] = nm.strip()
                                    if kl in ("merchantname", "sellername") and not xray_state["seller"]:
                                        if isinstance(v, str) and len(v.strip()) > 1:
                                            xray_state["seller"] = v.strip()
                                    if kl == "brand" and not xray_state["brand"]:
                                        if isinstance(v, dict):
                                            nm = v.get("name")
                                            if isinstance(nm, str) and len(nm.strip()) > 1:
                                                xray_state["brand"] = nm.strip()
                                    _xray_walk(v, depth + 1)
                            elif isinstance(node, list):
                                for it in node:
                                    _xray_walk(it, depth + 1)

                        def _xray_handler(resp):
                            try:
                                rurl = (resp.url or "").lower()
                                rstatus = resp.status
                                if rstatus in (403, 429, 503, 556):
                                    if any(x in rurl for x in ("review", "comment", "rating", "merchant", "social")):
                                        xray_state["blocked"] += 1
                                    return
                                if rstatus != 200:
                                    return
                                if not any(kw in rurl for kw in
                                           ("product-review", "review-rating", "review-by-content",
                                            "/review/", "/comment", "rating-summary",
                                            "/social/", "websfxsocialreview", "/reviews",
                                            "discovery-web-socialgw")):
                                    return
                                ct = (resp.headers.get("content-type") or "").lower()
                                if "json" not in ct:
                                    return
                                try:
                                    body = resp.json()
                                except Exception:
                                    return
                                xray_state["intercepted"] += 1
                                _xray_walk(body, 0)
                                log.info(f"[X-Ray] 🔬 #{xray_state['intercepted']}: {resp.url[:120]} → revs={len(xray_state['reviews'])}")
                            except Exception:
                                pass

                        try:
                            page.on("response", _xray_handler)
                            log.info("[X-Ray] response listener mevcut page'e bağlandı.")
                        except Exception as listen_err:
                            log.info(f"[X-Ray] listener bağlanamadı: {listen_err}")

                        # ─── HOTFIX 1.36: Trendyol Yorum Agresif Loop ───────────────
                        # Eski tek-pass scroll (5×1500px) Trendyol'un lazy-loader'ı için
                        # yetersizdi → sadece ilk batch (3-6 yorum) yakalanıyordu.
                        # Yeni mantık:
                        #   1) /yorumlar URL'ine direkt git
                        #   2) Cookie/popup kapatıcı
                        #   3) "X Değerlendirme" header'ından beklenen toplam yorum sayısını oku
                        #   4) MAX_TURNS turlu döngü:
                        #       - kademeli human-scroll
                        #       - "Daha Fazla / Tüm Yorumları Gör / Devamını Gör"
                        #         butonlarını metin bazlı tıklat
                        #       - DOM'dan yorum container'larını topla (sıkı selector)
                        #   5) Yorum sayısı plato yapınca veya hedefe ulaşınca çık
                        #   6) Sıkı içerik filtresi (badge/UI label/kısa metin temizliği)
                        # ────────────────────────────────────────────────────────────
                        try:
                            yor_url = url.split("?")[0].rstrip("/")
                            if "/yorumlar" not in yor_url:
                                yor_url = yor_url + "/yorumlar"
                            try:
                                page.goto(yor_url, timeout=35000, wait_until="domcontentloaded")
                            except Exception as nav_err:
                                log.info(f"[Worker] /yorumlar navigation hata, link click'e düşülüyor: {nav_err}")
                                page.evaluate("""() => { var a = document.querySelector('a[href*="/yorumlar"]'); if (a) a.click(); }""")
                            try:
                                page.wait_for_load_state("networkidle", timeout=10000)
                            except Exception:
                                pass
                            page.wait_for_timeout(3000)  # HOTFIX 1.40: +%20 (2500→3000)

                            # Cookie / popup'ları temizle
                            try:
                                page.evaluate("""() => {
                                    document.querySelectorAll('button').forEach(b => {
                                        var t = (b.innerText || '').toLowerCase();
                                        if (t.includes('kabul') || t.includes('tamam')) {
                                            try { b.click(); } catch(e) {}
                                        }
                                    });
                                }""")
                            except Exception:
                                pass

                            # Beklenen toplam yorum sayısını sayfa header'ından oku
                            expected_total = 0
                            try:
                                expected_total = page.evaluate(r"""() => {
                                    var b = document.body.innerText || '';
                                    var m = b.match(/(\d{1,5})\s*(?:Değerlendirme|Yorum|değerlendirme|yorum)/);
                                    return m ? parseInt(m[1]) : 0;
                                }""") or 0
                                if expected_total:
                                    log.info(f"[Worker] Trendyol beklenen yorum: {expected_total}")
                            except Exception:
                                pass

                            # ── HOTFIX 1.37: Geniş selector + hedefli badge blacklist ──
                            # 1.36'daki "≥4 kelime + noktalama" filtresi kısa Türkçe yorumları
                            # ("Çok güzel", "Tavsiye ederim") da reddediyordu → 0 yorum.
                            # Şimdi geniş selector havuzu kullanıyoruz, sadece tam-eşleşen
                            # badge metinleri kara listeye alınıyor.
                            MAX_TURNS = 15
                            target_count = max(40, min(expected_total, 80)) if expected_total else 40
                            last_count = 0
                            stable_turns = 0

                            for turn in range(MAX_TURNS):
                                # Human-like scroll — 4 × 700px = 2800px / tur
                                try:
                                    for _ in range(4):
                                        page.evaluate("window.scrollBy(0, 700);")
                                        page.wait_for_timeout(540)  # HOTFIX 1.40: +%20 (450→540)
                                except Exception:
                                    pass

                                # Daha Fazla / Tüm Yorumları Gör butonu tıklat
                                try:
                                    page.evaluate(r"""() => {
                                        var keywords = ['daha fazla', 'tüm yorumları', 'tümünü gör',
                                                        'devamını gör', 'sonraki', 'load more', 'show more'];
                                        var els = document.querySelectorAll('button, a, span, div, li');
                                        for (var i = 0; i < els.length; i++) {
                                            var el = els[i];
                                            if (!el || el.offsetParent === null) continue;
                                            var t = (el.innerText || '').toLowerCase().trim();
                                            if (t.length === 0 || t.length > 60) continue;
                                            if (t.includes('yap') || t.includes('soru') ||
                                                t.includes('giriş') || t.includes('kayıt') ||
                                                t.includes('satıcıya')) continue;
                                            for (var k = 0; k < keywords.length; k++) {
                                                if (t.indexOf(keywords[k]) !== -1) {
                                                    try { el.scrollIntoView({block:'center'}); el.click(); return true; } catch(e){}
                                                }
                                            }
                                        }
                                        return false;
                                    }""")
                                except Exception:
                                    pass
                                page.wait_for_timeout(840)  # HOTFIX 1.40: +%20 (700→840)

                                # DOM'dan yorum çek — geniş selector havuzu + targeted blacklist
                                trendyol_revs = page.evaluate(r"""() => {
                                    // Hedefli kara liste — TAM eşleşmeli, lower-case
                                    var bannedExact = new Set([
                                        'kargo bedava', 'indirimli fiyat', 'indirim',
                                        'taksit', 'taksitli', 'taksit imkanı',
                                        'aynı gün kargo', 'hızlı kargo', 'ücretsiz kargo',
                                        'orijinal ürün', 'orjinal ürün',
                                        'iade kolaylığı', 'yeni ürün', 'kapıda ödeme',
                                        'değerlendirme yap', 'soru sor', 'satıcıya sor',
                                        'tüm yorumları gör', 'tüm değerlendirmeler',
                                        'fotoğraflı', 'fotoğraflı yorumlar',
                                        'satıcısından aldı', 'bildir', 'faydalı',
                                        'kabul et', 'hemen al', 'sepete ekle',
                                        'favorilere ekle', 'devamını oku', 'satıcı puanı',
                                        'satıcı', 'mağaza', 'marka', 'satıcısı',
                                        'değerlendir', 'değerlendirme', 'yorumla',
                                        'detaylı bilgi', 'fiyat', 'stok',
                                    ]);
                                    // Başlangıç eşleşmeli (substring)
                                    var bannedStarts = [
                                        'değerlendirme yap', 'satıcıya sor',
                                        'tüm yorumları', 'tüm değerlendirmeler',
                                        'fotoğraflı yorumlar', 'bu değerlendirme',
                                        'satıcı puanı', 'satıcı bilgileri',
                                    ];
                                    function isContamination(t) {
                                        var lt = t.toLowerCase().trim();
                                        if (lt.length < 8) return true;  // çok kısa
                                        if (bannedExact.has(lt)) return true;
                                        for (var i = 0; i < bannedStarts.length; i++) {
                                            if (lt.startsWith(bannedStarts[i])) return true;
                                        }
                                        // Yıldız/rating-only metinleri ele (ör. "★★★★★ 5.0")
                                        if (/^[\★\☆\s\d.,\/]+$/.test(t)) return true;
                                        // Tarih-only metinleri ele (ör. "5 gün önce", "2 hafta önce")
                                        if (/^\d+\s+(gün|hafta|ay|yıl|saat|dakika)\s+önce$/i.test(lt)) return true;
                                        // Sadece sayı/tarih/saat
                                        if (/^[\d.\/:\s-]+$/.test(t)) return true;
                                        return false;
                                    }
                                    var out = [];
                                    var seen = {};
                                    // Container selector'ları — alt elementten yorum metnini izole et
                                    var containerSels = [
                                        '[class*="ReviewItem"]', '[class*="ReviewCard"]',
                                        '[class*="CommentItem"]', '[class*="CommentCard"]',
                                        '.pr-rnr-cn',
                                    ];
                                    var textSels = [
                                        '.comment-text', '.rnr-com-tx', '.review-rnr-text',
                                        '.rnr-text', '[itemprop="reviewBody"]',
                                        '[data-test-id="review-text"]', '[data-testid="review-text"]',
                                        '[class*="CommentText"]', '[class*="commentText"]',
                                        '[class*="comment-text"]', '[class*="review-text"]',
                                        '[class*="reviewText"]', '[class*="ReviewText"]',
                                    ];

                                    function tryAdd(t) {
                                        t = (t || '').trim();
                                        if (t.length < 10 || t.length > 2000) return;
                                        if (seen[t]) return;
                                        if (isContamination(t)) return;
                                        seen[t] = 1;
                                        out.push(t);
                                    }

                                    // 1) Önce direkt text selector'ları — en spesifik, en güvenilir
                                    for (var i = 0; i < textSels.length; i++) {
                                        var els = document.querySelectorAll(textSels[i]);
                                        for (var j = 0; j < els.length; j++) {
                                            tryAdd(els[j].innerText);
                                        }
                                    }
                                    // 2) Container selector'ları — içlerinden yorum text'ini izole et
                                    for (var i = 0; i < containerSels.length; i++) {
                                        var cards = document.querySelectorAll(containerSels[i]);
                                        for (var j = 0; j < cards.length; j++) {
                                            var card = cards[j];
                                            // Card içinde ilgili text element'ini ara
                                            var found = false;
                                            for (var k = 0; k < textSels.length; k++) {
                                                var inner = card.querySelector(textSels[k]);
                                                if (inner) {
                                                    tryAdd(inner.innerText);
                                                    found = true;
                                                    break;
                                                }
                                            }
                                            // Bulamadıysa: card içindeki en uzun <p> veya <span>
                                            if (!found) {
                                                var ps = card.querySelectorAll('p, span');
                                                var best = '';
                                                for (var k = 0; k < ps.length; k++) {
                                                    var pt = (ps[k].innerText || '').trim();
                                                    if (pt.length > best.length && pt.length < 1500) {
                                                        best = pt;
                                                    }
                                                }
                                                if (best) tryAdd(best);
                                            }
                                        }
                                    }
                                    return out;
                                }""")
                                if isinstance(trendyol_revs, list):
                                    for r in trendyol_revs:
                                        if r and r.strip():
                                            raw_data_set.add(r.strip())

                                # Plato kontrolü: 5 turdur sayım artmıyorsa çık (3 → 5)
                                if len(raw_data_set) == last_count:
                                    stable_turns += 1
                                    if stable_turns >= 5:
                                        log.info(f"[Worker] Trendyol plato (tur {turn+1}, {len(raw_data_set)} yorum) — döngü sonlandırıldı.")
                                        break
                                else:
                                    stable_turns = 0
                                last_count = len(raw_data_set)

                                if len(raw_data_set) >= target_count:
                                    log.info(f"[Worker] Trendyol hedefe ulaşıldı: {len(raw_data_set)} ≥ {target_count}")
                                    break

                            log.info(f"[Worker] Trendyol DOM yorum (agresif): {len(raw_data_set)} (beklenen ≈ {expected_total or '?'})")
                        except Exception as ty_dom_err:
                            log.info(f"[Worker] Trendyol DOM yorum hata: {ty_dom_err}")

                        # ── HOTFIX 1.38: X-Ray network sonuçlarını birleştir ──
                        try:
                            page.remove_listener("response", _xray_handler)
                        except Exception:
                            pass
                        if xray_state["reviews"]:
                            before = len(raw_data_set)
                            for r in xray_state["reviews"]:
                                if r and r.strip():
                                    raw_data_set.add(r.strip())
                            log.info(f"[X-Ray] 🔄 DOM ↔ network: +{len(raw_data_set) - before} yorum "
                                  f"(intercept: {xray_state['intercepted']}, blocked: {xray_state['blocked']})")
                        if xray_state.get("seller") and (satici in (None, "Bulunamadı", "Marka Bulunamadı") or "Marka:" in str(satici)):
                            satici = xray_state["seller"]
                            log.info(f"[X-Ray] 🎯 seller: {satici}")
                        elif xray_state.get("brand") and satici in (None, "Bulunamadı", "Marka Bulunamadı"):
                            satici = f"Marka: {xray_state['brand']}"
                            log.info(f"[X-Ray] 🎯 brand fallback: {satici}")

                        # API güvenlik ağı: DOM hâlâ tamamen sıfırsa (DNS de patladığı için
                        # genelde işe yaramaz ama iz bırakmaması için sessiz dene)
                        if len(raw_data_set) == 0:
                            try:
                                api_revs = _fetch_trendyol_reviews_api(url, max_pages=3, target_count=60)
                                if api_revs:
                                    for r in api_revs:
                                        if r and r.strip():
                                            raw_data_set.add(r.strip())
                                    log.info(f"[Worker] Trendyol API güvenlik ağı: {len(raw_data_set)}")
                            except Exception as api_e:
                                log.info(f"[Worker] Trendyol API güvenlik ağı hata: {api_e}")
                        
                else:
                    raw_data_set = set()  # Reset if blocked

                # HB-specific: Use curl_cffi (Chrome TLS impersonation) to bypass Akamai directly
                if is_hepsiburada:
                    try:
                        cffi_data = _scrape_hepsiburada_cffi(url, fetch_reviews=True)
                        if cffi_data:
                            if cffi_data.get("name") and urun_ismi == "İsim Bulunamadı":
                                urun_ismi = cffi_data["name"]
                                durum = "OK"
                            # HOTFIX 11.0: HB satıcı ld+json offers.seller'dan geliyor.
                            # Eskiden cffi sonucu seller taşımıyordu → "Satıcı: Bulunamadı".
                            if cffi_data.get("seller") and satici in (None, "Bulunamadı", "Marka Bulunamadı"):
                                satici = cffi_data["seller"]
                            for rev_text in cffi_data.get("reviews", []):
                                raw_data_set.add(rev_text)
                            log.info(f"[Worker] HB cffi reviews — got {len(cffi_data.get('reviews', []))} reviews, seller={cffi_data.get('seller')!r}")
                    except Exception as cffi_e:
                        log.info(f"[Worker] HB cffi failed in review analysis: {cffi_e}")

                # Fallback to Cloudscraper — get name AND attempt to extract reviews from HTML
                if durum != "OK" or urun_ismi == "İsim Bulunamadı":
                    try:
                        import cloudscraper
                        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False})
                        resp = scraper.get(url, timeout=20)
                        if resp.status_code == 200:
                            cs_html = resp.text
                            cs_data = extract_data_from_html(cs_html, url)
                            if cs_data["name"] != "İsim Bulunamadı":
                                urun_ismi = cs_data["name"]
                                durum = "OK"

                            # For HB specifically: try to pull reviews from ld+json in the HTML
                            if is_hepsiburada and not raw_data_set:
                                from bs4 import BeautifulSoup as _BS
                                _soup = _BS(cs_html, 'lxml')
                                for _script in _soup.find_all('script', type='application/ld+json'):
                                    try:
                                        _ld = json.loads(_script.string or '')
                                        _items = _ld if isinstance(_ld, list) else _ld.get('@graph', [_ld])
                                        for _item in _items:
                                            if _item.get('@type') == 'Review':
                                                body = _item.get('reviewBody', '')
                                                if body and len(body) > 15:
                                                    raw_data_set.add(body)
                                            if _item.get('@type') == 'Product':
                                                revs = _item.get('review', [])
                                                if isinstance(revs, list):
                                                    for _r in revs:
                                                        body = _r.get('reviewBody', '')
                                                        if body and len(body) > 15:
                                                            raw_data_set.add(body)
                                    except Exception:
                                        pass

                        # Final HB fallback for name via API if still missing
                        if is_hepsiburada and urun_ismi == "İsim Bulunamadı":
                            hb_api = _hepsiburada_api_fallback(url)
                            if hb_api and hb_api.get('name'):
                                urun_ismi = hb_api['name']
                                durum = "OK"
                    except Exception as cs_e:
                        log.info(f"[Worker] Cloudscraper fallback failed in review analysis: {cs_e}")

                # Rebuild raw_data from raw_data_set AFTER all fallbacks
                raw_data = list(raw_data_set)

                if raw_data:
                    negWords = ['kırık', 'kötü', 'iade', 'eksik', 'defolu', 'yırtık', 'koptu', 'kalitesiz', 'çöp',
                                'berbat', 'maalesef', 'sorun', 'sıkıntı', 'tavsiye', 'farklı', 'zarar', 'plastik',
                                'yamuk', 'bozuldu', 'çizik']
                    bad = [r for r in raw_data if any(w in r.lower() for w in negWords)]
                    good = [r for r in raw_data if r not in bad]
                    final_bad = bad[:20]
                    final_good = good[:40 - len(final_bad)]
                    sayfa_metinleri = final_bad + final_good
                    olumsuz = len(final_bad)
                    olumlu = len(final_good)
                    incelenen = len(sayfa_metinleri)

                    # HOTFIX 1.13: url parametresini de geç → AI platform-bağlamlı analiz yapsın.
                    ai_data = analyze_reviews_with_ai(sayfa_metinleri, api_key, url=url)
                    if not ai_data:
                        yorum_ozeti = "<div style='color:var(--muted);text-align:center;padding:30px;'>📭 Yorum Analizi Yapılamadı (AI Yanıtı Boş)</div>"
                    elif "error" in ai_data:
                        yorum_ozeti = f"<div style='color:var(--danger);text-align:center;padding:30px;line-height:1.6;'>⚠️ <b>Analiz Başarısız:</b><br>{ai_data['error']}</div>"
                    else:
                        total_sent = olumlu + olumsuz
                        olumlu_pct = (olumlu / total_sent * 100) if total_sent else 0
                        olumsuz_pct = (olumsuz / total_sent * 100) if total_sent else 0

                        # HOTFIX 1.14 / 1.37: tüm renderlar merkezi helper'lar + tema değişkenleri
                        prog = (
                            f"<div style=\"margin-top:35px;\"><div style=\"display:flex;justify-content:space-between;font-size:13px;font-weight:800;margin-bottom:12px;letter-spacing:0.5px;\">"
                            f"<span style=\"color:var(--success);\">{olumlu} OLUMLU</span><span style=\"color:var(--danger);\">{olumsuz} OLUMSUZ</span></div>"
                            f"<div style=\"width:100%;height:16px;background:var(--bg-strong);border-radius:99px;display:flex;overflow:hidden;\">"
                            f"<div style=\"background:var(--success);width:{olumlu_pct}%;transition:width 1s ease;opacity:0.85;\"></div>"
                            f"<div style=\"background:var(--danger);width:{olumsuz_pct}%;transition:width 1s ease;opacity:0.85;\"></div></div></div>"
                        )
                        good_tags = _build_review_tags(ai_data.get('basarili', []), 'var(--success)')
                        bad_tags = _build_review_tags(ai_data.get('sikayet', []), 'var(--danger)')
                        genel_text = _stringify_ai_field(ai_data.get('genel', ''))
                        yorum_ozeti = (
                            "<div style='display:grid;grid-template-columns:repeat(auto-fit, minmax(280px, 1fr));gap:30px;margin-bottom:24px;'>"
                            "<div><div style='color:var(--success);font-size:15px;font-weight:700;margin-bottom:16px;letter-spacing:0.5px;'>BAŞARILI YÖNLER</div>"
                            f"{good_tags}</div>"
                            "<div><div style='color:var(--danger);font-size:15px;font-weight:700;margin-bottom:16px;letter-spacing:0.5px;'>KRİTİK ŞİKAYETLER</div>"
                            f"{bad_tags}</div></div>"
                            f"<div style='padding-top:20px;border-top:1px solid var(--border);color:var(--text);font-size:14px;line-height:1.7;'>"
                            f"<b style='color:var(--info);'>GENEL KANI:</b> {genel_text}</div>{prog}"
                        )
                elif durum != "OK":
                    yorum_ozeti = "<div style='color:var(--danger);padding:20px;text-align:center;'><b>⛔ Güvenlik Duvarı Engeli</b></div>"
                else:
                    yorum_ozeti = "<div style='color:var(--muted);text-align:center;padding:30px;'>📭 İşe Yarar Yorum Bulunamadı</div>"

                # ── HOTFIX 1.35: Trendyol mağaza puanı ──
                seller_rating = None
                if is_trendyol:
                    try:
                        mid = _extract_merchant_id_from_url(url)
                        if mid:
                            score, name = _fetch_trendyol_seller_rating(mid)
                            if score is not None:
                                seller_rating = score
                            if name and (not satici or satici in ("Bulunamadı", "Marka Bulunamadı")):
                                satici = name
                    except Exception as sr_e:
                        log.info(f"[Worker] seller_rating fetch hata: {sr_e}")

                logo_url = f"https://icon.horse/icon/{domain}" if domain else ""
                sonuclar.append({
                    "Platform": platform_name, "Logo": logo_url, "Satici": satici,
                    "SellerRating": seller_rating,
                    "UrunAdi": urun_ismi, "URL": url, "Analiz": yorum_ozeti, "Sayi": incelenen
                })
            except Exception as e:
                sonuclar.append({
                    "Platform": platform_name, "Logo": "", "Satici": "Hata",
                    "SellerRating": None,
                    "UrunAdi": "Hata", "URL": url, "Analiz": f"⚠️ Hata: {str(e)}", "Sayi": 0
                })

    # Build HTML
    cards = ""
    for s in sonuclar:
        logo = f"<img src='{s['Logo']}' style='width:24px;height:24px;border-radius:6px;' onerror='this.style.display=\"none\"'>" if s['Logo'] else ""
        # HOTFIX 1.35: Satıcı puan badge'i — yeşil/sarı/kırmızı rengi puanına göre
        sr = s.get('SellerRating')
        sr_badge = ""
        if isinstance(sr, (int, float)) and sr > 0:
            if sr >= 9.0:
                bg, fg = "var(--success-bg)", "var(--success)"   # yeşil — CSS var
            elif sr >= 7.5:
                bg, fg = "var(--warn-bg)", "var(--warn)"           # sarı — CSS var
            else:
                bg, fg = "var(--danger-bg)", "var(--danger)"       # kırmızı — CSS var
            sr_badge = (
                f"<span style='display:inline-flex;align-items:center;gap:4px;"
                f"background:{bg};color:{fg};padding:2px 8px;border-radius:8px;"
                f"font-size:11px;font-weight:700;margin-left:6px;'>⭐ {sr:.1f}</span>"
            )
        cards += f"""
        <div style="border-bottom:1px solid var(--border);padding:40px 0;margin-bottom:10px;">
            <div style="display:grid;grid-template-columns:250px 1fr;gap:40px;">
                <div style="border-right:1px solid var(--border);padding-right:30px;">
                    <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">{logo}<span style="font-size:18px;font-weight:700;color:var(--text);">{s['Platform']}</span></div>
                    <div style="font-size:12px;color:var(--muted);margin-bottom:12px;">Satıcı: <span style="color:var(--text-soft);">{s['Satici']}</span>{sr_badge}</div>
                    <div style="font-size:13px;color:var(--text-soft);margin-bottom:20px;line-height:1.5;">{str(s['UrunAdi'])[:80]}...</div>
                    <div style="color:var(--title);font-size:13px;font-weight:700;margin-bottom:16px;">{s['Sayi']} Yorum İncelendi</div>
                    <a href='{s['URL']}' target='_blank' style='color:var(--text-soft);text-decoration:none;font-size:13px;font-weight:600;transition:color 0.2s;' onmouseover='this.style.color="var(--text)"' onmouseout='this.style.color="var(--text-soft)"'>Ürüne Git ↗</a>
                </div>
                <div style="font-size:14px;color:var(--text);line-height:1.6;">{s['Analiz']}</div>
            </div>
        </div>"""

    theme_html = _report_theme_block()
    return f"""<html><head><meta charset="utf-8"><link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;700;800&display=swap" rel="stylesheet">{theme_html}<style>.container{{max-width:1100px;}}</style></head>
    <body><div class="container"><div style="color:var(--title);font-weight:bold;letter-spacing:2px;font-size:13px;margin-bottom:5px;">BMK VERİ ODAKLI DANIŞMANLIK HİZMETİ</div>
    <h1 style="margin:0 0 30px 0;font-size:36px;">Ürün İtibar Analiz Raporu</h1>{cards}
    <div style="text-align:center;margin-top:40px;color:var(--report-color);font-size:14px;">Rapor Tarihi: {get_tr_now().strftime('%d.%m.%Y %H:%M')}</div></div></body></html>"""


def run_combined_headless(urls, api_key):
    """Run combined price + review analysis and return HTML report string."""
    from models import get_tr_now
    from datetime import datetime as dt

    price_html = run_price_headless(urls, api_key)
    review_html = run_review_headless(urls, api_key)

    # Combine into a single report
    theme_html = _report_theme_block()
    return f"""<html><head><meta charset="utf-8"><link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;700;800&display=swap" rel="stylesheet">{theme_html}<style>.container{{max-width:1100px;}}.section-divider{{border:none;border-top:1px solid var(--border);margin:40px 0;}}</style></head>
    <body><div class="container">
        <div style="color:var(--title);font-weight:bold;letter-spacing:2px;font-size:13px;margin-bottom:5px;">BMK VERİ ODAKLI DANIŞMANLIK HİZMETİ</div>
        <h1 style="margin:0 0 30px 0;font-size:36px;">Kombine Analiz Raporu</h1>
        <h2 style="color:var(--title);font-size:24px;margin:30px 0 20px 0;">💰 Fiyat Karşılaştırma</h2>
        {_extract_body_content(price_html)}
        <hr class="section-divider">
        <h2 style="color:var(--title);font-size:24px;margin:30px 0 20px 0;">🗣️ Yorum Analizi</h2>
        {_extract_body_content(review_html)}
        <div style="text-align:center;margin-top:40px;color:var(--report-color);font-size:14px;">Rapor Tarihi: {get_tr_now().strftime('%d.%m.%Y %H:%M')}</div>
    </div></body></html>"""


def _extract_body_content(html_str):
    """Extract content between container div tags, stripping header/footer."""
    import re
    # Find content after the h1 tag and before the closing date div
    match = re.search(r'</h1>(.*?)<div style="text-align:center', html_str, re.DOTALL)
    if match:
        return match.group(1)
    return html_str

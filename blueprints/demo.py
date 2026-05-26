"""
blueprints/demo.py — Public demo sayfası.

/demo — signup gerektirmeden tam panelin nasıl çalıştığını gösterir.
Tüm veriler MOCK (Python dict). DB'ye dokunmaz.

Amaç:
    Yeni ziyaretçi → /demo → 4 bölümlü interaktif önizleme → "Beta'ya katıl" CTA.
    Conversion'da en güçlü silah; landing hero'sundaki "Demoyu İncele" buradan açılır.
"""
from flask import Blueprint, render_template

bp = Blueprint('demo', __name__)


# ─────────────────────────────────────────────────────────────────────────────
# MOCK DATA — production'da değişmez; sadece okuma amaçlı.
# Bu veriler kasıtlı olarak "gerçek ürünleri" temsil eder ama hiçbiri DB'de
# yok. Tüm fiyat ve sıralama rakamları örnek/açıklayıcıdır.
# ─────────────────────────────────────────────────────────────────────────────

# Fiyat takip ürünleri (3 ürün — base + 2 rakip) + 7 günlük seriler
MOCK_PRICE_GROUP = {
    'name': 'Kategori: Şarjlı Tüy Toplayıcı',
    'products': [
        {
            'name': '👑 Sizin Ürününüz',
            'platform': 'Trendyol',
            'current': 449.90,
            'previous': 469.90,
            'change_pct': -4.3,
            'cost': 285.00,
            'profit': 164.90,
            'series': [469, 469, 459, 455, 449, 449, 449.90],
        },
        {
            'name': '📉 Rakip — Marka A',
            'platform': 'Trendyol',
            'current': 429.00,
            'previous': 449.00,
            'change_pct': -4.5,
            'series': [449, 449, 439, 439, 429, 429, 429],
        },
        {
            'name': '📈 Rakip — Marka B',
            'platform': 'Hepsiburada',
            'current': 489.00,
            'previous': 479.00,
            'change_pct': 2.1,
            'series': [479, 479, 479, 489, 489, 489, 489],
        },
    ],
    'days': ['19 May', '20 May', '21 May', '22 May', '23 May', '24 May', '25 May'],
}

# SEO sıralama
MOCK_SEO = {
    'keyword': 'şarjlı tüy toplayıcı',
    'current_rank': 7,   # 1. sayfada 7. sıra
    'previous_rank': 14,
    'history': [27, 22, 18, 14, 11, 9, 7],  # son 7 gün overall_rank (küçük = iyi)
}

# YZ Strateji raporu (markdown-benzeri kısaltılmış)
MOCK_AI_REPORT = {
    'created_at': '24 May 2026',
    'sector': 'Pet Bakım ve Aksesuar',
    'sections': [
        {
            'icon': '🌍',
            'title': '1. Pazar ve Niş Değerlendirmesi',
            'body': (
                'Trendyol ve Hepsiburada\'da <strong>şarjlı tüy toplayıcı</strong> kategorisinde '
                'üst-orta segmentte konumlanıyorsunuz. Pazar Q1 2026\'da '
                '<strong>%23 büyüme</strong> gösterdi; kullanıcı sorgu hacmi mevsimsel pik dönemde.'
            ),
        },
        {
            'icon': '⚖️',
            'title': '2. Rekabet ve Ürün Analizi',
            'body': (
                'Rakibinizdeki <strong>"şarj çabuk bitiyor"</strong> şikayetini '
                'bir <strong>fırsata çevirin</strong> — ürün başlığınızda "uzun pil ömrü" vurgusu '
                'tıklamayı %12-18 artırır. Yorum puanınız <strong>4.7★</strong>, min rakipten 0.4 yüksek.'
            ),
        },
        {
            'icon': '💰',
            'title': '3. Fiyatlandırma Stratejisi',
            'body': (
                '<strong>🚨 KURAL — GÖRÜNMEZLİK TUZAĞI:</strong> Fiyatınız (<strong>449,90 ₺</strong>) '
                'min rakipten <strong>+20 ₺</strong> yüksek ama SEO sıranız <strong>7. sıra</strong> '
                '— iyi konumdasınız. <strong>Fiyatı düşürmeyin.</strong> Yerine PPM reklamı + başlık optimizasyonu.'
            ),
        },
        {
            'icon': '🔍',
            'title': '4. Görünürlük (SEO) Stratejisi',
            'body': (
                'Önerilen long-tail kelimeler: <em>"şarjlı kedi tüy toplayıcı USB",'
                ' "düşük ses tüy toplayıcı evcil",</em> '
                '<em>"şarj edilebilir hayvan tüyü süpürgesi"</em>. '
                'Bu 3 kelime aylık aramada ~12.000 hacim taşıyor, rekabet düşük.'
            ),
        },
    ],
}

# Bildirim merkezi — son 5 örnek bildirim
MOCK_NOTIFICATIONS = [
    {
        'icon': '🟢',
        'category': 'opportunity',
        'message': 'Rakip A fiyatı %4,5 düştü (449 → 429 ₺). Buy Box riski.',
        'time': '2 dk önce',
    },
    {
        'icon': '📉',
        'category': 'price_down',
        'message': 'Ürününüz "Şarjlı Tüy Toplayıcı" fiyatı 469 → 449,90 ₺ (-4,1%).',
        'time': '12 dk önce',
    },
    {
        'icon': '🔍',
        'category': 'seo',
        'message': 'Arama sıranız 14 → 7. sıraya yükseldi ("şarjlı tüy toplayıcı").',
        'time': '1 saat önce',
    },
    {
        'icon': '🧠',
        'category': 'system',
        'message': 'Yeni YZ Strateji raporu hazır — Pazar analizi + 4 aksiyon önerisi.',
        'time': '3 saat önce',
    },
    {
        'icon': '⚠️',
        'category': 'threat',
        'message': 'Rakip B fiyatı %2,1 yükseldi — pazar genelinde fiyat dengelenmesi sinyali.',
        'time': 'Dün',
    },
]


@bp.route('/demo')
def demo():
    """Public demo — auth gerektirmez. Mock data ile tam panel önizlemesi."""
    return render_template(
        'demo.html',
        price_group=MOCK_PRICE_GROUP,
        seo=MOCK_SEO,
        ai_report=MOCK_AI_REPORT,
        notifications=MOCK_NOTIFICATIONS,
    )

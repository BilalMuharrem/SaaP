"""
services/notifications/classifier.py — Bildirim kategori sınıflandırıcı.

HOTFIX 1.54: Rule-First + AI Fallback
  • classify_notification_rule(msg)  → regex/keyword; None ise AI çağrılır
  • classify_notification_ai(msg)    → Groq AI; exception olmaz, 'system' fallback
  • classify_notification(msg, prefer_ai=False) → üst seviye public API

Kategoriler:
    price_down, price_up, combined, opportunity, threat, system, seo
"""
import logging

from services.ai.groq import resolve_groq_key

log = logging.getLogger(__name__)

NOTIFICATION_CATEGORIES = ('price_down', 'price_up', 'combined', 'opportunity',
                          'threat', 'system', 'seo')


def classify_notification_rule(message):
    """Mesaj metnine bakarak kategori tahmini. None döndürürse AI çağrılır."""
    if not message or not isinstance(message, str):
        return 'system'
    m = message  # case-sensitive — emoji ve büyük harfli sinyaller hassas

    # 1) Fırsat sinyalleri (önce kontrol — "🚨 Fırsat" "fiyatı düştü" de içerebilir)
    if ('KRİTİK FIRSAT' in m or 'BÜYÜK FIRSAT' in m
            or m.startswith('🚨 Fırsat') or '🚨 Fırsat!' in m
            or '🎯' in m or '🏆' in m):
        return 'opportunity'

    # 2) Tehdit sinyalleri
    if ('TEHDİT' in m or 'KRİTİK FİYAT DEĞİŞİMİ' in m
            or m.startswith('⚠️') or '⚡ KRİTİK' in m):
        return 'threat'

    # HOTFIX 1.99: SEO sıralama değişimi (fiyat sinyallerinden ÖNCE)
    if ('SEO Sıralaması' in m or 'aramasında Sayfa' in m
            or '🏆' in m and 'Sıra' in m):
        return 'seo'

    # 3) Fiyat yön bildirimleri
    if '📉' in m or ('fiyatı' in m and 'düştü' in m) or 'Fiyat Değişti' in m and 'düştü' in m.lower():
        return 'price_down'
    if '📈' in m or ('fiyatı' in m and ('arttı' in m or 'üstüne çıktı' in m)) or 'Fiyat Değişti' in m and 'arttı' in m.lower():
        return 'price_up'

    # 4) Kombine analiz tamamlandı/başarısız
    if ("'Kombine Analiz'" in m or "'Fiyat Analizi'" in m
            or "'Yorum Analizi'" in m or 'işleminiz başarıyla' in m
            or 'işleminiz başarısız' in m):
        return 'combined'

    # 5) Sistem mesajı — diğer her şey
    if ('Alarm kuruldu' in m or 'sistem' in m.lower() or 'bakım' in m.lower()):
        return 'system'

    return None


def classify_notification_ai(message, api_key=None):
    """Groq AI ile sınıflandırma. Sadece rule None döndürünce çağrılır."""
    if not message:
        return 'system'
    key = api_key or resolve_groq_key()
    if not key:
        return 'system'

    try:
        from groq import Groq
        client = Groq(api_key=key)
        prompt = (
            f"Aşağıdaki bildirim mesajını TEK kategoride sınıflandır. "
            f"Sadece kategori adını döndür (başka metin YOK). "
            f"Olası kategoriler: price_down, price_up, combined, "
            f"opportunity, threat, system.\n\n"
            f"Mesaj: {message[:300]}"
        )
        resp = client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.0,
            max_tokens=10,
        )
        out = (resp.choices[0].message.content or '').strip().lower()
        for cat in NOTIFICATION_CATEGORIES:
            if cat in out:
                return cat
        return 'system'
    except Exception:
        log.exception("[NotificationClassifier] AI fallback hatası")
        return 'system'


def classify_notification(message, api_key=None, prefer_ai=False):
    """Üst seviye sınıflandırıcı.
      - prefer_ai=False (default): önce kural, başarısızsa AI
      - prefer_ai=True: doğrudan AI'a sor (manuel debug için)
    """
    if not prefer_ai:
        cat = classify_notification_rule(message)
        if cat is not None:
            return cat
    return classify_notification_ai(message, api_key=api_key)

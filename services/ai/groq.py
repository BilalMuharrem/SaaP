"""
services/ai/groq.py — Groq API key fallback chain.

FAZ 10A güncellemesi: GROQ key artık DB'de saklanmaz (güvenlik).
Sadece env tabanlı. Eski Setting tablosu okuması kaldırıldı.

Öncelik sırası:
  1) User/Job'ın kendi anahtarı (parametre — bireysel kullanıcı kendi key'ini
     girerse o kullanılır; özellik şu an UI'da kapalı, ileri kullanım için)
  2) Config.GROQ_API_KEY (.env'den okunur)
  3) Doğrudan os.environ.get('GROQ_API_KEY') — Config çoktan import edilmişse
     env değişikliği yansımaz, bu nedenle son çare olarak doğrudan okuma

NOT: Setting tablosundan okuma kaldırıldı (Faz 10A). DB dump sızarsa key'in de
sızmaması için. Tek doğru kaynak .env veya production'da systemd env.
"""
import os


def _ok(k):
    return bool(k) and len(str(k).strip()) >= 15


def resolve_groq_key(user_key=None):
    """User → Config → env fallback chain. Yoksa '' döner."""
    if _ok(user_key):
        return str(user_key).strip()

    try:
        from config import Config
        cfg_key = getattr(Config, 'GROQ_API_KEY', '') or ''
        if _ok(cfg_key):
            return cfg_key.strip()
    except Exception:
        pass

    sys_key = (os.environ.get('GROQ_API_KEY', '') or '').strip()
    return sys_key if _ok(sys_key) else ''

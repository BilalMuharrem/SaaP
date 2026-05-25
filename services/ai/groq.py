"""
services/ai/groq.py — Groq API key fallback chain.

HOTFIX 1.25: Öncelik sırası
  1) User/Job'ın kendi anahtarı (parametre)
  2) Setting tablosundaki sistem ayarı (admin panel: groq_api_key)
  3) Config.GROQ_API_KEY (.env)
  4) Direkt os.environ.get('GROQ_API_KEY')

Yeni kayıt olan kullanıcılar kendi key'ini girene kadar admin'inkini kullanır →
"API Anahtarı eksik" hatası fırlamaz.
"""
import os


def _ok(k):
    return bool(k) and len(str(k).strip()) >= 15


def resolve_groq_key(user_key=None):
    """User → Setting → Config → env fallback chain. Yoksa '' döner."""
    if _ok(user_key):
        return str(user_key).strip()

    try:
        from models import Setting
        sk = Setting.get('groq_api_key', '') or ''
        if _ok(sk):
            return sk.strip()
    except Exception:
        pass

    try:
        from config import Config
        cfg_key = getattr(Config, 'GROQ_API_KEY', '') or ''
        if _ok(cfg_key):
            return cfg_key.strip()
    except Exception:
        pass

    sys_key = (os.environ.get('GROQ_API_KEY', '') or '').strip()
    return sys_key if _ok(sys_key) else ''

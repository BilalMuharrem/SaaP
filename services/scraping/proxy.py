"""
services/scraping/proxy.py — Residential proxy yapılandırma yardımcıları.

HOTFIX 1.32: .env'de PROXY_URL=http://user:pass@host:port tanımlandığında
HEM cloudscraper HEM Playwright kullanır. Tanımlı değilse direkt bağlantı.
"""
import logging
import os
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def get_proxy_url():
    """Returns raw proxy URL or '' if none configured."""
    return (os.environ.get("PROXY_URL", "") or "").strip()


def get_proxy_for_requests():
    """cloudscraper/requests için {'http': url, 'https': url} dict — yoksa None."""
    p = get_proxy_url()
    if not p:
        return None
    return {"http": p, "https": p}


def get_proxy_for_playwright():
    """Playwright launch(proxy=...) için dict — yoksa None.

    Format: {'server': 'http://host:port', 'username': '...', 'password': '...'}
    URL içinde user:pass varsa parse edilip ayrı alanlara yerleştirilir.
    (Playwright credential'ı URL'de kabul etmez.)
    """
    p = get_proxy_url()
    if not p:
        return None
    try:
        u = urlparse(p)
        netloc = u.hostname or ""
        if u.port:
            netloc = f"{netloc}:{u.port}"
        scheme = u.scheme or "http"
        cfg = {"server": f"{scheme}://{netloc}"}
        if u.username:
            cfg["username"] = u.username
        if u.password:
            cfg["password"] = u.password
        return cfg
    except Exception:
        log.exception("[Proxy] PROXY_URL parse hatası — bağlanılmayacak")
        return None

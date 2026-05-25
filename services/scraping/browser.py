"""
services/scraping/browser.py — Tarayıcı/HTTP istemci profil yardımcıları.

İçindekiler:
  UA_PROFILES, UA_POOL           — Sec-Ch-Ua uyumlu rotating UA havuzu
  rand_profile() / rand_ua()     — Tutarlı UA + Sec-Ch-Ua profili veya düz UA
  build_browser_headers()        — Modern Chromium header seti (Sec-Fetch-*, vb.)

HOTFIX 1.32: UA + Sec-Ch-Ua eşlemeli profiller. Her UA kendi platform/mobile
imzasıyla gelir; Trendyol gibi servisler UA-platform tutarsızlığını bot işareti
sayar. Cloudscraper + curl_cffi + Playwright extra_http_headers aynı havuzdan beslenir.
"""
import random


UA_PROFILES = [
    # Desktop Chrome — Windows
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "platform": '"Windows"', "mobile": "?0",
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
        "platform": '"Windows"', "mobile": "?0",
    },
    # Desktop Edge — Windows
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
        "sec_ch_ua": '"Microsoft Edge";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "platform": '"Windows"', "mobile": "?0",
    },
    # Desktop Firefox — Windows (Sec-Ch-Ua göndermez)
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
        "sec_ch_ua": None, "platform": None, "mobile": None,
    },
    # Desktop Chrome — macOS
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "platform": '"macOS"', "mobile": "?0",
    },
    # Desktop Safari — macOS
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "sec_ch_ua": None, "platform": None, "mobile": None,
    },
    # Desktop Chrome — Linux
    {
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
        "platform": '"Linux"', "mobile": "?0",
    },
    # Mobile Safari — iOS
    {
        "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
        "sec_ch_ua": None, "platform": None, "mobile": None,
    },
    # Mobile Chrome — Android
    {
        "ua": "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
        "platform": '"Android"', "mobile": "?1",
    },
    {
        "ua": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "platform": '"Android"', "mobile": "?1",
    },
]

# Geriye dönük uyumluluk
UA_POOL = [p["ua"] for p in UA_PROFILES]


def rand_profile():
    """Tutarlı bir UA + Sec-Ch-Ua profili döndürür."""
    return random.choice(UA_PROFILES)


def rand_ua():
    """Geriye uyum: sadece UA string'i."""
    return random.choice(UA_POOL)


def build_browser_headers(profile=None, referer="https://www.trendyol.com/"):
    """UA ile uyumlu modern Chromium header seti.

    Returns: (headers_dict, profile_dict) çifti.
    """
    p = profile or rand_profile()
    accept_langs = [
        "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "tr-TR,tr;q=0.9,en;q=0.6",
        "tr,en-US;q=0.9,en;q=0.8",
    ]
    h = {
        "User-Agent": p["ua"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": random.choice(accept_langs),
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Referer": referer,
    }
    if p.get("sec_ch_ua"):
        h["Sec-Ch-Ua"] = p["sec_ch_ua"]
        h["Sec-Ch-Ua-Mobile"] = p["mobile"]
        h["Sec-Ch-Ua-Platform"] = p["platform"]
    return h, p

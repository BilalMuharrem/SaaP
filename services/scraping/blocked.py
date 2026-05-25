"""
services/scraping/blocked.py — Bot algılama yardımcıları.
"""

_BLOCKER_PHRASES = (
    "captcha", "robot musunuz", "access denied",
    "are you a robot", "cf-challenge", "cf-browser-verification",
    "px-captcha", "perimeterx",
)


def is_blocked_response(resp):
    """HTTP response bot-block / rate-limit / CAPTCHA içeriyor mu?"""
    if resp is None:
        return True
    code = getattr(resp, "status_code", 0)
    if code in (403, 429, 503):
        return True
    text = (getattr(resp, "text", "") or "")[:8000].lower()
    return any(b in text for b in _BLOCKER_PHRASES)

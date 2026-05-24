"""
utils/filters.py — Jinja template filter'ları.

Flask app'e bağlama register_filters(app) ile yapılır.
"""
from models import get_tr_now


_TR_MONTHS = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz',
              'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara']


def turkdate(dt):
    """`{{ value|turkdate }}` → 25 May 2026 14:30"""
    if not dt:
        return '-'
    return f"{dt.day} {_TR_MONTHS[dt.month - 1]} {dt.year} {dt.strftime('%H:%M')}"


def timeago(dt):
    """`{{ value|timeago }}` → "5 dk önce", "Dün", "3 gün önce", veya 7+ gün için tam tarih."""
    if not dt:
        return '-'
    now = get_tr_now()
    delta = now - dt
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return 'Az önce'
    if delta.days >= 7:
        return turkdate(dt)
    if delta.days >= 2:
        return f'{delta.days} gün önce'
    if delta.days == 1:
        return 'Dün'
    if total_seconds >= 3600:
        return f'{total_seconds // 3600} saat önce'
    if total_seconds >= 60:
        return f'{total_seconds // 60} dk önce'
    return 'Az önce'


def register_filters(app):
    """create_app() içinden çağrılır — filter'ları Jinja env'ine kayıt eder."""
    app.add_template_filter(turkdate, name='turkdate')
    app.add_template_filter(timeago, name='timeago')

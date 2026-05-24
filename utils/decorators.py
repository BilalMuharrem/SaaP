"""
utils/decorators.py — Route dekoratörleri.
"""
from functools import wraps
from flask import abort
from flask_login import current_user


def admin_required(f):
    """Sadece is_admin=True kullanıcılar geçebilir. Aksi 403."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

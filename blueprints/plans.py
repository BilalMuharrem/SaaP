"""
blueprints/plans.py — Kullanıcı plan vitrin sayfası.

Rotalar:
    GET /plans   — admin ise admin.admin_plans'e yönlendirir.
"""
from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user

from models import Plan

bp = Blueprint('plans', __name__)


@bp.route('/plans')
@login_required
def user_plans():
    if current_user.is_admin:
        return redirect(url_for('admin.admin_plans'))
    plans = Plan.query.filter_by(is_active=True).order_by(Plan.sort_order).all()
    return render_template('user_plans.html', plans=plans)

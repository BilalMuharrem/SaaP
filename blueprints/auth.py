"""
blueprints/auth.py — Giriş/kayıt/çıkış + landing.

Rotalar:
    GET  /            → index (landing)
    GET/POST /login
    GET/POST /register
    GET  /logout
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db, limiter
from models import User, UsageLog, Plan, Setting, get_tr_now

bp = Blueprint('auth', __name__)


@bp.route('/')
def index():
    # HOTFIX 2.00: Authenticated kullanıcı da landing'i görebilir.
    return render_template('landing.html')


@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute;30 per hour", methods=['POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if not user.is_approved and not user.is_admin:
                flash('Hesabınız henüz onaylanmadı. Lütfen yönetici onayını bekleyin.', 'warning')
                return render_template('login.html')
            if not user.is_active and not user.is_admin:
                flash('Hesabınız devre dışı bırakılmış. Lütfen yöneticiyle iletişime geçin.', 'error')
                return render_template('login.html')

            user.last_login = get_tr_now()
            db.session.add(UsageLog(user_id=user.id, action='login'))
            db.session.commit()

            login_user(user, remember=True)

            if user.is_admin:
                return redirect(url_for('admin.admin_dashboard'))
            # FAZ 5A: İlk girişte onboarding wizard'a yönlendir
            if not user.onboarding_completed:
                return redirect(url_for('onboarding.start'))
            return redirect(url_for('dashboard.dashboard'))
        else:
            flash('Geçersiz e-posta veya şifre.', 'error')

    return render_template('login.html')


@bp.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per hour;20 per day", methods=['POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        full_name = request.form.get('full_name', '').strip()
        company = request.form.get('company', '').strip()
        phone = request.form.get('phone', '').strip()

        errors = []
        if not email or '@' not in email:
            errors.append('Geçerli bir e-posta adresi girin.')
        if len(password) < 6:
            errors.append('Şifre en az 6 karakter olmalıdır.')
        if password != confirm:
            errors.append('Şifreler eşleşmiyor.')
        if not full_name:
            errors.append('Ad Soyad zorunludur.')
        if User.query.filter_by(email=email).first():
            errors.append('Bu e-posta adresi zaten kayıtlı.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('register.html')

        approval_mode = Setting.get('approval_mode', 'manual')
        auto_approve = (approval_mode == 'auto')

        trial_plan = Plan.query.filter_by(name='trial').first()
        trial_days = int(Setting.get('free_trial_days', '14'))

        user = User(
            email=email,
            full_name=full_name,
            company=company,
            phone=phone,
            is_active=auto_approve,
            is_approved=auto_approve,
            plan_id=trial_plan.id if trial_plan else None,
            trial_start=get_tr_now(),
            trial_days=trial_days,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        if auto_approve:
            flash('Kayıt başarılı! Giriş yapabilirsiniz.', 'success')
        else:
            flash('Kayıt başarılı! Hesabınız yönetici onayı bekliyor.', 'info')

        return redirect(url_for('auth.login'))

    return render_template('register.html')


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Başarıyla çıkış yaptınız.', 'info')
    return redirect(url_for('auth.login'))

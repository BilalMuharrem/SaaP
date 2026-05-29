#!/usr/bin/env python3
"""
scripts/create_admin.py — İnteraktif admin kullanıcı oluşturma.

KULLANIM:
    python scripts/create_admin.py              # interaktif: email + şifre sorar
    ADMIN_EMAIL=x@y.com ADMIN_PASSWORD=... python scripts/create_admin.py  # env'den

DAVRANIŞ:
    1) ADMIN_EMAIL ve ADMIN_PASSWORD env varsa: oradan oku
    2) Yoksa: terminalden interaktif sor (şifre gizli, getpass ile)
    3) Aynı email'le admin varsa: hata ver (manuel sil veya farklı email gir)
    4) Yeni admin oluştur: is_admin=True, is_active=True, is_approved=True

GÜVENLIK:
    Şifre asla log'a yazılmaz. Production'da çalıştırırken bash history'sine
    `ADMIN_PASSWORD=...` yazmayın — interaktif mode daha güvenli.
"""
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env'i otomatik yükle
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
except ImportError:
    pass


def main():
    # ── Email ve şifre topla ──
    email = (os.environ.get('ADMIN_EMAIL') or '').strip().lower()
    password = os.environ.get('ADMIN_PASSWORD') or ''
    full_name = os.environ.get('ADMIN_FULL_NAME', '').strip()
    company = os.environ.get('ADMIN_COMPANY', '').strip()

    if not email:
        email = input("Admin e-postası: ").strip().lower()
    if not email or '@' not in email:
        print("HATA: Geçersiz e-posta adresi.", file=sys.stderr)
        sys.exit(1)

    if not password:
        password = getpass.getpass("Şifre (gizli): ")
        password_again = getpass.getpass("Şifre (tekrar): ")
        if password != password_again:
            print("HATA: Şifreler eşleşmiyor.", file=sys.stderr)
            sys.exit(1)

    if len(password) < 8:
        print("HATA: Şifre en az 8 karakter olmalı.", file=sys.stderr)
        sys.exit(1)

    if not full_name:
        full_name = input("Ad Soyad [BMK Admin]: ").strip() or 'BMK Admin'
    if not company:
        company = input("Şirket [BMK]: ").strip() or 'BMK'

    # ── App context içinde oluştur ──
    from app import create_app
    from config import Config
    from extensions import db
    from models import User

    flask_app = create_app(Config)
    with flask_app.app_context():
        existing = User.query.filter_by(email=email).first()
        if existing:
            if existing.is_admin:
                print(f"HATA: Bu e-posta ile zaten bir admin var: {email}", file=sys.stderr)
                print("Çözüm: Şifresini değiştirin veya bu hesabı silin.", file=sys.stderr)
                sys.exit(1)
            else:
                # Mevcut normal kullanıcıyı admin'e terfi et
                answer = input(f"'{email}' var ama admin değil. Admin'e terfi edilsin mi? [e/h]: ").strip().lower()
                if answer not in ('e', 'evet', 'y', 'yes'):
                    print("İptal edildi.")
                    sys.exit(0)
                existing.is_admin = True
                existing.is_active = True
                existing.is_approved = True
                existing.set_password(password)
                db.session.commit()
                print(f"✓ '{email}' admin'e terfi edildi.")
                return

        admin = User(
            email=email,
            full_name=full_name,
            company=company,
            is_admin=True,
            is_active=True,
            is_approved=True,
        )
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        print(f"✓ Admin oluşturuldu: {email}")
        print(f"  Giriş: http://localhost:5005/login")


if __name__ == '__main__':
    main()

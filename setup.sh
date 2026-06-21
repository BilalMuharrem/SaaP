#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup.sh — BMK YENİ MAKİNE KURULUMU (tek komut)
#
# Kullanım:   ./setup.sh
# Amaç:       git clone sonrası boş bir makinede uygulamayı çalışır hale getirmek.
#             venv + bağımlılıklar + Playwright + .env + DB şeması.
#
# Güvenli ve TEKRAR ÇALIŞTIRILABİLİR (idempotent): zaten yapılmış adımları atlar.
# Yani .env'i doldurduktan sonra tekrar `./setup.sh` çalıştırmak güvenlidir.
# ─────────────────────────────────────────────────────────────────────────────
set -u

cd "$(dirname "$0")"

say()  { printf "\n\033[1;36m▶ %s\033[0m\n" "$1"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$1"; }
warn() { printf "\033[1;33m  ⚠ %s\033[0m\n" "$1"; }
err()  { printf "\033[1;31m  ✗ %s\033[0m\n" "$1"; }

# ── 1. Python 3.11 ───────────────────────────────────────────────────────────
say "1/7 — Python kontrol ediliyor"
if command -v python3.11 >/dev/null 2>&1; then
  PYBIN=python3.11
elif command -v python3 >/dev/null 2>&1; then
  PYBIN=python3
  warn "python3.11 yok, python3 kullanılıyor ($(python3 --version 2>&1)). 3.11 önerilir: brew install python@3.11"
else
  err "Python bulunamadı. Kur: brew install python@3.11"; exit 1
fi
ok "$($PYBIN --version 2>&1)"

# ── 2. Sanal ortam ───────────────────────────────────────────────────────────
say "2/7 — Sanal ortam (.venv)"
if [ ! -d .venv ]; then
  $PYBIN -m venv .venv && ok ".venv oluşturuldu" || { err "venv oluşturulamadı"; exit 1; }
else
  ok ".venv zaten var (atlandı)"
fi

# ── 3. Python bağımlılıkları ─────────────────────────────────────────────────
say "3/7 — Bağımlılıklar kuruluyor (requirements.txt)"
./.venv/bin/pip install --upgrade pip -q
if ./.venv/bin/pip install -r requirements.txt -q; then
  ok "Tüm paketler kuruldu"
else
  err "pip install başarısız — yukarıdaki hatayı incele"; exit 1
fi

# ── 4. Playwright tarayıcısı ─────────────────────────────────────────────────
say "4/7 — Playwright Chromium (Trendyol SEO taraması için)"
if ./.venv/bin/playwright install chromium >/dev/null 2>&1; then
  ok "Chromium hazır"
else
  warn "Chromium kurulamadı — Trendyol SEO çalışmayabilir. Elle: ./.venv/bin/playwright install chromium"
fi

# ── 5. .env ──────────────────────────────────────────────────────────────────
say "5/7 — .env (sırlar)"
ENV_NEEDS_FILL=0
if [ ! -f .env ]; then
  cp .env.example .env
  warn ".env yoktu → .env.example'dan oluşturuldu"
  ENV_NEEDS_FILL=1
else
  ok ".env mevcut"
fi
# SECRET_KEY boşsa otomatik üret (kolaylık)
if grep -qE '^SECRET_KEY=\s*$' .env 2>/dev/null; then
  _KEY=$(./.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))")
  # macOS/BSD sed uyumlu in-place
  sed -i '' "s|^SECRET_KEY=.*|SECRET_KEY=${_KEY}|" .env 2>/dev/null || \
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${_KEY}|" .env
  ok "SECRET_KEY otomatik üretildi"
fi
# Hâlâ doldurulması gereken sırlar var mı?
if grep -qE '^(GROQ_API_KEY|ADMIN_EMAIL|ADMIN_PASSWORD)=\s*$' .env 2>/dev/null; then
  ENV_NEEDS_FILL=1
fi

# ── 6. Redis ─────────────────────────────────────────────────────────────────
say "6/7 — Redis (Celery broker)"
if command -v redis-cli >/dev/null 2>&1 && redis-cli ping >/dev/null 2>&1; then
  ok "Redis çalışıyor"
else
  warn "Redis çalışmıyor. Kur + başlat:  brew install redis && brew services start redis"
fi

# ── 7. PostgreSQL + şema ─────────────────────────────────────────────────────
say "7/7 — Veritabanı (PostgreSQL + şema)"
DB_NAME="${DB_NAME:-saas_db}"
DB_OK=0
if ! command -v psql >/dev/null 2>&1; then
  warn "psql/createdb PATH'te yok. PostgreSQL kur:  brew install postgresql@15 && brew services start postgresql@15"
  warn "(Zaten kuruluysa PATH'e ekle, sonra ./setup.sh tekrar çalıştır.)"
elif [ "$ENV_NEEDS_FILL" = "1" ]; then
  warn "Önce .env'deki sırları doldur (GROQ_API_KEY, ADMIN_EMAIL, ADMIN_PASSWORD),"
  warn "sonra ./setup.sh komutunu TEKRAR çalıştır → DB şeması o zaman kurulur."
else
  # DB yoksa oluştur
  if psql -lqt 2>/dev/null | cut -d '|' -f1 | tr -d ' ' | grep -qx "$DB_NAME"; then
    ok "Veritabanı '$DB_NAME' mevcut"
  else
    createdb "$DB_NAME" 2>/dev/null && ok "Veritabanı '$DB_NAME' oluşturuldu" \
      || warn "'$DB_NAME' oluşturulamadı (elle: createdb $DB_NAME)"
  fi
  # Tabloları + plan/admin seed'i yarat (init_db → db.create_all)
  if ./.venv/bin/python -c "from app import app; from models import init_db; init_db(app)" 2>/dev/null; then
    ok "Tablolar + plan/admin seed oluşturuldu (init_db)"
    # Alembic'i 'head'e damgala → gelecekteki migration'lar düzgün çalışsın
    ./.venv/bin/alembic stamp head >/dev/null 2>&1 && ok "Alembic 'head'e damgalandı" \
      || warn "alembic stamp atlandı (kritik değil)"
    DB_OK=1
  else
    err "init_db başarısız — .env DATABASE_URL doğru mu, Postgres çalışıyor mu?"
  fi
fi

# ── Özet ─────────────────────────────────────────────────────────────────────
printf "\n\033[1;35m═══════════════════════════════════════════════════════\033[0m\n"
if [ "$ENV_NEEDS_FILL" = "1" ]; then
  printf "\033[1;33m  KURULUM YARIM — önce .env'i doldur, sonra tekrar ./setup.sh\033[0m\n"
  echo  ""
  echo  "  Doldurulacaklar (.env içinde):"
  echo  "    • GROQ_API_KEY    → https://console.groq.com/keys"
  echo  "    • ADMIN_EMAIL     → ilk admin e-postan"
  echo  "    • ADMIN_PASSWORD  → güçlü bir şifre"
elif [ "$DB_OK" != "1" ]; then
  printf "\033[1;33m  KURULUM YARIM — ortam hazır ama VERİTABANI kurulmadı.\033[0m\n"
  echo  ""
  echo  "  PostgreSQL'i kur/başlat, sonra ./setup.sh komutunu tekrar çalıştır:"
  echo  "    brew install postgresql@15 && brew services start postgresql@15"
else
  printf "\033[1;32m  ✓ KURULUM TAMAM — uygulamayı başlat:\033[0m\n"
  echo  ""
  echo  "    ./.venv/bin/python app.py"
  echo  ""
  echo  "  (web + worker + beat otomatik açılır; tarama adımları konsola akar)"
fi
printf "\033[1;35m═══════════════════════════════════════════════════════\033[0m\n\n"

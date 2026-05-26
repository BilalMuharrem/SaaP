# BMK — Rekabet İstihbaratı

Trendyol & Hepsiburada satıcıları için yapay zeka destekli rekabet istihbaratı platformu.

> **Şu an Beta'da.** İlk müşterileri arıyoruz; halka açık launch için bkz.
> [Eksikler bölümü](#-launch-öncesi-eksikler).

---

## Modüller

| Modül | Ne yapar |
|---|---|
| **Fiyat Takibi** | Trendyol/Hepsiburada/N11/Çiçeksepeti/PttAVM/Amazon TR ürün fiyatlarını günde 4 stratejik saatte tarar. Maliyet girilince kâr/zarar analizi otomatik. |
| **SEO Sıralama** | Bir keyword + ürün URL'i ile Trendyol arama sonuçlarında konumu izler. (Hepsiburada SEO yakında.) |
| **YZ Strateji Danışmanı** | Fiyat + yorum + SEO verisini Llama-3.3-70b'ye verir, 5 bölümlü stratejik rapor üretir. "Görünmezlik tuzağı" kuralı yerleşik. |
| **Bildirim Merkezi** | Fiyat değişimi, fırsat, tehdit, SEO, sistem mesajları — kategorilerle filtreli. |

---

## Teknoloji

- **Backend:** Flask 3.x + SQLAlchemy + PostgreSQL
- **Worker:** Celery + Redis (4 stratejik tarama saati)
- **Scraping:** Playwright (stealth) + cloudscraper + curl_cffi (fallback zinciri)
- **LLM:** Groq API (Llama-3.3-70b-versatile)
- **Frontend:** Server-rendered Jinja, vanilla JS, ApexCharts (CDN), tema-aware CSS
- **Observability:** Sentry SDK + Python logging (RotatingFileHandler)
- **Test:** pytest + pytest-flask (138 test)

---

## Hızlı Kurulum

### Gereksinimler

- Python 3.11+
- PostgreSQL 14+
- Redis 6+ (yerel ya da Upstash gibi managed)
- `pg_dump` (PostgreSQL client tools — yedekleme scripti için)

### Adımlar

```bash
# 1. Klonla
git clone <repo-url> bmk
cd bmk

# 2. Sanal ortam
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Playwright tarayıcıları
playwright install chromium

# 4. .env dosyası
cp .env.example .env
# .env'i aç ve doldur:
#   SECRET_KEY=  → python -c "import secrets; print(secrets.token_urlsafe(48))"
#   DATABASE_URL=postgresql://user:pass@localhost:5432/bmk_dev
#   GROQ_API_KEY=...
#   CELERY_BROKER_URL=redis://localhost:6379/0

# 5. PostgreSQL veritabanını oluştur
createdb bmk_dev

# 6. Şemayı kur (init_db ALTER TABLE'ları da uygular)
./.venv/bin/python -c "from app import app; from models import init_db; init_db(app)"

# 7. Çalıştır — tek komut, arka planda
./scripts/start.sh
# → http://localhost:5005
# Web + Celery worker + Celery beat hepsi arka planda
# Loglar: logs/{web,worker,beat}.out

# Durum kontrol / yeniden başlat / durdur
./scripts/start.sh status
./scripts/start.sh restart
./scripts/stop.sh
```

**Schedule** (Europe/Istanbul, otomatik):
- Fiyat taraması: **03:15, 09:15, 15:15, 21:15**
- SEO taraması: **03:45, 09:45, 15:45, 21:45** (fiyat'tan 30 dk sonra, bot çakışmasını önler)

### macOS Özel

- AirPlay Receiver port 5000'i tutar → uygulama 5005'te çalışır.
- `--pool=solo` zorunlu — `prefork` SIGTERM yiyor (HOTFIX 1.36).

### İlk Kullanıcı

```bash
# Sanal ortamda Python shell aç
./.venv/bin/python

>>> from app import app
>>> from extensions import db
>>> from models import User, Plan
>>> with app.app_context():
...     plan = Plan.query.filter_by(name='enterprise').first()
...     admin = User(email='admin@bmk.local', full_name='Admin',
...                  is_admin=True, is_active=True, is_approved=True,
...                  onboarding_completed=True, plan_id=plan.id)
...     admin.set_password('admin-strong-password')
...     db.session.add(admin); db.session.commit()
```

Sonra `http://localhost:5005/login` → `admin@bmk.local` / parolanla giriş.

---

## Proje Yapısı

```
SaaS App/
├── app.py                  # Flask factory (create_app) + entry point
├── extensions.py           # db, login_manager, celery, limiter — tek örnek
├── config.py               # Config + TestConfig
├── models.py               # SQLAlchemy modelleri + init_db auto-migration
├── worker.py               # Celery task tanımları + scraping orchestration
├── bmk_suite.py            # Eski desktop sürüm — worker bazı utility'lerini hâlâ kullanır
├── logging_config.py       # Logging + Sentry kurulumu
├── blueprints/             # Route modülleri (her özellik kendi blueprint'i)
│   ├── auth.py             # /login, /register, /logout, /
│   ├── dashboard.py        # /dashboard, /history
│   ├── jobs.py             # /new-request, /job/*, sistem-status API
│   ├── tracked.py          # /tracked-products/* (fiyat takibi)
│   ├── seo.py              # /seo-tracker, /seo-graph, /api/generate-seo-tips
│   ├── ai_consultant.py    # /ai-consultant, /generate, /download-pdf
│   ├── notifications.py    # /notifications, /api/notifications/*
│   ├── plans.py            # /plans (kullanıcı plan vitrini)
│   ├── admin.py            # /admin/* (panel)
│   ├── onboarding.py       # /onboarding/* (3 adımlı wizard)
│   ├── demo.py             # /demo (public, signup'sız önizleme)
│   └── health.py           # /healthz, /healthz/deep (UptimeRobot)
├── services/               # İş mantığı
│   ├── ai/                 # Groq çağrıları + prompt'lar
│   ├── notifications/      # Bildirim sınıflandırıcı
│   ├── scraping/           # Trendyol/Hepsiburada/N11/... scraperları
│   └── demo_data.py        # Yeni kayıt için demo ürün seed
├── utils/                  # Yardımcı modüller
│   ├── filters.py          # turkdate, timeago Jinja filtreleri
│   ├── decorators.py       # admin_required
│   └── analytics.py        # extract_review_insights_from_jobs
├── templates/              # Jinja2 şablonları
│   ├── base.html           # Customer layout (sidebar dahil)
│   ├── landing.html        # Halka açık landing page
│   ├── demo.html           # /demo (self-contained, sidebar yok)
│   ├── onboarding/         # 3 adımlı wizard
│   ├── admin/              # Admin layout + sayfaları
│   └── _macros/            # tooltips.html — yeniden kullanılan UI parçaları
├── static/                 # CSS, favicon, OG image
├── scripts/                # Otomasyon
│   ├── backup_db.py        # PostgreSQL yedekleme (cron-ready)
│   ├── migrate_url_for.py  # Tek seferlik template url_for migration (kullanıldı)
│   ├── dev/                # Geliştirici tool'ları (baslat.py, seed_opportunity.py)
│   └── _archive/           # Eski/tek seferlik script'ler (referans için saklı)
├── tests/                  # pytest test suite (138 test)
│   ├── conftest.py         # Fixtures (app, client, kullanıcılar, db cleanup)
│   └── test_*.py
├── docs/
│   └── operations.md       # Sentry/UptimeRobot/Backup/Staging rehberi
├── procfile                # Heroku-tarzı: web + worker + beat
├── requirements.txt
├── pytest.ini
├── .env.example            # Tüm env değişkenleri (yorumlu)
└── .github/workflows/ci.yml # GitHub Actions — push'ta pytest çalıştırır
```

---

## Sık Kullanılan Komutlar

```bash
# Test
./.venv/bin/pytest                              # tüm suite
./.venv/bin/pytest tests/test_auth.py -v        # tek dosya
./.venv/bin/pytest -k "demo"                    # adında "demo" geçenler
./.venv/bin/pytest --cov                        # coverage raporu

# Veritabanı yedekleme (manuel test)
./.venv/bin/python scripts/backup_db.py --dry-run
./.venv/bin/python scripts/backup_db.py

# Logları izle
tail -f logs/bmk-web.log
tail -f logs/bmk-worker.log

# Celery worker durumu
./.venv/bin/celery -A extensions.celery inspect active
```

---

## 🚧 Launch Öncesi Eksikler

Uygulama teknik olarak çalışıyor ancak halka açık launch için ŞU şeyler gerekiyor:

| Eksik | Çözüm | Süre |
|---|---|---|
| KVKK Aydınlatma + Gizlilik + Kullanım Koşulları | Şirket bilgileri lazım; avukat/şablon ile yazılır | 1 gün |
| Şifre sıfırlama | Email altyapısı şart (Resend/SendGrid) | 1-2 gün |
| Ödeme | iyzico (TR) veya Stripe entegrasyonu | 1 hafta |
| Analytics | Plausible/PostHog tracking snippet + funnel | 1 gün |
| Mobile audit | Tüm sayfaları 320/375/414 px'de test + düzeltme | 1-2 gün |

Detaylar için sohbet geçmişine bak — her biri için ayrı Faz tasarlandı, sırası gelince devam edilir.

---

## Dokümantasyon

- [`docs/operations.md`](docs/operations.md) — Üretim ortamı (Sentry, UptimeRobot, backup, restore, runbook)
- [`CLAUDE.md`](CLAUDE.md) — Mimari kararlar + konvansiyonlar (gelecek refactor'lar için)
- `.env.example` — Tüm env değişkenleri (yorum satırlarıyla açıklamalı)

---

## Lisans

Özel — tüm hakları saklıdır.

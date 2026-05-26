# CLAUDE.md — BMK Project Memory

> Bu dosya gelecekte Claude (veya başka bir AI agent) bu kod tabanına geldiğinde
> sıfırdan keşfetmek zorunda kalmasın diye yazıldı. Mimari kararlar, konvansiyonlar
> ve "gotcha"lar burada — kod okumadan önce bu dosyayı oku.

---

## Bir Cümlede Proje

BMK, Trendyol & Hepsiburada satıcıları için yapay zeka destekli **rekabet
istihbaratı platformudur**. Beta'da. Fiyat takibi + SEO sıralama + YZ
strateji raporu + bildirim merkezi olmak üzere 4 ana modülü vardır.

## Önceki Çalışmalar (özet)

Bu proje büyük bir refactor'dan geçti (Faz 0 → 9). Önceki durum:
- 3079 satırlık tek `app.py`, 30+ dağınık script proje kökünde
- Sahte schema.org rating'leri ve sahte trust logo'ları (KVKK riskli)
- Yarım bırakılmış "Zafiyet Radarı" modülü
- Test yok, CI yok, Sentry yok, backup yok
- Git deposu bile yoktu

Şu anki durum:
- 13 Flask blueprint, services/ ayrımı, utils/ yardımcıları
- 138 pytest test + GitHub Actions CI
- Sentry + DB backup script + healthz endpoint + ops docs
- Public demo sayfası (signup'sız)
- Onboarding wizard + demo data seed
- Dürüst landing (Beta konumlandırma, sahte içerik temizliği)

---

## Mimari Konvansiyonlar

### Flask App Factory

`app.py` ve `extensions.py` birlikte çalışır:

```python
# extensions.py — tek örnek
db = SQLAlchemy()
login_manager = LoginManager()
celery = Celery(...)
limiter = Limiter(...)

# app.py — factory
def create_app(config_object=Config):
    flask_app = Flask(__name__)
    flask_app.config.from_object(config_object)
    db.init_app(flask_app)
    login_manager.init_app(flask_app)
    limiter.init_app(flask_app)
    register_filters(flask_app)
    register_blueprints(flask_app)
    return flask_app

# Modül seviyesinde — worker.py ve gunicorn bunu bekler
app = create_app()
```

**KURAL:** Yeni uzantı eklenirken `extensions.py`'de örnek oluşturulur, `create_app()` içinde init_app çağrılır. **ASLA** `app.py`'de doğrudan `Flask()` çağrısı yapma.

### Blueprint Eklemek

```python
# 1) blueprints/yeni_ozellik.py
from flask import Blueprint
bp = Blueprint('yeni_ozellik', __name__)

@bp.route('/yeni')
def index():
    ...

# 2) blueprints/__init__.py
from .yeni_ozellik import bp as yeni_ozellik_bp

def register_blueprints(app):
    ...
    app.register_blueprint(yeni_ozellik_bp)
```

**KURAL:** Template'lerde `url_for('yeni_ozellik.index')` formatında kullan — blueprint-prefixed. Düz isim (`url_for('index')`) **çalışmaz**, eski koddan kalmıştı, Faz 1G'de tüm template'ler güncellendi.

### Modeller (`models.py`)

Tek dosyada tüm SQLAlchemy modelleri. `init_db(app)` fonksiyonu:
- `db.create_all()` çağırır
- Eski DB'lerde eksik kolonlar için `ALTER TABLE ... ADD COLUMN` çalıştırır (idempotent)
- Yeni bir kolon eklerken: model'e ekle + `init_db`'ye matching `ALTER TABLE` ekle

**Alembic kullanmıyoruz** — `init_db` tarzı ALTER TABLE migration yeterli olduğu sürece. Schema kompleksleşirse Alembic'e geçilebilir.

### Worker (`worker.py`)

7400 satırlık dev bir dosya. Celery task tanımları + Trendyol/Hepsiburada/N11/vb. scraper'ları içerir. **Bu dosyaya cerrahi müdahale et** — agresif refactor riskli.

Önemli noktalar:
- `from app import app` ile app context'i alır. App factory pattern'iyle uyumlu.
- `_import_bmk_utils()` — eskiden `bmk_suite.py` (tkinter desktop)'tan utility'ler alırdı; şimdi `services/scraping/parsers.py`'den. İsim geriye uyumluluk için aynı.
- 4 Celery task: `check_tracked_products_task` (toplu fiyat tarama), `check_single_product_task` (tek ürün), `process_job_task` (kullanıcının yeni analiz talebi), `check_keyword_trackers_task` (SEO tarama).
- Beat schedule `extensions.py`'de — günde 4 stratejik saat: 03:15, 09:15, 15:15, 21:15 (Europe/Istanbul).

### Logging + Sentry

`logging_config.setup_logging()` çağrısı:
- `RotatingFileHandler` — 10MB, 5 backup
- Konsol + dosya çift handler
- Sonunda `setup_sentry()` otomatik çağrılır — `SENTRY_DSN` env varsa init eder, yoksa no-op

**KURAL:** `print()` kullanma, `logging.getLogger(__name__)` kullan. Eski kodda `print(f"[App] ...")` vardı, Faz 1I'de hepsi `log.info`/`log.exception`'a çevrildi.

### Rate Limiting

`extensions.limiter` — Flask-Limiter. Default limit yok; hassas endpoint'ler explicit limit:

```python
@bp.route('/ai-consultant/generate', methods=['POST'])
@login_required
@limiter.limit("5 per hour;15 per day")
def generate(): ...
```

Test'te `tests/conftest.py:_db_cleanup` fixture'ı `limiter.reset()` çağırır — test'ler arası izolasyon.

---

## Anahtar Konvansiyonlar

### Tooltip Sistemi

```jinja
{% from '_macros/tooltips.html' import tooltip %}

<label>Birim Maliyet {{ tooltip('Sana mal oluş fiyatı...') }}</label>
```

CSS-only (hover + focus). `static/style.css` sonunda `.help-tip` class'ı tanımlı. Erişilebilir (`tabindex="0"`, `aria-label`). Detay: Faz 7E.

### Demo Data Seed

Yeni kayıt sonrası otomatik demo ürün yüklenir:

```python
# blueprints/auth.py register içinde:
from services.demo_data import seed_demo_products
seed_demo_products(user.id)
```

Demo URL'leri `services/demo_data.py:DEMO_PRODUCTS` — değiştirmek için bu listeyi düzenle. Eklenen ürünler `is_demo=True` flag'iyle işaretli; dashboard banner'ı bunu kullanır.

### Banner Mantığı (Dashboard)

`blueprints/dashboard.py` üç state hesaplar:

| State | Koşul | Banner |
|---|---|---|
| `is_first_time_user` | total_jobs=0 AND tracked_count=0 | Mavi "İlk rakibini ekle" |
| `has_only_demo` | total_jobs=0 AND own_tracked=0 AND tracked>0 | Amber "Sistem nasıl çalışır" + "Kendi ürününü ekle" |
| (normal) | own_tracked>0 | Banner yok |

`is_demo` flag'i bunu mümkün kılar — kendi ürünü vs demo ürünü ayırt.

### Onboarding Wizard

`User.onboarding_completed` BOOLEAN. Login sonrası `auth.login`:
- admin → `/admin`
- !onboarding_completed → `/onboarding`
- else → `/dashboard`

3 adım: start → product → cost → done. Skip butonu her adımda var. Template'ler `templates/onboarding/_base.html`'i extend eder (self-contained, sidebar yok).

### Public Demo

`/demo` rotası auth gerektirmez. `blueprints/demo.py` MOCK_PRICE_GROUP, MOCK_SEO, MOCK_AI_REPORT, MOCK_NOTIFICATIONS sabitleri ile çalışır. **DB'ye dokunmaz.** Landing hero CTA "Demoyu İncele" buraya yönlenir.

### Health Endpoints

- `GET /healthz` — liveness, auth yok, "ok" döner
- `GET /healthz/deep` — DB + Redis ping, 200/503

UptimeRobot için tasarlandı. `app.py`'deki Werkzeug log filter'ı `/healthz`'i de susturur (polling log'u kirletmesin).

---

## "Gotcha"lar

### 1. Sahte sosyal kanıt kabul edilmez

Proje boyunca tekrar tekrar şu temizliği yaptık:
- ❌ schema.org aggregateRating "4.8 / 127 oy" — silindi (Faz 2A)
- ❌ Trust logo'lar (PetPivot, Patiguard, vb.) — silindi (Faz 7B)
- ❌ Testimonial'lar (Mert K, Ayşe T, Cem B) — silindi (Faz 7D)

**KURAL:** Gerçek müşteri yorumu/rating'i olmadan hiçbir landing element'i sahte sosyal kanıt içeremez. Beta hikayesi tercih edilir.

### 2. Email altyapısı YOK

`services/email/` yok. Kayıt onay maili, şifre sıfırlama, fiyat alarm maili — hiçbiri kurulu değil. **Şifre sıfırlama özelliği yok**, kullanıcı şifresini unutursa admin manuel reset eder.

Faz 5C bekliyor; kurulduğunda Resend tercih edildi (`docs/operations.md`'de plan var).

### 3. Ödeme YOK

Stripe / iyzico entegrasyonu yok. Beta dönemi tüm planlar "3 ay ücretsiz" olarak konumlandırıldı. Ödeme gerçek müşteri geldiğinde kurulur (Faz 5D).

### 4. KVKK metni YOK

Kullanım Koşulları, Gizlilik Politikası, Aydınlatma Metni — hiçbiri yok. **Halka açık launch ÖNCESİ** yazılması gerekir (Faz 6, şirket kurulunca).

### 5. Manuel onay default

`APPROVAL_MODE=manual` — yeni kayıtlar admin onayı bekler. Mesaj kullanıcıya "en geç 24 saat içinde" sözü verir. Admin sidebar'da pending sayacı + tab title prefix var (Faz 6).

Auto-approve mode da var (`APPROVAL_MODE=auto`); spam riski nedeniyle aktif değil.

### 6. Zafiyet Radarı kalıntıları

Modeller (`StockHistory`, `VulnerabilityAlert`) DB'de hâlâ duruyor — silmedik (data migration riskli). UI ve route tamamen kaldırıldı. `is_radar_tracked=False` kasıtlı olarak her yeni ürün yaratmada açıkça yazılıyor (gelecekte feature dönerse zemin hazır).

### 7. Rate-limit testte kümülatif

Tests arası `limiter.reset()` her fixture'da çağrılır AMA bazı senaryolarda (özellikle `test_register_*`) limit kümülatif tetiklenebilir. Çözüm: register flow testlerini ayrı dosyalarda gruplama veya `inspect.getsource()` ile kaynak kodu doğrulama (örnek: `test_admin_approval_flow.py:test_registration_message_mentions_24_hours`).

### 8. `--pool=solo` zorunlu (macOS)

`celery worker --pool=prefork` macOS'ta SIGTERM yer. `procfile` ve `scripts/dev/baslat.py` `--pool=solo` kullanır. Linux production'da `--pool=prefork` kullanılabilir.

### 9. Port 5005

macOS AirPlay Receiver port 5000'i tutar. App 5005'te çalışır. `PORT` env ile override edilebilir.

---

## Test Konvansiyonları

```bash
./.venv/bin/pytest                     # tüm suite (138 test, ~14sn)
./.venv/bin/pytest -k "demo"           # adında demo geçen testler
./.venv/bin/pytest tests/test_X.py -v  # tek dosya verbose
```

`tests/conftest.py` fixture'ları:
- `app` (session scope) — SQLite in-memory + plans seed'li
- `client` — anonim test client
- `auth_client` / `admin_client` / `enterprise_client` — login'li session

DB her test'ten önce temizlenir (autouse `_db_cleanup`). Plan'lar korunur.

**KURAL:** Yeni feature → yeni test. Test yazmadan PR mergelenmez (CI bunu zorunlu kılar).

---

## İleri Bakış (deferred phases)

| Faz | Ne | Ne zaman |
|---|---|---|
| 5C | Email altyapısı (Resend) | İlk gerçek kullanıcı geldiğinde |
| 5D | Ödeme (iyzico tercih edilir, Stripe TR fatura kesemez) | İlk ödeyen müşteri |
| 5E | Review collection (NPS sonrası) | İlk mutlu müşteriler |
| 6 | KVKK metinleri + cookie banner + hesap silme | Şirket kurulunca |
| 11 | Plausible/PostHog analytics | Launch'tan hemen önce |
| 12 | Mobile audit + skeleton'lar + 2FA | Sürekli |

Detaylar git log'da — her faz commit'i Faz numarasıyla başlar (`feat(faz7d): ...`).

---

## Bana (Sonraki Claude'a) Notlar

1. **Önce git log oku** — son 10-15 commit, projenin ne yöne gittiğini söyler.
2. **138 test güvenliğindir** — değişiklik yaparken `pytest` çalıştırmadan commit etme.
3. **Sahte içerik yazma** — kullanıcı bunu en sevmediği şey. Beta hikayesi her zaman dürüst yol.
4. **`worker.py`'ye sakin yaklaş** — 7400 satır, refactor edilmedi (Faz 1H sadece tek bir helper modülü ayırdı). Büyük dokunuş risk taşır.
5. **`docs/operations.md`** — Sentry/UptimeRobot/backup gibi ops konularında kullanıcıya açıklama yaparken o dosyayı referans göster.
6. **README.md** — yeni geliştirici için, mimari değil kurulum odaklı. Sen (Claude) için bu CLAUDE.md daha değerli.

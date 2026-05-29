# Operations Rehberi

BMK üretim ortamı için işletim notları. Geliştirme ortamı için `.env.example` yeterlidir.

---

## 1. Sentry (Hata Takibi)

**Kurulum:**
1. https://sentry.io üzerinde ücretsiz hesap aç (5K event/ay).
2. Yeni proje oluştur: Platform = **Python / Flask**.
3. DSN'i kopyala (`https://<key>@<org>.ingest.sentry.io/<project>`).
4. Üretim `.env`'sine ekle:
   ```
   SENTRY_DSN=https://abc...@xyz.ingest.sentry.io/12345
   SENTRY_ENVIRONMENT=production
   SENTRY_TRACES_RATE=0.1
   ```
5. Uygulamayı yeniden başlat. İlk hatada Sentry'de görünür.

**Doğrulama:**
- Bilinçli bir hata atarak test: konsola `raise Exception("sentry test")` veya bir route içinde.
- Sentry dashboard → Issues sekmesinde 1 dk içinde görünmeli.

**KVKK Notu:** `send_default_pii=False` ayarı sayesinde kullanıcı IP, e-posta gibi kişisel veriler Sentry'ye gönderilmez. Sadece hata + stack trace.

---

## 2. UptimeRobot (Erişim İzleme)

**Kurulum:**
1. https://uptimerobot.com → ücretsiz hesap (50 monitör, 5 dakika aralık).
2. **Add New Monitor**:
   - Type: **HTTP(s)**
   - URL: `https://bmk.app/healthz` (liveness — hafif, her dakika OK)
   - Friendly name: `BMK Production - Liveness`
   - Monitoring interval: 5 dk (ücretsiz tier)
   - Alert contacts: e-posta (ücretsiz) veya SMS/Telegram (Pro)
3. İkinci monitör:
   - URL: `https://bmk.app/healthz/deep` (DB + Redis kontrolü)
   - Interval: 15-30 dk (daha pahalı endpoint)
   - Alert: "Status code != 200"

**Önerim:** Telegram bot bildirimi (ücretsiz, anında ulaşır).
Telegram entegrasyonu: My Settings → Alert Contacts → Add → Telegram.

---

## 3. Veritabanı Yedekleme

**Manuel:**
```bash
./.venv/bin/python scripts/backup_db.py
```

**Otomatik (cron — her gece 02:00):**
```bash
crontab -e
```
```cron
0 2 * * * cd /home/bmk/app && ./.venv/bin/python scripts/backup_db.py >> logs/backup.log 2>&1
```

**Davranış:**
- `backups/bmk_YYYY-MM-DD_HHMMSS.sql.gz` formatında dosya yaratır.
- Son 14 günü tutar, eskileri otomatik siler (env: `BACKUP_KEEP=14`).
- Hata durumunda exit kodu non-zero — cron mail bildirimi gönderir.

**Geri yükleme:**
```bash
gunzip -c backups/bmk_2025-05-26_020000.sql.gz | psql $DATABASE_URL
```

**S3 / Backblaze B2 (üretim için önerilir):**
Cron komutuna `aws s3 cp` ekle:
```cron
0 2 * * * cd /home/bmk/app && ./.venv/bin/python scripts/backup_db.py && aws s3 sync backups/ s3://bmk-backups/ --delete
```

---

## 4. Log Yönetimi

- Logger: `logging_config.setup_logging()` her başlatmada çağrılır.
- Dosya: `logs/bmk-web.log` (Flask) ve `logs/bmk-worker.log` (Celery).
- Rotation: 10MB sonra, son 5 dosya saklanır (`bmk-web.log.1`, `.2`, ...).
- Seviye: env `LOG_LEVEL=INFO` (default), `DEBUG` development'ta.

**Disk sıkışıyorsa:**
```bash
# Eski sıkıştırılmamış yedek logları sıkıştır
gzip logs/bmk-web.log.*
```

---

## 5. Staging Environment

Üretime almadan önce değişiklikleri test etmek için ayna ortam.

**Kurulum (özetle):**
1. Production'un `.env`'sini kopyala, sadece şunları değiştir:
   - `DATABASE_URL` → ayrı staging DB
   - `SENTRY_ENVIRONMENT=staging`
   - `APPROVAL_MODE=auto` (test için)
   - `GROQ_API_KEY` → ayrı key (LLM masrafları karışmasın)
2. Subdomain: `staging.bmk.app` → ayrı sunucu / ayrı port.
3. Deployment akışı:
   - `git push origin main` → `staging` deploy
   - Staging'de smoke test
   - Manuel onay → `git tag prod-v1.X.Y` → `production` deploy

**Minimum şart:** staging DB ayrı olmalı; aksi halde production verisi üzerinde test edilir.

---

## 6. Health Endpoints

| Endpoint | Auth | Ne kontrol eder? | Hangi monitörü çağırır? |
|---|---|---|---|
| `/healthz` | ❌ Yok | Process ayakta mı | UptimeRobot liveness |
| `/healthz/deep` | ❌ Yok | DB + Redis ping | UptimeRobot readiness (15 dk) |

İkisi de auth gerektirmez (load balancer / monitör için). Hassas bilgi sızdırmaz, sadece "ok / degraded" döner.

---

## 7. Acil Durum Müdahalesi

**Production "down" — UptimeRobot alert:**
1. SSH ile sunucuya gir.
2. `systemctl status bmk-web bmk-worker` — process'ler ayakta mı?
3. `tail -100 logs/bmk-web.log` — son hata?
4. Sentry'ye bak — fresh exception var mı?
5. Servisi yeniden başlat: `systemctl restart bmk-web`
6. Hâlâ down ise: en son backup'tan restore + post-mortem.

**Database corruption:**
1. Servisi durdur: `systemctl stop bmk-web bmk-worker`
2. Mevcut DB'yi yedekle (hatalı bile olsa): `pg_dump > corrupted.sql`
3. Son sağlam backup'tan restore et.
4. Kayıp veri penceresini hesapla ve etkilenen kullanıcılara haber ver.

---

## 6. Admin Kullanıcı Yönetimi (Faz 10A)

**ESKİ DAVRANIŞ (kaldırıldı):** `init_db` hard-coded `admin@bmk.com / bmk2024admin`
yaratıyordu. Production'a deploy edip unutursanız, internetteki herkes admin
paneline girebilirdi. Bu davranış Faz 10A'da kaldırıldı.

**YENİ DAVRANIŞ:** Admin sadece şu iki yoldan biriyle oluşturulur.

### A) İlk deploy — env üzerinden tek seferlik

`.env` dosyasına ekleyin:
```
ADMIN_EMAIL=siz@firmaniz.com
ADMIN_PASSWORD=<güçlü-32-karakter-rastgele>
ADMIN_FULL_NAME=Adınız Soyadınız
ADMIN_COMPANY=Firma Adı
```

Uygulama açıldığında `init_db` bu env'leri okuyup admin yaratır.
**Sonra**: bu satırları `.env`'den **silin** (admin hesabı DB'de saklanır,
env'e gerek yok).

### B) İnteraktif script

Sunucuda terminal varsa:
```bash
./.venv/bin/python scripts/create_admin.py
```

Şifreyi gizli (getpass) sorar, bash history'sine yazılmaz. Mevcut bir
kullanıcıyı admin'e terfi etmek için de aynı script çalışır.

### Mevcut admin@bmk.com hesabını silme (Faz 10A geçişi)

Eğer production DB'nizde eski hard-coded admin varsa:
```bash
psql $DATABASE_URL -c "DELETE FROM users WHERE email='admin@bmk.com';"
```
Önce yeni admin yarattığınızdan emin olun — yoksa hiç admin kalmaz.

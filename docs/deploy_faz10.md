# Faz 10 Üretim Geçiş Kontrol Listesi

Bu rehber Faz 10A + 10B + 10C değişikliklerini **mevcut bir üretim sunucusuna**
güvenli şekilde uygulamak içindir. Sıraya sadık kal — her adım kendinden öncekine
dayanır. Bir adım kırılırsa BİR SONRAKİNE GEÇME.

**Tahmini süre:** 30-45 dk (kesinti penceresi). Manuel testle birlikte 1-1.5 saat.

---

## 0. Hazırlık (deploy gününden önce)

- [ ] **Yerel ortamda Faz 10 commit'lerini test ettin** (`pytest` 150/150 yeşil)
- [ ] **Çakışan açık PR yok** (`git status` temiz, `main`'desin)
- [ ] **Kullanıcılara haber verildi** — beklenmedik 30 dk kesinti planlı ise küçük bir e-posta/bildirim göndermek dürüst yol
- [ ] **Bu dosyayı yazdır veya ikinci ekranda aç** — sunucuya bağlıyken referansa erişim olsun
- [ ] **Geri dönüş planın hazır:** son backup'ın yeri ve restore komutu elinde mi?

---

## 1. Sunucuya bağlan ve mevcut durumu doğrula

```bash
# Sunucuya SSH
ssh user@sunucu

# Proje dizinine geç
cd /path/to/SaaS\ App

# Hangi commit'tesin?
git log --oneline -3

# Hangi branch?
git status
```

**Beklenen:** `main` branch, en güncel commit `aed09df` veya öncesi (Faz 9 sonu).

---

## 2. Backup — KRİTİK ADIM

```bash
# Veritabanı yedeği (zorunlu)
./.venv/bin/python scripts/backup_db.py

# Yedeğin yerini ve boyutunu doğrula
ls -lh backups/ | tail -3
```

**Beklenen:** `backups/bmk_2026-XX-XX_XXXXXX.sql.gz` — birkaç MB+. Boyut 0 ise
backup başarısız, **DURMA, devam etme**, sorunu çöz.

```bash
# (Opsiyonel ama önerilen) Yedek dosyasını sunucunun dışına kopyala
scp backups/bmk_2026-XX-XX_XXXXXX.sql.gz yerel:~/bmk-backups/
```

---

## 3. Servisleri durdur

```bash
./scripts/stop.sh

# Doğrula
./scripts/start.sh status
# Beklenen: tüm servisler "kapalı"

# Yedek kontrol: hâlâ koşan Python süreçleri var mı?
ps aux | grep -E "celery|gunicorn|flask" | grep -v grep
# Beklenen: boş çıktı. Varsa: kill -TERM <pid>
```

---

## 4. Kod güncellemesini al

```bash
# Lokal'de bir şey değişmediğinden emin ol
git status   # Beklenen: clean

# En güncel commit'leri çek
git fetch origin
git log --oneline origin/main -5

# Beklenen 3 Faz 10 commit'i görünmeli:
#   e000041 chore(faz10c): polish ...
#   0403eff feat(faz10b): sağlamlaştırma ...
#   163cb92 feat(faz10a): güvenlik sertleştirmesi ...

# Hızlı ileri (fast-forward)
git pull --ff-only origin main
```

---

## 5. Bağımlılıkları güncelle

```bash
# Faz 10A: Flask-WTF eklendi
# Faz 10B: alembic eklendi
# Faz 10C: requirements.txt artık pin'li lock dosyası

./.venv/bin/pip install -r requirements.txt

# Doğrula — kritik yeni paketler kuruldu mu?
./.venv/bin/pip show flask-wtf alembic | grep -E "Name|Version"
# Beklenen:
#   Name: Flask-WTF, Version: 1.3.0+
#   Name: alembic,  Version: 1.13.0+
```

---

## 6. `.env` dosyasını güncelle

```bash
# Açıp düzenle
nano .env   # veya vim, vs.
```

**Eklenmesi gereken yeni env'ler** (Faz 10A):

```bash
# ── İlk Admin (Faz 10A) ──
# Mevcut admin@bmk.com hesabını kullanmaya devam edeceksen:
ADMIN_EMAIL=admin@bmk.com
ADMIN_PASSWORD=<güçlü-yeni-şifre>     # Eski "bmk2024admin" GÜVENLİ DEĞİL, değiştir

# YA DA yeni bir admin yaratacaksan:
ADMIN_EMAIL=siz@firma.com
ADMIN_PASSWORD=<güçlü-32-karakter-rastgele>
ADMIN_FULL_NAME=Adınız Soyadınız
ADMIN_COMPANY=Firma Adı
```

**Doğrula** GROQ key'in zaten env'de — Faz 10A artık DB'den OKUMUYOR:

```bash
grep "GROQ_API_KEY" .env
# Beklenen: GROQ_API_KEY=gsk_... (gerçek bir değer)
# Boşsa: AI Consultant özelliği çalışmaz. Doldur veya görmezden gel.
```

---

## 7. Alembic'i mevcut DB ile sync et

İki seçenek var — DB'nde drift (şema farkı) var mı diye karara bağlı.

### Seçenek A — Drift yok / önemsemiyorum (HIZLI)

DB'yi "şu an baseline'dayım, bana dokunma" olarak işaretle:

```bash
./.venv/bin/alembic stamp head

# Doğrula
./.venv/bin/alembic current
# Beklenen: 9074133c49e8 (head)
```

Bu seçenek hiçbir ALTER TABLE çalıştırmaz, sadece `alembic_version` tablosunda
bir satır yazar. **Risk sıfır.**

### Seçenek B — Drift'leri otomatik uygula (TEMİZ)

Faz 10B'nin baseline migration'ı şu drift'leri düzeltir:
- `keyword_trackers.pool_id` FK constraint eklenir
- `tracked_products.global_product_id` FK constraint eklenir
- `tracked_products.is_demo` NOT NULL
- `users.onboarding_completed` NOT NULL
- 2 index rename

```bash
./.venv/bin/alembic upgrade head

# Doğrula
./.venv/bin/alembic current
# Beklenen: 9074133c49e8 (head)
```

Bu seçenek hiçbir veriye dokunmaz, sadece şema güzelleştirir.

**Hangisini seçerim?** Şüpheliyim → **A** (stamp). Bir hafta sonra bakıp B'yi
ayrı bir kesintide uygula. Risk yok.

---

## 8. Servisleri başlat

```bash
./scripts/start.sh

# Birkaç saniye bekle
sleep 5

# Web ayakta mı?
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5005/healthz
# Beklenen: 200

# Derin healthz (DB + Redis bağlantısı)
curl -s http://localhost:5005/healthz/deep
# Beklenen: {"status":"ok","checks":{"db":true,"redis":true}}

# Tüm servisler ayakta mı?
./scripts/start.sh status
# Beklenen: web ✓, worker ✓, beat ✓
```

**Sorun varsa logları kontrol:**

```bash
tail -100 logs/web.out
tail -100 logs/bmk-web.log
```

---

## 9. Kritik fonksiyonları manuel test et

### 9.1 Landing + Demo (auth yok)

- [ ] `https://bmk.app/` → sayfa açılıyor, footer "Beta hikayemiz" linki #testimonials'a gidiyor
- [ ] `https://bmk.app/demo` → 4 mock bölüm görünüyor (fiyat, SEO, AI, bildirim)
- [ ] `https://bmk.app/healthz` → "ok" döner

### 9.2 CSRF aktif mi?

Tarayıcıda `https://bmk.app/login` aç, sağ tık → Sayfa Kaynağını Görüntüle.
- [ ] `<meta name="csrf-token" content="...">` var mı?
- [ ] Login form'unda `<input type="hidden" name="csrf_token" value="...">` var mı?

Terminalde token'sız POST denemesi:
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  -d "email=x@y.com&password=test" \
  https://bmk.app/login
# Beklenen: 400 (CSRF reddetti)
```

### 9.3 Login

- [ ] Mevcut bir kullanıcı hesabıyla login dene → ✓ Dashboard'a yönlendiriyor
- [ ] Yanlış şifre → "Geçersiz e-posta veya şifre" mesajı

### 9.4 Admin paneli

- [ ] Admin hesabıyla login → /admin
- [ ] Sidebar'da bekleyen onay sayacı (varsa) doğru
- [ ] `/admin/settings` → GROQ alanı **input değil**, "✓ Yapılandırıldı" veya "✗ Eksik" durumu
- [ ] Settings'i kaydet → flash "Ayarlar kaydedildi"

### 9.5 Mevcut admin@bmk.com hesabını sil (eğer farklı admin yarattıysan)

```bash
# Yeni admin'in çalıştığından EMİN ol önce.
psql $DATABASE_URL -c "SELECT email, is_admin FROM users WHERE is_admin=true;"
# Beklenen: SENİN yeni admin email'in görünmeli.

# Sonra sil
psql $DATABASE_URL -c "DELETE FROM users WHERE email='admin@bmk.com';"
```

**ATLA** eğer mevcut admin@bmk.com'u kullanmaya devam ediyorsan (şifreyi değiştirdin).

### 9.6 Fiyat takibi

- [ ] `/tracked-products` → mevcut grupların listesi sorunsuz
- [ ] Yeni grup ekle (test URL'iyle) → ekleniyor, scan başlıyor
- [ ] Bir grup sil → silindi mesajı, liste güncel

### 9.7 AI Consultant (Enterprise plan)

- [ ] `/ai-consultant` → sayfa açılıyor, geçmiş raporlar görünüyor
- [ ] (Enterprise hesapla) "Rapor üret" → GROQ çağrısı çalışıyor, rapor geliyor
- [ ] Hata almıyorsan GROQ env doğru kurulmuş demektir

---

## 10. Deploy sonrası gözlem (ilk 24 saat)

- [ ] **Sentry'yi izle:** ilk birkaç saat içinde yeni hata gelirse acil incele
- [ ] **UptimeRobot'tan alert gelmedi:** /healthz ve /healthz/deep 200'de kalıyor
- [ ] **Worker log'unda 4 stratejik tarama saatlerinde aktivite var** (3:15, 9:15, 15:15, 21:15)
- [ ] **Kullanıcı şikâyeti yok** (login, form gönderme, ürün ekleme)

### CSRF kaynaklı muhtemel sorunlar

Eğer bir kullanıcı "form çalışmıyor" veya "İstek limiti aşıldı" gibi hatalar
raporlarsa, %90 ihtimalle **tarayıcı önbelleği** sebep:
- Eski sayfada eski (CSRF token'sız) form HTML'i cache'lenmiş
- Çözüm: kullanıcı Ctrl+Shift+R (hard refresh) → düzeldi
- Önleme: CDN/proxy varsa cache TTL'i kısalt

---

## 11. Acil geri dönüş planı

Bir şey bozulup geri almak istersen:

```bash
# 1) Servisleri durdur
./scripts/stop.sh

# 2) Kodu önceki commit'e geri al
git log --oneline -5
git checkout aed09df   # Faz 9 sonu

# 3) Eski bağımlılık seti
./.venv/bin/pip install -r requirements.txt
# (Lock dosyası eski commit'te yok, bu adım Faz 9'a kıyasla biraz farklı —
#  manuel olarak Flask-WTF ve alembic'i remove etmek istersen:)
# ./.venv/bin/pip uninstall -y Flask-WTF alembic

# 4) Veritabanını backup'tan restore et (sadece DB drift uyguladıysan)
gunzip -c backups/bmk_2026-XX-XX_XXXXXX.sql.gz | psql $DATABASE_URL

# 5) Servisleri başlat
./scripts/start.sh

# 6) Doğrula
curl -s http://localhost:5005/healthz
```

**Not:** Adım 4'ü sadece `alembic upgrade head` (Seçenek B) uyguladıysan yap.
`alembic stamp head` sadece tek bir satır yazdığından, geri dönmek için yeterli:

```bash
psql $DATABASE_URL -c "DROP TABLE IF EXISTS alembic_version;"
```

---

## 12. Tamamlama kontrolü

Tüm adımlar yeşil ise:

- [ ] CLAUDE.md'de Faz 10 notları gerçeği yansıtıyor (yerel oku, sunucuya çek)
- [ ] `.env`'den artık gerekmeyen `ADMIN_EMAIL`/`ADMIN_PASSWORD` satırlarını **sil**
  (admin hesabı DB'de saklı, env'de tutmana gerek yok — geri dönüş için zaten
  backup var)
- [ ] Sunucuda son commit:
  ```bash
  git log --oneline -1
  # Beklenen: e000041 chore(faz10c): polish ...
  ```
- [ ] Bu deploy notu bir yere tarih + uygulayan kişi yazısıyla saklandı
  (ileride "10 Haziran'da Faz 10'u uyguladık" referansı için)

---

## Soru/sorun varsa

`docs/operations.md` Bölüm 5-7'de ayrıntılı rehber var:
- Bölüm 5 — Servis durdu / DB corruption
- Bölüm 6 — Admin yönetimi
- Bölüm 7 — Alembic kullanımı

İlk kez deploy ediyorsan, **eldeki en güvenli yol Seçenek A** (alembic stamp head).
B'yi sonraki kesintide rahatça uygula.

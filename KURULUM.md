# 🚀 Yeni Bilgisayara Kurulum Rehberi

Bu dosya, BMK uygulamasını **sıfır bir bilgisayara** kurmak için adım adım yol
gösterir. (macOS varsayılır — mevcut geliştirme ortamıyla aynı.)

> Hızlı bakış: `setup.sh` ortamı kurar ama **Redis + PostgreSQL'i kurmaz** —
> onları önce sen kurarsın (Aşama 0). Gerisi büyük ölçüde otomatik.

---

## 📌 Özet kart (acelen varsa)

```bash
# 0. Ön gereksinimler (bir kez)
brew install git python@3.11 redis postgresql@15
brew services start redis postgresql@15

# 1. Kodu indir
git clone https://github.com/BilalMuharrem/SaaP.git && cd SaaP

# 2. Otomatik kurulum
./setup.sh

# 3. .env'i doldur (GROQ_API_KEY, ADMIN_EMAIL, ADMIN_PASSWORD)

# 4. DB'yi tamamla
./setup.sh

# 5. Çalıştır
./.venv/bin/python app.py        # → http://localhost:5005
```

---

## Aşama 0 — Ön gereksinimler (bir kez kurulur)

Yeni makinede yoksa Terminal'de:

```bash
# Homebrew (yoksa)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Git + Python + Redis + PostgreSQL
brew install git python@3.11 redis postgresql@15

# Redis ve PostgreSQL arka planda sürekli çalışsın
brew services start redis
brew services start postgresql@15
```

`setup.sh` bunları **kurmaz**, sadece var olduklarını varsayar. O yüzden bu aşama önce.

---

## Aşama 1 — Kodu indir

```bash
cd ~/Desktop
git clone https://github.com/BilalMuharrem/SaaP.git
cd SaaP
```

İlk `clone`'da GitHub kullanıcı adı + **token** sorabilir (keychain her makinede
ayrıdır). Token yoksa: https://github.com/settings/tokens/new → `repo` işaretle →
**Generate** → çıkan `ghp_...` kodunu password alanına yapıştır.

---

## Aşama 2 — Otomatik kurulum

```bash
./setup.sh
```

Kurar: sanal ortam (`.venv`) + tüm Python paketleri + Playwright tarayıcısı +
`.env` taslağı (+ `SECRET_KEY` otomatik üretimi).

Sonunda büyük ihtimalle **"KURULUM YARIM — önce .env'i doldur"** der (sırlar boş
olduğu için). Normaldir, sıradaki aşamaya geç.

> `setup.sh` idempotenttir — tekrar çalıştırmak güvenli, yapılmış adımları atlar.

---

## Aşama 3 — Sırları doldur (`.env`)

`.env` dosyasını aç (`open -e .env` veya PyCharm'da) ve şu 3 değeri doldur:

| Anahtar | Nereden |
|---|---|
| `GROQ_API_KEY` | **Eski bilgisayardaki `.env`'den kopyala** (en kolayı) veya https://console.groq.com/keys |
| `ADMIN_EMAIL` | Kendi e-posta adresin (ilk admin hesabı) |
| `ADMIN_PASSWORD` | Güçlü bir şifre belirle |

> `SECRET_KEY` ve `DATABASE_URL` otomatik dolduruldu — onlara dokunma.
>
> ⚠️ **`.env`'i asla GitHub'a veya Drive'a koyma** — sırlar içerir. Değerleri eski
> makineden yeni makineye elle / güvenli kanaldan taşı.

---

## Aşama 4 — Veritabanını tamamla

`.env` doldurulduktan sonra setup'ı **tekrar** çalıştır — bu sefer veritabanını
ve şemayı kurar:

```bash
./setup.sh
```

Artık **"✓ KURULUM TAMAM"** görmelisin.

---

## Aşama 5 — Çalıştır

```bash
./.venv/bin/python app.py
```

- Üç banner çıkar: **web + worker + beat** otomatik başlar (macOS'ta caffeinate de).
- Site: **http://localhost:5005**
- PyCharm kullanıyorsan: `app.py`'ye sağ tık → **Run**.

İlk girişte `.env`'deki `ADMIN_EMAIL` / `ADMIN_PASSWORD` ile admin olarak giriş yap.

---

## Aşama 6 — (İsteğe bağlı) Eski verini taşı

Yeni kurulum **boş** çalışır — kullanıcılar, takip edilen ürünler ve geçmiş
veritabanında saklanır, kodda değil. Eski verini istiyorsan:

**Eski bilgisayarda:**
```bash
pg_dump saas_db > ~/Desktop/bmk_yedek.sql
```

`.sql` dosyasını yeni bilgisayara taşı (USB/Drive), sonra **yeni bilgisayarda:**
```bash
psql saas_db < ~/Desktop/bmk_yedek.sql
```

Veri taşımak istemiyorsan bu aşamayı atla — sıfırdan temiz başlarsın.

---

## 🆘 Sık karşılaşılan sorunlar

| Belirti | Çözüm |
|---|---|
| `setup.sh` "psql yok" diyor | PostgreSQL kurulu/başlatılmış değil → Aşama 0'ı yap |
| Banner'da "Redis broker: ✗ KAPALI" | `brew services start redis` |
| `git clone` token soruyor | https://github.com/settings/tokens/new → `repo` → token al |
| Port 5005 dolu | `PORT=5006 ./.venv/bin/python app.py` |
| Analiz "pending"de takılıyor | worker çalışmıyordur → `app.py`'yi yeniden başlat (worker otomatik gelir) |

---

Daha teknik detay (mimari, konvansiyonlar) için: **`CLAUDE.md`** ve **`README.md`**.

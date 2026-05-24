"""
baslat.py — BMK SaaS App başlatıcı (macOS uyumlu, HOTFIX 1.36)

Yapılan kritik düzeltmeler:
  1) AirPlay Port Çakışması: Flask 5000 → 5005 portuna taşındı.
     macOS Monterey+ "AirPlay Receiver" servisi 5000'i tutuyor.
  2) Celery Worker Çökmesi: prefork havuzu macOS'ta SIGTERM yiyordu;
     `--pool=solo` ile tek thread'de stabil çalışıyor (procfile'da).
  3) Fork güvenliği: OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES en üstte.
"""

import os
import sys
import time
import platform

# ── 1) Fork güvenliği (Objective-C / Foundation çağrıları için) ──
# IMPORT'lardan SONRA, ama her şeyden ÖNCE set edilmeli.
os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

# ── 2) HOTFIX 1.36: Yeni port (env ile override'lanabilir) ──
APP_PORT = os.environ.get("PORT", "5005")

print("🧹 Eski hayalet süreçler ve takılı portlar temizleniyor...")
# Hem eski 5000 hem yeni 5005 (ve genel olarak hangi port aktifse) temizlensin
os.system(f"lsof -ti:{APP_PORT} | xargs kill -9 > /dev/null 2>&1")
os.system("lsof -ti:5000 | xargs kill -9 > /dev/null 2>&1")  # eski takılı kalanlar

# Arkada kalan Celery (işçi) hayaletlerini temizler
os.system("pkill -f 'celery' > /dev/null 2>&1")
os.system("pkill -f 'celerybeat' > /dev/null 2>&1")

# Eski caffeinate hayaletlerini de temizle (varsa)
os.system("pkill -f 'caffeinate' > /dev/null 2>&1")

# Honcho hayaleti
os.system("pkill -f 'honcho' > /dev/null 2>&1")

print("✨ Temizlik tamamlandı!")

# ── macOS App Nap Engelleyici ──
# macOS, terminal arka plana atıldığında veya sistem idle olduğunda
# Python/Celery süreçlerini "App Nap" ile uyutuyor. Bu da saatlik
# beat tetiklemelerinin kaçmasına sebep oluyor. `caffeinate` komutu
# sistemi uyanık tutar:
#   -d : display uyumasın
#   -i : idle sleep olmasın
#   -m : disk sleep olmasın
#   -s : AC güçteyken system sleep olmasın
#   -u : user active olarak işaretle (5s'ye kadar)
# Caffeinate arka plana atılır, Python ana süreç bitince kendiliğinden ölür.
if platform.system() == "Darwin":
    print("☕ macOS tespit edildi — caffeinate başlatılıyor (App Nap engelleyici)...")
    pid = os.getpid()
    os.system(f"caffeinate -d -i -m -s -u -w {pid} > /dev/null 2>&1 &")
    time.sleep(0.5)
    print("☕ caffeinate aktif — sistem uyanık tutulacak.")

print(f"🚀 Motor ateşleniyor... (PORT={APP_PORT}, Celery pool=solo)")
time.sleep(1)

# ── Honcho ile procfile'ı çalıştır ──
# HOTFIX 1.36: honcho yolu otomatik tespit ediliyor (proje .venv → Python 3.x).
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_HONCHO = os.path.join(PROJECT_DIR, ".venv", "bin", "honcho")
SYSTEM_HONCHOS = [
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/honcho",
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/honcho",
    "/usr/local/bin/honcho",
    "/opt/homebrew/bin/honcho",
]


def _find_honcho():
    if os.path.isfile(VENV_HONCHO) and os.access(VENV_HONCHO, os.X_OK):
        return VENV_HONCHO
    for p in SYSTEM_HONCHOS:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    # PATH'te honcho var mı son çare olarak
    return "honcho"


HONCHO = _find_honcho()
print(f"📦 honcho: {HONCHO}")

try:
    # PORT env'i procfile'a aktar (web: line üzerinde de zaten var ama belt-and-suspenders)
    os.environ["PORT"] = APP_PORT
    os.system(f'cd "{PROJECT_DIR}" && PORT={APP_PORT} {HONCHO} start -f procfile')
finally:
    # Honcho kapandığında caffeinate'i de temizle
    if platform.system() == "Darwin":
        os.system("pkill -f 'caffeinate.*-w' > /dev/null 2>&1")
        print("☕ caffeinate sonlandırıldı.")

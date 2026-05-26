#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/start.sh — BMK servislerini arka planda başlat.
#
# 3 process başlatır (her biri ayrı, arka planda):
#   • web    — Flask, port 5005
#   • worker — Celery worker (--pool=solo, macOS uyumlu)
#   • beat   — Celery beat (zamanlayıcı: 03:15/09:15/15:15/21:15 fiyat,
#              03:45/09:45/15:45/21:45 SEO)
#
# Logları logs/<servis>.out dosyalarına yazar (uygulama kendi logs/bmk-*.log
# dosyalarını yaratır — bunlar process'lerin stdout/stderr'i).
#
# macOS'ta caffeinate ekler — sistem uyusa bile Celery beat çalışmaya devam.
#
# KULLANIM:
#   ./scripts/start.sh           Tümünü başlat
#   ./scripts/start.sh status    Durum kontrol
#   ./scripts/start.sh restart   Önce durdur sonra başlat
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Proje kök dizinine geç (script konumunun bir üstü)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

# Sanal ortam
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python"
if [[ ! -x "${VENV_PYTHON}" ]]; then
    echo "❌ .venv/bin/python bulunamadı. Önce: python3.11 -m venv .venv && pip install -r requirements.txt"
    exit 1
fi

# PID + log dizinleri
PID_DIR="${PROJECT_DIR}/.pids"
LOG_DIR="${PROJECT_DIR}/logs"
mkdir -p "${PID_DIR}" "${LOG_DIR}"

# Port (env override edilebilir)
PORT="${PORT:-5005}"

# macOS App Nap engelleyici (caffeinate) — varsa
HAS_CAFFEINATE=0
if [[ "$(uname -s)" == "Darwin" ]] && command -v caffeinate >/dev/null 2>&1; then
    HAS_CAFFEINATE=1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Yardımcı fonksiyonlar
# ─────────────────────────────────────────────────────────────────────────────

_is_running() {
    # $1 = PID dosyası yolu
    local pid_file="$1"
    [[ -f "${pid_file}" ]] || return 1
    local pid
    pid="$(cat "${pid_file}")"
    [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

_start_service() {
    # $1 = servis adı, $2 = komut
    local name="$1"; shift
    local pid_file="${PID_DIR}/${name}.pid"
    local log_file="${LOG_DIR}/${name}.out"

    if _is_running "${pid_file}"; then
        echo "  ↪ ${name}: zaten çalışıyor (pid $(cat "${pid_file}"))"
        return 0
    fi

    # nohup ile başlat, stdout/stderr log dosyasına, PID kaydet
    nohup "$@" >> "${log_file}" 2>&1 &
    local pid=$!
    echo "${pid}" > "${pid_file}"
    sleep 0.3
    if _is_running "${pid_file}"; then
        echo "  ✓ ${name}: başladı (pid ${pid}, log: ${log_file})"
    else
        echo "  ❌ ${name}: BAŞLAMADI. Log: ${log_file}"
        return 1
    fi
}

cmd_status() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  BMK servis durumu"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    for name in web worker beat caffeinate; do
        local pid_file="${PID_DIR}/${name}.pid"
        if _is_running "${pid_file}"; then
            printf "  %-12s ✓ ÇALIŞIYOR   pid=%s\n" "${name}" "$(cat "${pid_file}")"
        else
            printf "  %-12s ✗ kapalı\n" "${name}"
        fi
    done
    echo ""
    # Redis ping
    if command -v redis-cli >/dev/null 2>&1; then
        if redis-cli ping >/dev/null 2>&1; then
            echo "  redis        ✓ ÇALIŞIYOR (PONG)"
        else
            echo "  redis        ✗ kapalı (brew services start redis)"
        fi
    fi
    echo ""
    echo "  Loglar: ${LOG_DIR}/{web,worker,beat}.out"
    echo "  PID:    ${PID_DIR}/"
    echo ""
}

cmd_start() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  BMK başlatılıyor (PORT=${PORT})"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Redis var mı kontrol et
    if command -v redis-cli >/dev/null 2>&1; then
        if ! redis-cli ping >/dev/null 2>&1; then
            echo "  ⚠️  Redis kapalı. Başlatılıyor..."
            if command -v brew >/dev/null 2>&1; then
                brew services start redis >/dev/null 2>&1 || true
                sleep 1
            fi
            if ! redis-cli ping >/dev/null 2>&1; then
                echo "  ❌ Redis hâlâ kapalı. Manuel başlat: brew services start redis"
                exit 1
            fi
            echo "  ✓ Redis başlatıldı"
        fi
    else
        echo "  ⚠️  redis-cli bulunamadı. Devam ediliyor (manuel doğrula)."
    fi

    # Eski takılı port'ları temizle (kontrollü)
    if lsof -ti:"${PORT}" >/dev/null 2>&1; then
        echo "  ⚠️  Port ${PORT} dolu — eski process kapatılıyor..."
        lsof -ti:"${PORT}" | xargs kill -9 2>/dev/null || true
        sleep 0.3
    fi

    # OBJC fork güvenliği (macOS)
    export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
    export PORT

    # 1) Web
    _start_service web \
        "${VENV_PYTHON}" "${PROJECT_DIR}/app.py"

    # 2) Worker
    _start_service worker \
        "${VENV_PYTHON}" -m celery -A extensions.celery worker \
            --pool=solo --loglevel=info

    # 3) Beat
    _start_service beat \
        "${VENV_PYTHON}" -m celery -A extensions.celery beat \
            --loglevel=info \
            --schedule="${PROJECT_DIR}/celerybeat-schedule"

    # 4) Caffeinate (macOS — App Nap engelleyici)
    if [[ "${HAS_CAFFEINATE}" == "1" ]]; then
        local caf_pid_file="${PID_DIR}/caffeinate.pid"
        if ! _is_running "${caf_pid_file}"; then
            nohup caffeinate -d -i -m -s >/dev/null 2>&1 &
            echo $! > "${caf_pid_file}"
            echo "  ✓ caffeinate: başladı (App Nap engelleyici)"
        fi
    fi

    echo ""
    echo "  Hepsi başladı. Web: http://localhost:${PORT}"
    echo "  Logları izle:   tail -f logs/{web,worker,beat}.out"
    echo "  Durum:          ./scripts/start.sh status"
    echo "  Durdur:         ./scripts/stop.sh"
    echo ""
}

cmd_restart() {
    "${PROJECT_DIR}/scripts/stop.sh" || true
    sleep 1
    cmd_start
}

# ─────────────────────────────────────────────────────────────────────────────
# Komut dağıtıcı
# ─────────────────────────────────────────────────────────────────────────────
case "${1:-start}" in
    start)   cmd_start ;;
    status)  cmd_status ;;
    restart) cmd_restart ;;
    *)
        echo "Kullanım: $0 [start|status|restart]"
        exit 1
        ;;
esac

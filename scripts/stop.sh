#!/usr/bin/env bash
# scripts/stop.sh — BMK servislerini düzgün şekilde kapat.
#
# Sırasıyla SIGTERM → 3 saniye bekle → SIGKILL (kalan varsa).
# .pids/ dosyalarını temizler.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PID_DIR="${PROJECT_DIR}/.pids"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  BMK servisleri durduruluyor"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ ! -d "${PID_DIR}" ]]; then
    echo "  ⚠️  ${PID_DIR} yok — hiçbir servis başlatılmamış."
    exit 0
fi

# Beat → worker → web → caffeinate sırası (bağımlılık tersine)
for name in beat worker web caffeinate; do
    pid_file="${PID_DIR}/${name}.pid"
    [[ -f "${pid_file}" ]] || continue

    pid="$(cat "${pid_file}")"
    if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
        echo "  ↪ ${name}: zaten kapalı"
        rm -f "${pid_file}"
        continue
    fi

    echo "  ↪ ${name}: SIGTERM (pid ${pid})..."
    kill -TERM "${pid}" 2>/dev/null || true

    # 3 saniye bekle, gerekirse SIGKILL
    for _ in 1 2 3; do
        sleep 1
        if ! kill -0 "${pid}" 2>/dev/null; then
            break
        fi
    done

    if kill -0 "${pid}" 2>/dev/null; then
        echo "    ⚠️  hâlâ ayakta, SIGKILL..."
        kill -KILL "${pid}" 2>/dev/null || true
        sleep 0.3
    fi

    if kill -0 "${pid}" 2>/dev/null; then
        echo "    ❌ pid ${pid} kapatılamadı"
    else
        echo "    ✓ kapandı"
    fi
    rm -f "${pid_file}"
done

# Hayalet celery process'leri (PID dosyasız kalmış)
GHOST_CELERY=$(pgrep -f "celery.*-A extensions.celery" 2>/dev/null || true)
if [[ -n "${GHOST_CELERY}" ]]; then
    echo "  ⚠️  Hayalet celery process'leri tespit edildi: ${GHOST_CELERY}"
    echo "${GHOST_CELERY}" | xargs kill -TERM 2>/dev/null || true
    sleep 1
    echo "${GHOST_CELERY}" | xargs kill -KILL 2>/dev/null || true
    echo "  ✓ Hayaletler temizlendi"
fi

echo ""
echo "  Tüm servisler kapandı."
echo ""

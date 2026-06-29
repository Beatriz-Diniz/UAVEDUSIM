#!/usr/bin/env bash
set -u
finish() {
    local status="$?"
    trap - EXIT
    echo
    echo "[UAVEDUSIM] Comando finalizado com codigo: $status"
    rm -f "$0" >/dev/null 2>&1 || true
    if [ "${UAVEDUSIM_TERMINAL_HOLD:-1}" = "1" ]; then
        echo "[UAVEDUSIM] Pressione ENTER para fechar este terminal."
        if [ -t 0 ]; then
            read -r _ || true
        else
            sleep 3600
        fi
    else
        echo "[UAVEDUSIM] Fechando terminal automaticamente."
    fi
    exit "$status"
}
trap finish EXIT
trap 'exit 130' INT TERM HUP
exec > >(tee -a "${UAVEDUSIM_LOG:-/tmp/uavedusim.log}") 2>&1
cd "${UAVEDUSIM_APP_DIR:?}" || exit 1
echo "[UAVEDUSIM] ${UAVEDUSIM_TITLE:-Terminal}"
echo "[UAVEDUSIM] Log: ${UAVEDUSIM_LOG:-}"
echo "[UAVEDUSIM] Session: ${UAVEDUSIM_SESSION_ID:-}"
echo
set +e
cd '/home/beatrizdiniz/UAVEDUSIM' && chmod +x './QGroundControl-x86_64.AppImage' && ./QGroundControl-x86_64.AppImage
status=$?
exit "$status"

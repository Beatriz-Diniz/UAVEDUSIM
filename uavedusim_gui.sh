#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PX4_DIR="$APP_DIR/PX4-Autopilot"
QGC_APP="$APP_DIR/QGroundControl-x86_64.AppImage"

if ! command -v zenity >/dev/null 2>&1; then
    echo "zenity nao encontrado. Instale com: sudo dnf install zenity"
    exit 1
fi

open_terminal() {
    local title="$1"
    local command="$2"

    if command -v kitty >/dev/null 2>&1; then
        kitty --title "$title" bash -lc "$command; exec bash" &
    elif command -v gnome-terminal >/dev/null 2>&1; then
        gnome-terminal --title="$title" -- bash -lc "$command; exec bash" &
    elif command -v konsole >/dev/null 2>&1; then
        konsole --new-tab --workdir "$APP_DIR" -e bash -lc "$command; exec bash" &
    elif command -v xterm >/dev/null 2>&1; then
        xterm -T "$title" -e bash -lc "$command; exec bash" &
    else
        zenity --error --text="Nenhum terminal suportado encontrado: kitty, gnome-terminal, konsole ou xterm."
        return 1
    fi
}

run_cleanup() {
    if [ -x "$APP_DIR/run_with_cleanup.sh" ]; then
        open_terminal "UAVEDUSIM Cleanup" "cd '$APP_DIR' && ./run_with_cleanup.sh"
    elif [ -x "$APP_DIR/cleanup_px4_gazebo.sh" ]; then
        open_terminal "UAVEDUSIM Cleanup" "cd '$APP_DIR' && ./cleanup_px4_gazebo.sh"
    else
        zenity --error --text="Nao encontrei run_with_cleanup.sh ou cleanup_px4_gazebo.sh executavel."
    fi
}

open_qgc() {
    if [ ! -f "$QGC_APP" ]; then
        zenity --error --text="Nao encontrei: $QGC_APP"
        return 1
    fi

    chmod +x "$QGC_APP"
    open_terminal "QGroundControl" "cd '$APP_DIR' && ./QGroundControl-x86_64.AppImage"
}

run_px4() {
    local model

    model="$(zenity --list \
        --title="UAVEDUSIM" \
        --text="Escolha o modelo PX4/Gazebo:" \
        --column="Modelo" \
        "gz_x500" \
        "gz_x500_vision" \
        "gz_x500_uavjamsim" \
        --height=260 --width=360)"

    [ -z "${model:-}" ] && return 0

    if [ ! -d "$PX4_DIR" ]; then
        zenity --error --text="Nao encontrei a pasta: $PX4_DIR"
        return 1
    fi

    open_terminal "PX4 SITL - $model" "cd '$PX4_DIR' && make px4_sitl $model"
}

while true; do
    choice="$(zenity --list \
        --title="UAVEDUSIM" \
        --text="O que voce quer abrir?" \
        --column="Opcao" \
        "Abrir PX4/Gazebo" \
        "Abrir QGroundControl" \
        "Limpar processos PX4/Gazebo" \
        "Sair" \
        --height=300 --width=420)"

    case "${choice:-}" in
        "Abrir PX4/Gazebo")
            run_px4
            ;;
        "Abrir QGroundControl")
            open_qgc
            ;;
        "Limpar processos PX4/Gazebo")
            run_cleanup
            ;;
        "Sair" | "")
            exit 0
            ;;
    esac
done

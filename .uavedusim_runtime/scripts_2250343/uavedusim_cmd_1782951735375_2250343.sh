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
load_gazebo_env() {
    local setup_file=""
    local source_status=0

    if [ -f "$HOME/gazebo_workspace/install/setup.bash" ]; then
        setup_file="$HOME/gazebo_workspace/install/setup.bash"
        echo "[UAVEDUSIM] Carregando Gazebo: $setup_file"
        set +u
        . "$setup_file"
        source_status=$?
        set -u
        if [ "$source_status" -ne 0 ]; then
            echo "[UAVEDUSIM] ERRO: falha ao carregar $setup_file"
            exit "$source_status"
        fi
    elif [ -f "$HOME/gazebo_workspace/install/setup.sh" ]; then
        setup_file="$HOME/gazebo_workspace/install/setup.sh"
        echo "[UAVEDUSIM] Carregando Gazebo: $setup_file"
        set +u
        . "$setup_file"
        source_status=$?
        set -u
        if [ "$source_status" -ne 0 ]; then
            echo "[UAVEDUSIM] ERRO: falha ao carregar $setup_file"
            exit "$source_status"
        fi
    elif [ -f "$HOME/gazebo_workspace/install/setup.zsh" ]; then
        setup_file="$HOME/gazebo_workspace/install/setup.zsh"
        echo "[UAVEDUSIM] Carregando Gazebo via zsh: $setup_file"
        if command -v zsh >/dev/null 2>&1; then
            while IFS= read -r -d "" env_line; do
                case "$env_line" in
                    *=*) export "$env_line" ;;
                esac
            done < <(zsh -lc "source \"$setup_file\" >/dev/null && env -0")
        else
            echo "[UAVEDUSIM] ERRO: encontrei setup.zsh, mas zsh nao esta instalado."
            exit 1
        fi
    else
        echo "[UAVEDUSIM] Aviso: setup do gazebo_workspace nao encontrado. Continuando com ambiente atual."
    fi

    export GZ_IP="${GZ_IP:-127.0.0.1}"
}
load_gazebo_env

cd '/home/beatrizdiniz/UAVEDUSIM/PX4-Autopilot'
if [ ! -d 'Tools/simulation/gz/models/x500' ]; then
    echo '[UAVEDUSIM] Submodulo Tools/simulation/gz ausente. Inicializando submodulos...'
    git submodule sync --recursive || true
    git submodule update --init --recursive Tools/simulation/gz || git submodule update --init --recursive || true
fi
cd '/home/beatrizdiniz/UAVEDUSIM'
echo '[UAVEDUSIM] Garantindo links simbolicos e airframe do UAVJamSim...'
ls -l '/home/beatrizdiniz/UAVEDUSIM/PX4-Autopilot/Tools/simulation/gz/models/x500_uavjamsim' 2>/dev/null || true
ls -l '/home/beatrizdiniz/UAVEDUSIM/PX4-Autopilot/Tools/simulation/gz/worlds/default.sdf' 2>/dev/null || true
ls -l '/home/beatrizdiniz/UAVEDUSIM/PX4-Autopilot/ROMFS/px4fmu_common/init.d-posix/airframes/4020_gz_x500_uavjamsim' 2>/dev/null || true
if [ 'gz_x500_uavjamsim' = 'gz_x500_uavjamsim' ]; then
    if [ ! -f '/home/beatrizdiniz/UAVEDUSIM/PX4-Autopilot/ROMFS/px4fmu_common/init.d-posix/airframes/4020_gz_x500_uavjamsim' ]; then
        echo '[UAVEDUSIM] ERRO: airframe 4020_gz_x500_uavjamsim nao foi instalado no ROMFS.'
        exit 1
    fi
    if [ -L '/home/beatrizdiniz/UAVEDUSIM/PX4-Autopilot/ROMFS/px4fmu_common/init.d-posix/airframes/4020_gz_x500_uavjamsim' ]; then
        echo '[UAVEDUSIM] ERRO: airframe 4020_gz_x500_uavjamsim ainda e link simbolico. Ele precisa ser arquivo real.'
        exit 1
    fi
fi
cd '/home/beatrizdiniz/UAVEDUSIM/PX4-Autopilot'
PX4_CONSOLE_FIFO='/home/beatrizdiniz/UAVEDUSIM/.uavedusim_runtime/px4_console_2250343.fifo'
rm -f "$PX4_CONSOLE_FIFO"
mkfifo "$PX4_CONSOLE_FIFO"
echo "[UAVEDUSIM] Console PX4 por FIFO: $PX4_CONSOLE_FIFO"
echo '[UAVEDUSIM] Executando sem limpar build: make px4_sitl gz_x500_uavjamsim PX4_GZ_WORLD=default'
( while true; do cat "$PX4_CONSOLE_FIFO"; done ) | make px4_sitl 'gz_x500_uavjamsim' PX4_GZ_WORLD='default'
status=$?
exit "$status"

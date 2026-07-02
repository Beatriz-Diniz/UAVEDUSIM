#!/usr/bin/env bash
# Launcher principal do UAVEDUSIM GUI.
# Inicia a interface Python (uavedusim_gui_app.py) e gerencia processos filhos.
set -Eeuo pipefail

# Diretórios e caminhos dos componentes do simulador
APP_DIR="${UAVEDUSIM_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
PX4_DIR="$APP_DIR/PX4-Autopilot"
QGC_APP="$APP_DIR/QGroundControl-x86_64.AppImage"
QGC_EXTRACTED="$APP_DIR/squashfs-root/usr/bin/QGroundControl"
LOG="$APP_DIR/uavedusim_gui.log"

# Recria o log e redireciona stdout/stderr para ele
mkdir -p "$APP_DIR"
rm -f "$LOG"
touch "$LOG"
exec > >(tee -a "$LOG") 2>&1

# Permite abrir janelas tanto em Wayland quanto em X11
export GDK_BACKEND="${GDK_BACKEND:-wayland,x11}"

# Identificadores de sessão; cada execução tem arquivos de PID e scripts próprios
UAVEDUSIM_SESSION_ID="${UAVEDUSIM_SESSION_ID:-$$}"
UAVEDUSIM_RUNTIME_DIR="$APP_DIR/.uavedusim_runtime"
UAVEDUSIM_PID_FILE="$UAVEDUSIM_RUNTIME_DIR/terminals_${UAVEDUSIM_SESSION_ID}.pids"
UAVEDUSIM_SCRIPT_DIR="$UAVEDUSIM_RUNTIME_DIR/scripts_${UAVEDUSIM_SESSION_ID}"
UAVEDUSIM_EXIT_CLEANUP_RAN=0

# Cria diretórios de runtime e inicializa o arquivo de PIDs vazio
mkdir -p "$UAVEDUSIM_RUNTIME_DIR" "$UAVEDUSIM_SCRIPT_DIR"
: > "$UAVEDUSIM_PID_FILE"


# Imprime mensagem com timestamp no stdout (e no log via tee acima)
log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

# Envia notificação de desktop via notify-send quando disponível.
notify_msg() {
    local msg="$1"
    if command -v notify-send >/dev/null 2>&1; then
        notify-send "UAVEDUSIM" "$msg" >/dev/null 2>&1 || true
    fi
}

# Registra erro no log, notifica e exibe diálogo zenity se disponível
show_error() {
    local msg="$1"
    log "ERRO: $msg"
    notify_msg "$msg"
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="UAVEDUSIM" --text="$msg\n\nLog: $LOG" >/dev/null 2>&1 || true
    fi
}


# Detecta gerenciador de pacotes e instala dependencias mínimas da interface.
detect_package_manager() {
    if command -v dnf >/dev/null 2>&1; then
        printf '%s\n' "dnf"
    elif command -v apt-get >/dev/null 2>&1; then
        printf '%s\n' "apt"
    elif command -v pacman >/dev/null 2>&1; then
        printf '%s\n' "pacman"
    elif command -v zypper >/dev/null 2>&1; then
        printf '%s\n' "zypper"
    elif command -v apk >/dev/null 2>&1; then
        printf '%s\n' "apk"
    elif command -v xbps-install >/dev/null 2>&1; then
        printf '%s\n' "xbps"
    else
        printf '%s\n' "unknown"
    fi
}

# Executa instalação usando sudo/doas quando necessário.
install_system_packages() {
    local manager="$1"
    shift

    local packages=("$@")
    local sudo_cmd=()

    if [ "${#packages[@]}" -eq 0 ]; then
        return 0
    fi

    if [ "$(id -u)" -ne 0 ]; then
        if command -v sudo >/dev/null 2>&1; then
            sudo_cmd=(sudo)
        elif command -v doas >/dev/null 2>&1; then
            sudo_cmd=(doas)
        else
            show_error "Dependencias ausentes: ${packages[*]}. Instale manualmente ou instale sudo/doas para permitir instalação automática."
            return 1
        fi
    fi

    log "Dependencias ausentes detectadas: ${packages[*]}"

    if [ "${UAVEDUSIM_AUTO_INSTALL_DEPS:-1}" != "1" ]; then
        show_error "Dependencias ausentes: ${packages[*]}. Instalação automática desativada por UAVEDUSIM_AUTO_INSTALL_DEPS=0."
        return 1
    fi

    log "Instalando dependencias pelo gerenciador: $manager"

    case "$manager" in
        dnf)
            "${sudo_cmd[@]}" dnf install -y "${packages[@]}"
            ;;
        apt)
            "${sudo_cmd[@]}" apt-get update
            "${sudo_cmd[@]}" apt-get install -y "${packages[@]}"
            ;;
        pacman)
            "${sudo_cmd[@]}" pacman -S --needed --noconfirm "${packages[@]}"
            ;;
        zypper)
            "${sudo_cmd[@]}" zypper --non-interactive install --no-recommends "${packages[@]}"
            ;;
        apk)
            "${sudo_cmd[@]}" apk add --no-cache "${packages[@]}"
            ;;
        xbps)
            "${sudo_cmd[@]}" xbps-install -Sy "${packages[@]}"
            ;;
        *)
            show_error "Não reconheci o gerenciador de pacotes. Instale manualmente: python3, tkinter, git, cmake, make, gcc/g++, ninja, zsh e um terminal grafico."
            return 1
            ;;
    esac
}

# Verifica dependencias e tenta instalar os pacotes corretos para a distro.
ensure_system_dependencies() {
    local manager
    manager="$(detect_package_manager)"

    local -a py_pkg=()
    local -a tk_pkg=()
    local -a git_pkg=(git)
    local -a cmake_pkg=(cmake)
    local -a make_pkg=(make)
    local -a compiler_pkg=()
    local -a ninja_pkg=()
    local -a zsh_pkg=(zsh)
    local -a terminal_pkg=(kitty)
    local -a packages=()

    case "$manager" in
        dnf)
            py_pkg=(python3)
            tk_pkg=(python3-tkinter tk)
            compiler_pkg=(gcc gcc-c++)
            ninja_pkg=(ninja-build)
            ;;
        apt)
            py_pkg=(python3)
            tk_pkg=(python3-tk tk)
            compiler_pkg=(gcc g++)
            ninja_pkg=(ninja-build)
            ;;
        pacman)
            py_pkg=(python)
            tk_pkg=(tk)
            compiler_pkg=(gcc)
            ninja_pkg=(ninja)
            ;;
        zypper)
            py_pkg=(python3)
            tk_pkg=(python3-tk tk)
            compiler_pkg=(gcc gcc-c++)
            ninja_pkg=(ninja)
            ;;
        apk)
            py_pkg=(python3)
            tk_pkg=(py3-tkinter tk)
            compiler_pkg=(gcc g++ musl-dev)
            ninja_pkg=(ninja)
            ;;
        xbps)
            py_pkg=(python3)
            tk_pkg=(python3-tkinter tk)
            compiler_pkg=(gcc)
            ninja_pkg=(ninja)
            ;;
        *)
            py_pkg=()
            tk_pkg=()
            compiler_pkg=()
            ninja_pkg=()
            terminal_pkg=()
            ;;
    esac

    add_pkg() {
        local pkg
        local existing

        for pkg in "$@"; do
            [ -n "$pkg" ] || continue

            for existing in "${packages[@]:-}"; do
                if [ "$existing" = "$pkg" ]; then
                    continue 2
                fi
            done

            packages+=("$pkg")
        done
    }

    if ! command -v python3 >/dev/null 2>&1; then
        add_pkg "${py_pkg[@]}"
    fi

    if ! command -v git >/dev/null 2>&1; then
        add_pkg "${git_pkg[@]}"
    fi

    if ! command -v cmake >/dev/null 2>&1; then
        add_pkg "${cmake_pkg[@]}"
    fi

    if ! command -v make >/dev/null 2>&1; then
        add_pkg "${make_pkg[@]}"
    fi

    if ! command -v gcc >/dev/null 2>&1 || ! command -v g++ >/dev/null 2>&1; then
        add_pkg "${compiler_pkg[@]}"
    fi

    if ! command -v ninja >/dev/null 2>&1 && ! command -v ninja-build >/dev/null 2>&1; then
        add_pkg "${ninja_pkg[@]}"
    fi

    if ! command -v zsh >/dev/null 2>&1; then
        add_pkg "${zsh_pkg[@]}"
    fi

    if ! command -v kitty >/dev/null 2>&1 &&
       ! command -v foot >/dev/null 2>&1 &&
       ! command -v alacritty >/dev/null 2>&1 &&
       ! command -v gnome-terminal >/dev/null 2>&1; then
        add_pkg "${terminal_pkg[@]}"
    fi

    if command -v python3 >/dev/null 2>&1; then
        if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
            add_pkg "${tk_pkg[@]}"
        fi
    fi

    install_system_packages "$manager" "${packages[@]}"

    if ! command -v python3 >/dev/null 2>&1; then
        show_error "python3 continua ausente após a instalação das dependencias."
        return 1
    fi

    if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
        show_error "Tkinter continua ausente após a instalação. Instale o pacote Tkinter correspondente a sua distribuicao."
        return 1
    fi

    log "Dependencias mínimas da interface verificadas."
}



# Encerra todos os terminais registrados: TERM primeiro, KILL após 1s para os restantes
kill_tracked_terminals() {
    local pid=""
    local title=""

    [ -f "$UAVEDUSIM_PID_FILE" ] || return 0

    log "Encerrando terminais abertos pela sessão $UAVEDUSIM_SESSION_ID"

    while IFS="$(printf '\t')" read -r pid title; do
        [ -n "$pid" ] || continue

        case "$pid" in
            *[!0-9]*)
                continue
                ;;
        esac

        if kill -0 "$pid" >/dev/null 2>&1; then
            log "Enviando TERM para terminal: pid=$pid title=${title:-terminal}"
            kill -TERM -- "-$pid" >/dev/null 2>&1 || true
            kill -TERM "$pid" >/dev/null 2>&1 || true
        fi
    done < "$UAVEDUSIM_PID_FILE"

    sleep 1

    while IFS="$(printf '\t')" read -r pid title; do
        [ -n "$pid" ] || continue

        case "$pid" in
            *[!0-9]*)
                continue
                ;;
        esac

        if kill -0 "$pid" >/dev/null 2>&1; then
            log "Forcando KILL para terminal restante: pid=$pid title=${title:-terminal}"
            kill -KILL -- "-$pid" >/dev/null 2>&1 || true
            kill -KILL "$pid" >/dev/null 2>&1 || true
        fi
    done < "$UAVEDUSIM_PID_FILE"
}

# Encerra processos PX4/Gazebo/QGC ao fechar; usa cleanup_px4_gazebo.sh se disponível
cleanup_uavedusim_processes_on_exit() {
    if [ "${UAVEDUSIM_KILL_PROCESSES_ON_EXIT:-1}" != "1" ]; then
        log "Cleanup de processos no encerramento desativado por UAVEDUSIM_KILL_PROCESSES_ON_EXIT=0"
        return 0
    fi

    log "Encerrando processos PX4/Gazebo/QGroundControl do UAVEDUSIM"

    if [ -x "$APP_DIR/cleanup_px4_gazebo.sh" ]; then
        (
            cd "$APP_DIR" || exit 0
            ./cleanup_px4_gazebo.sh
        ) >> "$LOG" 2>&1 || true
    else
        pkill -TERM -f 'px4_sitl_default/bin/px4|PX4-Autopilot/build/.*/bin/px4|gz sim|gzserver|gzclient|MicroXRCEAgent|QGroundControl' >/dev/null 2>&1 || true
        sleep 1
        pkill -KILL -f 'px4_sitl_default/bin/px4|PX4-Autopilot/build/.*/bin/px4|gz sim|gzserver|gzclient|MicroXRCEAgent|QGroundControl' >/dev/null 2>&1 || true
    fi
}

# Handler de EXIT: garante que terminais e processos são encerrados uma única vez
uavedusim_on_exit() {
    local code="$?"

    trap - EXIT INT TERM HUP ERR
    set +e

    if [ "$UAVEDUSIM_EXIT_CLEANUP_RAN" = "1" ]; then
        exit "$code"
    fi

    UAVEDUSIM_EXIT_CLEANUP_RAN=1

    log "Encerrando UAVEDUSIM GUI. Código=$code"

    kill_tracked_terminals
    cleanup_uavedusim_processes_on_exit

    rm -f "$UAVEDUSIM_PID_FILE" >/dev/null 2>&1 || true
    rm -rf "$UAVEDUSIM_SCRIPT_DIR" >/dev/null 2>&1 || true

    exit "$code"
}

# Handler para sinais INT/TERM/HUP: loga e sai com código 130
uavedusim_on_signal() {
    local sig="${1:-SIGNAL}"
    log "Sinal recebido: $sig. Encerrando UAVEDUSIM GUI..."
    exit 130
}

# Registra handlers: ERR mostra linha com falha; EXIT/INT/TERM/HUP fazem cleanup
trap 'code=$?; show_error "Falha na linha $LINENO. Comando: $BASH_COMMAND. Código: $code"; exit $code' ERR
trap 'uavedusim_on_exit' EXIT
trap 'uavedusim_on_signal INT' INT
trap 'uavedusim_on_signal TERM' TERM
trap 'uavedusim_on_signal HUP' HUP


log "Iniciando UAVEDUSIM GUI"
log "APP_DIR=$APP_DIR"
log "Sessão: XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-vazio}, WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-vazio}, DISPLAY=${DISPLAY:-vazio}"

# Verifica e instala dependencias mínimas em distros suportadas.
ensure_system_dependencies

if [ ! -f "$APP_DIR/uavedusim_gui_app.py" ]; then
    show_error "Não encontrei $APP_DIR/uavedusim_gui_app.py. Copie o arquivo da interface persistente para a pasta UAVEDUSIM."
    exit 1
fi

# Lança a interface GUI Python passando todas as variáveis de sessão via ambiente
set +e
APP_DIR="$APP_DIR" PX4_DIR="$PX4_DIR" QGC_APP="$QGC_APP" QGC_EXTRACTED="$QGC_EXTRACTED" LOG="$LOG" UAVEDUSIM_SESSION_ID="$UAVEDUSIM_SESSION_ID" UAVEDUSIM_RUNTIME_DIR="$UAVEDUSIM_RUNTIME_DIR" UAVEDUSIM_PID_FILE="$UAVEDUSIM_PID_FILE" UAVEDUSIM_SCRIPT_DIR="$UAVEDUSIM_SCRIPT_DIR" python3 "$APP_DIR/uavedusim_gui_app.py"
gui_status=$?
set -e

exit "$gui_status"
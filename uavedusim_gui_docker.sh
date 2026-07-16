#!/usr/bin/env bash
# Launcher principal do UAVEDUSIM GUI.
# Inicia a interface Python (uavedusim_gui_app.py) e gerencia processos filhos.
set -Eeuo pipefail

# ============================================================================
# ORQUESTRACAO DOCKER
# Roda o simulador sempre dentro de um container Ubuntu 24.04 persistente,
# independente da distro/versao do host (Fedora, Ubuntu, Arch, etc.):
#   - Se a imagem nao existe, constroi (docker build) na primeira vez.
#   - Se o container nao existe, cria (docker run -d ... sleep infinity),
#     SEM --rm: o container fica parado quando nao esta em uso, mas nunca
#     e destruido automaticamente, entao pacotes instalados nele persistem.
#   - Se o container existe mas esta parado, so da "docker start".
#   - Se o container ja esta rodando, so reaproveita.
# O repositorio (esta pasta) e montado (bind mount) no mesmo caminho dentro
# do container, entao o codigo-fonte, builds do PX4 e logs continuam no HOST
# mesmo que o container seja removido manualmente algum dia.
# Para desativar esta orquestracao e rodar direto no host: UAVEDUSIM_USE_DOCKER=0
# ============================================================================
UAVEDUSIM_USE_DOCKER="${UAVEDUSIM_USE_DOCKER:-1}"
# Nomes de imagem/container incluem o UID do usuario do host: isso evita
# colisao caso o MESMO computador seja compartilhado por contas diferentes
# (comum em laboratorios), e garante que a imagem seja sempre construida com
# o UID/GID corretos para quem esta rodando o script.
UAVEDUSIM_DOCKER_IMAGE="${UAVEDUSIM_DOCKER_IMAGE:-uavedusim:uid$(id -u)}"
UAVEDUSIM_DOCKER_CONTAINER="${UAVEDUSIM_DOCKER_CONTAINER:-uavedusim-sim-uid$(id -u)}"

# /.dockerenv so existe DENTRO de um container Docker: usamos isso para saber
# se este script ja esta rodando la dentro (e assim nao tentar aninhar de novo).
if [ "$UAVEDUSIM_USE_DOCKER" = "1" ] && [ ! -f /.dockerenv ]; then
    _script_path="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/$(basename -- "${BASH_SOURCE[0]}")"
    _script_dir="$(dirname -- "$_script_path")"

    # Se o comando "docker" nem existe, tenta instalar automaticamente antes
    # de qualquer outra coisa (cross-distro: dnf/apt/pacman/zypper/apk).
    if ! command -v docker >/dev/null 2>&1; then
        echo "[uavedusim_gui] Docker nao encontrado. Tentando instalar automaticamente..."
        _dpm=""
        if command -v dnf >/dev/null 2>&1; then _dpm="dnf"
        elif command -v apt-get >/dev/null 2>&1; then _dpm="apt"
        elif command -v pacman >/dev/null 2>&1; then _dpm="pacman"
        elif command -v zypper >/dev/null 2>&1; then _dpm="zypper"
        elif command -v apk >/dev/null 2>&1; then _dpm="apk"
        elif [ -f /etc/arch-release ]; then _dpm="pacman"
        fi

        _sudo_install=()
        if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
            _sudo_install=(sudo)
        fi

        case "$_dpm" in
            dnf)
                "${_sudo_install[@]}" dnf install -y moby-engine 2>/dev/null \
                    || "${_sudo_install[@]}" dnf install -y docker
                ;;
            apt)
                "${_sudo_install[@]}" apt-get update
                "${_sudo_install[@]}" apt-get install -y docker.io
                ;;
            pacman)
                "${_sudo_install[@]}" pacman -Sy --needed --noconfirm docker
                
                # Habilita e inicia o serviço Docker (essencial no Arch/CachyOS)
                "${_sudo_install[@]}" systemctl enable --now docker >/dev/null 2>&1 || true
                
                # Adiciona o usuário ao grupo docker (evita usar sudo sempre)
                if getent group docker >/dev/null 2>&1; then
                    "${_sudo_install[@]}" usermod -aG docker "$USER" >/dev/null 2>&1 || true
                fi
                
                echo "[uavedusim_gui] Docker instalado. Pode ser necessário relogar para usar sem sudo."
                ;;
            zypper)
                "${_sudo_install[@]}" zypper --non-interactive install docker
                ;;
            apk)
                "${_sudo_install[@]}" apk add --no-cache docker
                ;;
            *)
                echo "[uavedusim_gui] Nao reconheci o gerenciador de pacotes para instalar o Docker. Instale manualmente: https://docs.docker.com/engine/install/" >&2
                ;;
        esac

        # Inicia o servico do Docker (best-effort; alguns ambientes nao usam systemd)
        "${_sudo_install[@]}" systemctl enable --now docker >/dev/null 2>&1 || true

        if ! command -v docker >/dev/null 2>&1; then
            echo "[uavedusim_gui] Nao foi possivel instalar o Docker automaticamente. Seguindo sem container." >&2
            UAVEDUSIM_USE_DOCKER=0
        else
            echo "[uavedusim_gui] Docker instalado com sucesso."
        fi
    fi

    # Detecta o gerenciador de pacotes da distro do HOST (cross-distro:
    # dnf/apt/pacman/zypper/apk). Reaproveitado abaixo para instalar o
    # 'xauth' e o 'git-lfs' automaticamente, do mesmo jeito que o Docker
    # e instalado acima.
    _uavedusim_detect_dpm() {
        if command -v dnf >/dev/null 2>&1; then printf '%s' "dnf"
        elif command -v apt-get >/dev/null 2>&1; then printf '%s' "apt"
        elif command -v pacman >/dev/null 2>&1; then printf '%s' "pacman"
        elif command -v zypper >/dev/null 2>&1; then printf '%s' "zypper"
        elif command -v apk >/dev/null 2>&1; then printf '%s' "apk"
        else printf '%s' ""
        fi
    }

    _uavedusim_sudo=()
    if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
        _uavedusim_sudo=(sudo)
    fi

    # Garante que "xauth" esta disponivel no HOST. E' usado logo abaixo para
    # gerar o cookie X11 compartilhado com o container - sem ele, a
    # interface pode falhar ao conectar ao display mesmo com o container
    # corretamente configurado (erro "Authorization required, but no
    # authorization protocol specified").
    if ! command -v xauth >/dev/null 2>&1; then
        echo "[uavedusim_gui] Comando 'xauth' nao encontrado. Tentando instalar automaticamente..."
        case "$(_uavedusim_detect_dpm)" in
            dnf)
                "${_uavedusim_sudo[@]}" dnf install -y xorg-x11-xauth
                ;;
            apt)
                "${_uavedusim_sudo[@]}" apt-get update
                "${_uavedusim_sudo[@]}" apt-get install -y xauth
                ;;
            pacman)
                "${_uavedusim_sudo[@]}" pacman -S --needed --noconfirm xorg-xauth
                ;;
            zypper)
                "${_uavedusim_sudo[@]}" zypper --non-interactive install xauth
                ;;
            apk)
                "${_uavedusim_sudo[@]}" apk add --no-cache xauth
                ;;
            *)
                echo "[uavedusim_gui] Nao reconheci o gerenciador de pacotes para instalar o 'xauth' automaticamente. Instale manualmente (pacote costuma se chamar 'xauth', 'xorg-xauth' ou 'xorg-x11-xauth')." >&2
                ;;
        esac

        if command -v xauth >/dev/null 2>&1; then
            echo "[uavedusim_gui] 'xauth' instalado com sucesso."
        else
            echo "[uavedusim_gui] Nao foi possivel instalar 'xauth' automaticamente. Seguindo so com xhost (menos confiavel)." >&2
        fi
    fi

    # Garante que o Git LFS esta disponivel e que os arquivos rastreados por
    # ele (como o QGroundControl-x86_64.AppImage) foram baixados de verdade.
    # Sem isso, o Git deixa so um "ponteiro" de texto no lugar do binario
    # real, o que faz a extracao do AppImage falhar mais adiante com erros
    # confusos (ex: "version: command not found", "oid: command not found").
    if ! command -v git-lfs >/dev/null 2>&1; then
        echo "[uavedusim_gui] Comando 'git-lfs' nao encontrado. Tentando instalar automaticamente..."
        case "$(_uavedusim_detect_dpm)" in
            dnf)
                "${_uavedusim_sudo[@]}" dnf install -y git-lfs
                ;;
            apt)
                "${_uavedusim_sudo[@]}" apt-get update
                "${_uavedusim_sudo[@]}" apt-get install -y git-lfs
                ;;
            pacman)
                "${_uavedusim_sudo[@]}" pacman -S --needed --noconfirm git-lfs
                ;;
            zypper)
                "${_uavedusim_sudo[@]}" zypper --non-interactive install git-lfs
                ;;
            apk)
                "${_uavedusim_sudo[@]}" apk add --no-cache git-lfs
                ;;
            *)
                echo "[uavedusim_gui] Nao reconheci o gerenciador de pacotes para instalar o 'git-lfs' automaticamente. Instale manualmente: https://git-lfs.com" >&2
                ;;
        esac

        if command -v git-lfs >/dev/null 2>&1; then
            echo "[uavedusim_gui] 'git-lfs' instalado com sucesso."
        else
            echo "[uavedusim_gui] Nao foi possivel instalar 'git-lfs' automaticamente. Arquivos rastreados por LFS (ex: QGroundControl-x86_64.AppImage) podem continuar como ponteiros de texto." >&2
        fi
    fi

    if command -v git-lfs >/dev/null 2>&1 && [ -d "$_script_dir/.git" ]; then
        ( cd "$_script_dir" && git lfs install --local >/dev/null 2>&1 || true )

        # Se o AppImage do QGroundControl ainda for so o ponteiro de texto
        # do Git LFS (poucos bytes, comecando com "version https://git-lfs
        # ..."), baixa o binario real agora, antes do container tentar
        # extrai-lo.
        _uavedusim_qgc_appimage="$_script_dir/QGroundControl-x86_64.AppImage"
        if [ -f "$_uavedusim_qgc_appimage" ] && head -c 200 "$_uavedusim_qgc_appimage" 2>/dev/null | grep -q '^version https://git-lfs.github.com/spec/v1'; then
            echo "[uavedusim_gui] QGroundControl-x86_64.AppImage ainda e um ponteiro do Git LFS. Baixando o binario real..."
            ( cd "$_script_dir" && git lfs pull --include="QGroundControl-x86_64.AppImage" )
        fi
    fi

    _docker_bin="docker"
    _sudo_prefix=()
    if ! "$_docker_bin" info >/dev/null 2>&1; then
        if command -v sudo >/dev/null 2>&1 && sudo -n "$_docker_bin" info >/dev/null 2>&1; then
            _sudo_prefix=(sudo)
        elif command -v sudo >/dev/null 2>&1; then
            # sudo pode pedir senha interativamente aqui, o que e esperado.
            _sudo_prefix=(sudo)
        else
            echo "[uavedusim_gui] Docker indisponivel e sem sudo. Seguindo sem container." >&2
            UAVEDUSIM_USE_DOCKER=0
        fi
    fi

    if [ "$UAVEDUSIM_USE_DOCKER" = "1" ]; then
        _dk() { "${_sudo_prefix[@]}" "$_docker_bin" "$@"; }

        if ! _dk image inspect "$UAVEDUSIM_DOCKER_IMAGE" >/dev/null 2>&1; then
            if [ ! -f "$_script_dir/Dockerfile" ]; then
                echo "[uavedusim_gui] Dockerfile nao encontrado em $_script_dir. Rodando sem container." >&2
                UAVEDUSIM_USE_DOCKER=0
            else
                echo "[uavedusim_gui] Construindo imagem '$UAVEDUSIM_DOCKER_IMAGE' (primeira execucao, pode demorar)..."
                _dk build \
                    --build-arg HOST_UID="$(id -u)" \
                    --build-arg HOST_GID="$(id -g)" \
                    -t "$UAVEDUSIM_DOCKER_IMAGE" "$_script_dir"
            fi
        fi
    fi

    if [ "$UAVEDUSIM_USE_DOCKER" = "1" ]; then
        # Libera conexoes X11 locais via checagem de UID do socket (SO_PEERCRED).
        # IMPORTANTE: "+local:docker" NAO e uma sintaxe valida do xhost - roda
        # sem erro mas nao libera nada. "+local:" (sem nada depois dos dois
        # pontos) e a forma correta de liberar qualquer conexao local.
        command -v xhost >/dev/null 2>&1 && xhost +local: >/dev/null 2>&1 || true

        # Metodo universal para o cookie Xauthority: em vez de tentar adivinhar
        # ONDE cada compositor guarda o arquivo (~/.Xauthority no X11 puro,
        # /run/user/UID/.mutter-Xwayland-authXXXX no GNOME, um caminho proprio
        # no Hyprland/wlroots, etc.), extraimos o cookie diretamente do
        # SERVIDOR X em execucao via "xauth nlist" e geramos um arquivo novo,
        # dedicado, com o "family" trocado para coringa (truque padrao para
        # esse tipo de forwarding). Isso funciona igual em qualquer compositor
        # ou window manager, X11 ou Wayland/XWayland - nao depende de nenhum
        # caminho especifico.
        _xauth_create_args=()
        _xauth_exec_args=()
        if command -v xauth >/dev/null 2>&1; then
            _uavedusim_xauth="/tmp/.uavedusim_xauth_uid$(id -u)"
            touch "$_uavedusim_xauth"
            xauth nlist "${DISPLAY:-:0}" 2>/dev/null | sed -e 's/^..../ffff/' | xauth -f "$_uavedusim_xauth" nmerge - >/dev/null 2>&1 || true
            if [ -s "$_uavedusim_xauth" ]; then
                _xauth_create_args=(-v "$_uavedusim_xauth":/home/dev/.Xauthority:ro -e XAUTHORITY=/home/dev/.Xauthority)
                _xauth_exec_args=(-e XAUTHORITY=/home/dev/.Xauthority)
            fi
        fi
        if [ "${#_xauth_create_args[@]}" -eq 0 ]; then
            echo "[uavedusim_gui] Aviso: nao foi possivel gerar cookie Xauthority (comando 'xauth' ausente ou sem cookie disponivel). Seguindo so com xhost." >&2
        fi

        _container_exists=0
        if _dk container inspect "$UAVEDUSIM_DOCKER_CONTAINER" >/dev/null 2>&1; then
            _container_exists=1
        fi

        if [ "$_container_exists" = "1" ]; then
            _running="$(_dk inspect -f '{{.State.Running}}' "$UAVEDUSIM_DOCKER_CONTAINER")"
            if [ "$_running" != "true" ]; then
                echo "[uavedusim_gui] Reiniciando container existente '$UAVEDUSIM_DOCKER_CONTAINER'..."
                _dk start "$UAVEDUSIM_DOCKER_CONTAINER" >/dev/null
            fi

            # Verifica se o mount da pasta do repositorio ainda esta saudavel.
            # Isso pode ficar invalido se a pasta no host foi apagada e
            # recriada com o mesmo caminho (ex: "rm -rf" seguido de um novo
            # "git clone" no mesmo lugar) enquanto o container ja existia -
            # nesse caso o Docker recusa comandos com um erro tecnico
            # ("possible container breakout detected"). Detectando isso aqui,
            # recriamos o container automaticamente em vez de falhar.
            if ! _dk exec "$UAVEDUSIM_DOCKER_CONTAINER" test -d "$_script_dir" >/dev/null 2>&1; then
                echo "[uavedusim_gui] Container existente com referencia invalida a pasta do repositorio (pode ter sido apagada/recriada no host). Recriando o container..."
                _dk rm -f "$UAVEDUSIM_DOCKER_CONTAINER" >/dev/null 2>&1 || true
                _container_exists=0
            fi
        fi

        if [ "$_container_exists" = "0" ]; then
            echo "[uavedusim_gui] Criando container '$UAVEDUSIM_DOCKER_CONTAINER' (pacotes instalados nele serao preservados entre execucoes)..."

            # Deteccao automatica de GPU: cada maquina do laboratorio pode ter
            # Intel, AMD, NVIDIA ou nenhuma GPU dedicada. Isso roda uma unica
            # vez, na criacao do container, e decide os flags certos para
            # ESTA maquina especifica - sem exigir que o usuario saiba nada
            # sobre isso.
            _gpu_docker_args=()
            _gpu_env_args=()

            if command -v nvidia-smi >/dev/null 2>&1; then
                # GPU NVIDIA com driver proprietario presente.
                if _dk info 2>/dev/null | grep -qi 'nvidia'; then
                    echo "[uavedusim_gui] GPU NVIDIA detectada, NVIDIA Container Toolkit ja configurado. Usando --gpus all."
                    _gpu_docker_args=(--gpus all)
                    # --gpus all sozinho so libera capacidade de COMPUTACAO
                    # (CUDA/nvidia-smi) por padrao - sem isso, o Gazebo nao
                    # consegue usar a GPU para OpenGL/EGL (renderizacao 3D),
                    # mesmo com a GPU visivel e funcional para outras coisas.
                    _gpu_env_args+=(-e NVIDIA_DRIVER_CAPABILITIES=all)
                else
                    echo "[uavedusim_gui] GPU NVIDIA detectada, mas o NVIDIA Container Toolkit nao esta configurado. Tentando instalar automaticamente..."
                    _nvctk_ok=0
                    _sudo_nv=()
                    if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
                        _sudo_nv=(sudo)
                    fi

                    if command -v apt-get >/dev/null 2>&1; then
                        if curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
                                | "${_sudo_nv[@]}" gpg --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
                            && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
                                | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
                                | "${_sudo_nv[@]}" tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null \
                            && "${_sudo_nv[@]}" apt-get update \
                            && "${_sudo_nv[@]}" apt-get install -y nvidia-container-toolkit; then
                            _nvctk_ok=1
                        fi
                    elif command -v dnf >/dev/null 2>&1; then
                        if curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo \
                                | "${_sudo_nv[@]}" tee /etc/yum.repos.d/nvidia-container-toolkit.repo >/dev/null \
                            && "${_sudo_nv[@]}" dnf install -y nvidia-container-toolkit; then
                            _nvctk_ok=1
                        fi
                    else
                        echo "[uavedusim_gui] Gerenciador de pacotes nao suportado para instalar o NVIDIA Container Toolkit automaticamente." >&2
                    fi

                    if [ "$_nvctk_ok" = "1" ]; then
                        "${_sudo_nv[@]}" nvidia-ctk runtime configure --runtime=docker >/dev/null 2>&1 || true
                        "${_sudo_nv[@]}" systemctl restart docker >/dev/null 2>&1 || true
                        sleep 2
                        if _dk info 2>/dev/null | grep -qi 'nvidia'; then
                            echo "[uavedusim_gui] NVIDIA Container Toolkit instalado e configurado com sucesso. Usando --gpus all."
                            _gpu_docker_args=(--gpus all)
                            _gpu_env_args+=(-e NVIDIA_DRIVER_CAPABILITIES=all)
                        else
                            echo "[uavedusim_gui] Aviso: instalei o toolkit, mas o Docker ainda nao reconhece o runtime NVIDIA. Seguindo sem aceleracao dedicada." >&2
                        fi
                    else
                        echo "[uavedusim_gui] Aviso: nao consegui instalar o NVIDIA Container Toolkit automaticamente. Seguindo sem aceleracao dedicada." >&2
                    fi
                fi
            fi

            if [ "${#_gpu_docker_args[@]}" -eq 0 ] && [ -e /dev/dri ]; then
                # Intel / AMD (ou NVIDIA com driver open-source nouveau) via Mesa.
                # Nao exige nenhuma instalacao extra: o dispositivo ja existe
                # no host se houver qualquer GPU com driver open-source ativo.
                echo "[uavedusim_gui] GPU Intel/AMD (Mesa) detectada. Usando /dev/dri."
                _gpu_docker_args=(--device /dev/dri:/dev/dri)
            fi

            if [ "${#_gpu_docker_args[@]}" -eq 0 ]; then
                echo "[uavedusim_gui] Nenhuma GPU dedicada disponivel; Gazebo usara renderizacao por software (mais lento)." >&2
                _gpu_env_args=(-e LIBGL_ALWAYS_SOFTWARE=1)
            fi

            # Usuarios avancados podem sobrescrever a deteccao automatica
            # definindo UAVEDUSIM_GPU_ARGS antes de rodar o script.
            if [ -n "${UAVEDUSIM_GPU_ARGS:-}" ]; then
                read -r -a _gpu_docker_args <<< "${UAVEDUSIM_GPU_ARGS}"
                _gpu_env_args=()
            fi

            # --security-opt label=disable: desativa o confinamento do SELinux
            #   para este container. Sem isso, hosts com SELinux enforcing
            #   (Fedora, RHEL, CentOS) bloqueiam acesso a arquivos/sockets
            #   montados (repo, .X11-unix) mesmo com permissoes Unix corretas,
            #   exigindo relabel manual toda vez (inclusive em arquivos novos).
            #   Com isso desativado, funciona sem nenhum passo extra, em
            #   qualquer distro, com ou sem SELinux.
            # --network host: PX4/Gazebo/QGroundControl trocam mensagens MAVLink
            #   via UDP; host networking evita mapear dezenas de portas manualmente.
            #   So funciona assim no Linux (nao no Docker Desktop de Mac/Windows).
            # --init: injeta um processo "init" minimo (tini) como PID 1 do
            #   container, no lugar do "sleep infinity" sozinho. Sem isso,
            #   processos orfaos (ex: uma sessao anterior encerrada de forma
            #   abrupta) viram "zumbis" e nunca sao recolhidos - alguns deles
            #   continuam aparecendo no "ps"/"pgrep" mesmo mortos, o que pode
            #   enganar checagens de "ja tem uma simulacao rodando".
            _dk run -d \
                --name "$UAVEDUSIM_DOCKER_CONTAINER" \
                --network host \
                --security-opt label=disable \
                --init \
                "${_gpu_docker_args[@]}" \
                "${_gpu_env_args[@]}" \
                "${_xauth_create_args[@]}" \
                -e DISPLAY="${DISPLAY:-}" \
                -e GDK_BACKEND=x11 \
                -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
                -v "$_script_dir":"$_script_dir" \
                -w "$_script_dir" \
                "$UAVEDUSIM_DOCKER_IMAGE" \
                sleep infinity >/dev/null
        fi

        # Reexecuta este MESMO script, agora dentro do container. DISPLAY e
        # XAUTHORITY sao passados de novo aqui (nao so na criacao), porque
        # "docker exec" NAO herda automaticamente valores atualizados de "-e"
        # da criacao do container - cada exec precisa receber o valor atual.
        # -it exige um terminal; se este launcher for chamado sem terminal
        # (ex: atalho de desktop sem terminal associado), troque "-it" por "-i".
        exec "${_sudo_prefix[@]}" "$_docker_bin" exec -it \
            -e DISPLAY="${DISPLAY:-}" \
            "${_xauth_exec_args[@]}" \
            "$UAVEDUSIM_DOCKER_CONTAINER" bash "$_script_path"
    fi
fi
# A partir daqui o script roda DENTRO do container Ubuntu 24.04 (ou no host,
# se a orquestracao acima tiver sido desativada/indisponivel).
# ============================================================================

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

    # Segunda camada de limpeza: qualquer "wget-log*" residual que tenha
    # sobrado na pasta do repositorio (ex: de algum download feito durante
    # a sessao) e removido ao encerrar.
    rm -f "$APP_DIR"/wget-log* >/dev/null 2>&1 || true

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



# BEGIN UAVEDUSIM_PILLOW_DEPENDENCY_PATCH
# Verifica e instala Pillow, usado nos cantos arredondados com antialiasing
# da interface Tkinter.
ensure_python_pillow_dependency() {
    if python3 -c 'from PIL import Image, ImageDraw, ImageTk' >/dev/null 2>&1; then
        log "Pillow verificado."
        return 0
    fi

    local manager
    manager="$(detect_package_manager)"

    local -a pillow_pkg=()

    case "$manager" in
        dnf)
            pillow_pkg=(python3-pillow python3-pillow-tk)
            ;;
        apt)
            pillow_pkg=(python3-pil python3-pil.imagetk)
            ;;
        pacman)
            pillow_pkg=(python-pillow)
            ;;
        zypper)
            pillow_pkg=(python3-Pillow)
            ;;
        apk)
            pillow_pkg=(py3-pillow)
            ;;
        xbps)
            pillow_pkg=(python3-Pillow)
            ;;
        *)
            pillow_pkg=()
            ;;
    esac

    if [ "${#pillow_pkg[@]}" -eq 0 ]; then
        show_error "Pillow ausente e gerenciador de pacotes não reconhecido. Instale manualmente o Pillow para Python 3."
        return 1
    fi

    install_system_packages "$manager" "${pillow_pkg[@]}"

    if ! python3 -c 'from PIL import Image, ImageDraw, ImageTk' >/dev/null 2>&1; then
        show_error "Pillow continua ausente após a instalação. Instale manualmente o pacote Pillow/ImageTk da sua distribuição."
        return 1
    fi

    log "Pillow instalado e verificado."
}
# END UAVEDUSIM_PILLOW_DEPENDENCY_PATCH


# BEGIN UAVEDUSIM_PX4_GAZEBO_SETUP_PATCH
# Roda o PX4-Autopilot/Tools/setup/ubuntu.sh (instala Gazebo, toolchain NuttX,
# dependencias Python etc.) APENAS na primeira execucao. Um arquivo sentinela
# marca a conclusao, para nunca mais repetir esse passo pesado automaticamente.
ensure_px4_gazebo_dependencies() {
    # O marcador fica em $HOME (ex: /home/dev dentro do container), e NAO na
    # pasta do repositorio - a pasta do repo e montada do host e sobrevive a
    # recriacao do container, mas os pacotes que o ubuntu.sh instala ficam
    # DENTRO do container e se perdem se ele for recriado. Se o marcador
    # ficasse no repo, o script acharia (erradamente) que ja instalou tudo
    # mesmo depois de perder os pacotes de verdade.
    local sentinel="$HOME/.uavedusim_ubuntu_setup_done"

    if [ -f "$sentinel" ]; then
        log "Dependencias do PX4/Gazebo ja instaladas (marcador: $sentinel). Pulando ubuntu.sh."
        return 0
    fi

    local ubuntu_sh="$PX4_DIR/Tools/setup/ubuntu.sh"
    if [ ! -f "$ubuntu_sh" ]; then
        show_error "Não encontrei $ubuntu_sh. Confirme se o submódulo PX4-Autopilot foi clonado (git submodule update --init --recursive)."
        return 1
    fi

    log "Primeira execução: instalando dependências do PX4/Gazebo via ubuntu.sh (pode levar vários minutos)..."
    bash "$ubuntu_sh"

    # O wget (usado pelo ubuntu.sh) cria arquivos "wget-log*" quando a saida
    # nao esta indo direto para um terminal (nosso caso, por causa do tee).
    # Sao só logs de download, inofensivos, mas poluem a pasta do repositorio.
    rm -f "$APP_DIR"/wget-log*

    touch "$sentinel"
    log "Setup do PX4/Gazebo concluído. Marcador criado em $sentinel (apague este arquivo para forçar reinstalação)."
}
# END UAVEDUSIM_PX4_GAZEBO_SETUP_PATCH


# BEGIN UAVEDUSIM_QGC_EXTRACT_PATCH
# AppImages (como o QGroundControl) normalmente precisam do FUSE para rodar,
# e containers Docker nao tem /dev/fuse disponivel por padrao. A solucao
# padrao (recomendada pelo proprio projeto AppImage para CI/containers) e
# extrair o conteudo uma unica vez com "--appimage-extract", que NAO depende
# de FUSE, e rodar o binario ja extraido diretamente depois disso.
ensure_qgroundcontrol_extracted() {
    if [ -x "$QGC_EXTRACTED" ]; then
        log "QGroundControl ja extraido em $QGC_EXTRACTED."
        return 0
    fi

    if [ ! -f "$QGC_APP" ]; then
        log "Aviso: $QGC_APP não encontrado; pulando extração do QGroundControl."
        return 0
    fi

    log "Extraindo QGroundControl (AppImages exigem FUSE, indisponível em containers por padrão)..."
    chmod +x "$QGC_APP"
    (
        cd "$APP_DIR" && "$QGC_APP" --appimage-extract >/dev/null
    )

    if [ -x "$QGC_EXTRACTED" ]; then
        log "QGroundControl extraído com sucesso em $QGC_EXTRACTED."
    else
        show_error "Falha ao extrair o QGroundControl. Verifique se $QGC_APP é um AppImage válido."
    fi
}
# END UAVEDUSIM_QGC_EXTRACT_PATCH


# BEGIN UAVEDUSIM_SPOOFER_BUILD_PATCH
# Recompila as ferramentas do UAVJamSim (spoofer/attack_mitigator) sempre que
# o container e novo. A pasta de build vive no HOST (montada), entao um
# binario compilado numa versao anterior do container/Ubuntu (com bibliotecas
# .so incompativeis) pode sobreviver por engano para o container atual - foi
# exatamente isso que aconteceu quando trocamos de Ubuntu 26.04 para 24.04.
# Apagando o build antigo e recompilando do zero (uma unica vez por
# container, controlado por marcador em $HOME) evita esse tipo de problema.
ensure_spoofer_tools_built() {
    local build_marker="$HOME/.uavedusim_spoofer_build_done"

    if [ -f "$build_marker" ]; then
        log "Ferramentas UAVJamSim já compiladas neste container (marcador: $build_marker)."
        return 0
    fi

    local spoofer_dir="$APP_DIR/UAVJamSim/spoofer"
    local build_dir="$spoofer_dir/build"

    if [ ! -d "$spoofer_dir" ]; then
        log "Aviso: pasta $spoofer_dir não encontrada; pulando build do UAVJamSim."
        return 0
    fi

    if [ -d "$build_dir" ]; then
        log "Removendo build antigo do UAVJamSim (pode ser incompatível com este container): $build_dir"
        rm -rf "$build_dir"
    fi

    # Dependencia de build especifica do spoofer (Boost.ProgramOptions), nao
    # instalada pelo ubuntu.sh/Dockerfile por ser exclusiva deste projeto.
    if ! dpkg -s libboost-program-options-dev >/dev/null 2>&1; then
        log "Instalando dependência de build ausente: libboost-program-options-dev"
        sudo apt-get update -qq
        sudo apt-get install -y libboost-program-options-dev
    fi

    if [ -x "$spoofer_dir/build.sh" ]; then
        log "Compilando ferramentas do UAVJamSim (spoofer/attack_mitigator) via build.sh..."
        ( cd "$spoofer_dir" && bash ./build.sh )
    else
        log "Aviso: $spoofer_dir/build.sh não encontrado ou não executável; a interface tentará compilar sozinha quando necessário."
        return 0
    fi

    if [ -x "$build_dir/spoofer" ] && [ -x "$build_dir/attack_mitigator" ]; then
        touch "$build_marker"
        log "Ferramentas do UAVJamSim compiladas com sucesso."
    else
        show_error "Falha ao compilar as ferramentas do UAVJamSim (spoofer/attack_mitigator). Veja o log."
    fi
}
# END UAVEDUSIM_SPOOFER_BUILD_PATCH


# BEGIN UAVEDUSIM_NERD_FONTS_PATCH
# Instala uma Nerd Font (icones usados por prompts/terminais e pela propria
# interface) automaticamente, uma unica vez. Nao depende do Dockerfile ja
# ter isso pronto - funciona igual em containers novos (onde a imagem ja
# vem com a fonte, e o passo abaixo so confirma e pula) ou em containers
# ja existentes/antigos (onde baixa e instala na hora, sem nenhum passo
# manual do usuario). Instala na pasta de fontes do PROPRIO usuario
# ($HOME/.local/share/fonts), entao nao precisa de sudo/root.
ensure_nerd_fonts_installed() {
    local sentinel="$HOME/.uavedusim_nerdfonts_done"
    if [ -f "$sentinel" ]; then
        log "Nerd Fonts já instaladas (marcador: $sentinel)."
        return 0
    fi

    if fc-list 2>/dev/null | grep -qi "JetBrainsMono Nerd Font"; then
        log "Nerd Fonts já presentes no sistema (a imagem já vinha com elas)."
        touch "$sentinel"
        return 0
    fi

    log "Instalando Nerd Fonts (ícones para terminais/interface)..."

    if ! command -v unzip >/dev/null 2>&1; then
        install_system_packages "$(detect_package_manager)" unzip
    fi

    local font_dir="$HOME/.local/share/fonts/nerd-fonts"
    mkdir -p "$font_dir"

    if curl -fsSL -o /tmp/uavedusim_nerdfont.zip \
        https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.zip; then
        unzip -oq /tmp/uavedusim_nerdfont.zip -d "$font_dir"
        rm -f /tmp/uavedusim_nerdfont.zip
        fc-cache -f "$font_dir" >/dev/null 2>&1 || fc-cache -f >/dev/null 2>&1 || true
        touch "$sentinel"
        log "Nerd Fonts instaladas com sucesso em $font_dir."
    else
        log "Aviso: não foi possível baixar as Nerd Fonts (sem internet?). Ícones podem não aparecer, mas isso não impede o simulador de funcionar."
    fi
}
# END UAVEDUSIM_NERD_FONTS_PATCH


# BEGIN UAVEDUSIM_GPU_STATUS_PATCH
# Informa, em TODA execucao, se a aceleracao de GPU esta realmente acessivel
# de dentro do container. Isso importa porque os flags de GPU (--gpus all ou
# --device /dev/dri) so sao aplicados na CRIACAO do container - se o
# container ja existia de uma sessao anterior a essa funcionalidade (ou de
# uma maquina sem os drivers corretos na epoca), ele nunca vai ter acesso a
# GPU ate ser recriado, mesmo que o host tenha uma placa dedicada.
report_gpu_status() {
    local has_nvidia=0
    local has_dri=0

    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        has_nvidia=1
    fi

    if [ -e /dev/dri ]; then
        has_dri=1
    fi

    if [ "$has_nvidia" = "1" ]; then
        log "GPU: NVIDIA acessível dentro do container (--gpus all funcionando)."
    elif [ "$has_dri" = "1" ]; then
        log "GPU: dispositivo /dev/dri acessível dentro do container (Intel/AMD/Mesa)."
    else
        log "GPU: nenhuma aceleração de GPU disponível dentro deste container. Gazebo usará renderização por software (mais lento)."
        log "GPU: se esta máquina tem uma GPU dedicada, este container provavelmente foi criado antes desse suporte existir (ou dos drivers estarem prontos). Rode 'sudo docker rm -f ${UAVEDUSIM_DOCKER_CONTAINER}' e execute o script de novo para recriar com aceleração de GPU."
    fi
}
# END UAVEDUSIM_GPU_STATUS_PATCH

log "Iniciando UAVEDUSIM GUI"
log "APP_DIR=$APP_DIR"
log "Sessão: XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-vazio}, WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-vazio}, DISPLAY=${DISPLAY:-vazio}"
report_gpu_status

# Verifica e instala dependencias mínimas em distros suportadas.
ensure_system_dependencies
ensure_python_pillow_dependency
ensure_nerd_fonts_installed
ensure_px4_gazebo_dependencies
ensure_spoofer_tools_built
ensure_qgroundcontrol_extracted

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
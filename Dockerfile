# Dockerfile
# Ambiente de testes para o tutorial de instalacao (Ubuntu 24.04 LTS "Noble Numbat")
# Test environment for the installation tutorial (Ubuntu 24.04 LTS "Noble Numbat")
#
# NOTA: usamos 24.04 em vez da versao mais recente (26.04 "Resolute") porque o
# repositorio do Gazebo Harmonic (packages.osrfoundation.org) so publica pacotes
# para Ubuntu Jammy (22.04) e Noble (24.04). O script PX4 ubuntu.sh tambem so
# tem suporte explicito ate 24.04.
# NOTE: we use 24.04 instead of the newest release (26.04 "Resolute") because
# the Gazebo Harmonic repository (packages.osrfoundation.org) only publishes
# packages for Ubuntu Jammy (22.04) and Noble (24.04). The PX4 ubuntu.sh script
# also only has explicit support up to 24.04.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# Pacotes base para seguir o tutorial passo a passo
# Base packages needed to follow the tutorial step by step
RUN apt-get update && apt-get install -y --no-install-recommends \
    sudo \
    curl \
    wget \
    git \
    nano \
    vim \
    unzip \
    fontconfig \
    build-essential \
    ca-certificates \
    gnupg \
    gosu \
    lsb-release \
    software-properties-common \
    libboost-program-options-dev \
    && rm -rf /var/lib/apt/lists/*

# Nerd Font (JetBrainsMono) para os terminais/interface exibirem os icones
# usados por prompts (ex: starship, powerlevel10k) e pela propria GUI.
# Instalado uma unica vez, no build da imagem.
# Nerd Font (JetBrainsMono) so terminals/UI can render the icon glyphs used
# by prompts (e.g. starship, powerlevel10k) and by the GUI itself.
# Installed once, at image build time.
RUN mkdir -p /usr/share/fonts/nerd-fonts \
    && curl -fsSL -o /tmp/JetBrainsMono.zip \
        https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.zip \
    && unzip -oq /tmp/JetBrainsMono.zip -d /usr/share/fonts/nerd-fonts \
    && rm -f /tmp/JetBrainsMono.zip \
    && fc-cache -f >/dev/null

# Python + Tkinter + Pillow: usados pela GUI do UAVEDUSIM (Tkinter para a
# janela, Pillow para os componentes arredondados/degrades e para gerar o
# icone do app). Isso e independente do PX4/Gazebo/QGroundControl, que
# continuam fazendo parte do tutorial de instalacao manual.
# Python + Tkinter + Pillow: used by the UAVEDUSIM GUI (Tkinter for the
# window, Pillow for the rounded/gradient components and to generate the
# app icon). This is independent of PX4/Gazebo/QGroundControl, which
# remain part of the manual installation tutorial.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-tk \
    python3-pil \
    python3-pil.imagetk \
    && rm -rf /var/lib/apt/lists/*

# TODO: ajuste o caminho de origem abaixo para o local real do script
# dentro do repositorio de voces (ex: src/uavedusim_gui_app.py).
# TODO: adjust the source path below to where the script actually lives
# in your repo (e.g. src/uavedusim_gui_app.py).
COPY uavedusim_gui_app.py /opt/uavedusim/uavedusim_gui_app.py

# Gera o icone do app (PNG, a partir do mesmo desenho usado no badge da
# tela inicial) uma unica vez, no build da imagem - nao e um arquivo
# "pronto" versionado a mao, e sim produzido pelo proprio script.
# Instalado no tema de icones padrao (hicolor) para o GNOME/Ubuntu
# conseguir resolver "Icon=uavedusim" no .desktop sem precisar de
# caminho absoluto.
# Generates the app icon (PNG, from the same drawing used in the home
# screen badge) once, at image build time - not a hand-made "ready"
# file, but produced by the script itself. Installed into the standard
# (hicolor) icon theme so GNOME/Ubuntu can resolve "Icon=uavedusim" in
# the .desktop file without needing an absolute path.
RUN mkdir -p /usr/share/icons/hicolor/256x256/apps \
    && python3 /opt/uavedusim/uavedusim_gui_app.py --export-icon \
        /usr/share/icons/hicolor/256x256/apps/uavedusim.png --icon-size 256 \
    && gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true

# Atalho .desktop para o UAVEDUSIM aparecer com nome/icone proprios no
# menu de aplicativos, dash, alt-tab e barra de janelas abertas do
# GNOME/Ubuntu (StartupWMClass casa com o className="UAVEDUSIM" do Tk).
# .desktop launcher so UAVEDUSIM shows up with its own name/icon in the
# GNOME/Ubuntu app menu, dash, alt-tab and open-windows list
# (StartupWMClass matches the Tk className="UAVEDUSIM").
#
# TODO: ajuste o Exec= abaixo para o comando real que voces usam pra
# abrir a GUI (ex: uavedusim_gui_docker.sh, um venv especifico, etc.).
# TODO: adjust Exec= below to the actual command you use to launch the
# GUI (e.g. uavedusim_gui_docker.sh, a specific venv, etc.).
RUN mkdir -p /usr/share/applications \
    && printf '%s\n' \
        '[Desktop Entry]' \
        'Type=Application' \
        'Name=UAVEDUSIM' \
        'Comment=Simulador de ataques de jamming em VANTs' \
        'Exec=python3 /opt/uavedusim/uavedusim_gui_app.py' \
        'Icon=uavedusim' \
        'StartupWMClass=UAVEDUSIM' \
        'Terminal=false' \
        'Categories=Education;Science;' \
        > /usr/share/applications/uavedusim.desktop


# Usuario nao-root com sudo, para reproduzir a experiencia real de um usuario Ubuntu
# Non-root sudo user, to reproduce the real experience of an Ubuntu user
#
# O UID/GID sao recebidos via --build-arg (definidos pelo script de launcher
# como os do usuario do HOST). Isso garante que arquivos em pastas montadas
# via bind mount (-v) apareçam com o dono/permissoes corretos para o usuario
# "dev" de dentro do container - sem isso, operacoes como apagar/criar
# arquivos na pasta do repositorio (que vive no host) falham com "Permission
# denied" sempre que o UID do host for diferente do UID padrao do container.
#
# The dev user is created with UID/GID matching the HOST user (passed as
# --build-arg by the launcher script). This keeps ownership consistent on
# bind-mounted folders - without this, file operations (deleting/creating
# files) in the repo folder (which lives on the host) fail with "Permission
# denied" whenever the host UID differs from the container's default UID.
ARG HOST_UID=1000
ARG HOST_GID=1000

# Imagens oficiais recentes do Ubuntu (24.04+) ja vem com um usuario padrao
# "ubuntu" em UID/GID 1000 (pratica de seguranca da Canonical). Como a maioria
# dos usuarios Linux tambem tem UID 1000 (primeiro usuario da maquina), isso
# colide direto com o nosso "dev". Por isso removemos qualquer usuario/grupo
# pre-existente nesse UID/GID antes de criar o "dev" - funciona com qualquer
# UID de host, em qualquer distro.
#
# Recent official Ubuntu images (24.04+) already ship a default "ubuntu" user
# at UID/GID 1000 (Canonical security practice). Since most Linux users also
# have UID 1000 (first user on the machine), this collides directly with our
# "dev" user. So we remove any pre-existing user/group at that UID/GID before
# creating "dev" - works with any host UID, on any distro.
RUN set -eux; \
    if getent passwd "$HOST_UID" >/dev/null; then \
        userdel -r "$(getent passwd "$HOST_UID" | cut -d: -f1)" 2>/dev/null || true; \
    fi; \
    if getent group "$HOST_GID" >/dev/null; then \
        groupdel "$(getent group "$HOST_GID" | cut -d: -f1)" 2>/dev/null || true; \
    fi; \
    groupadd -g "$HOST_GID" dev; \
    useradd -m -s /bin/bash -u "$HOST_UID" -g "$HOST_GID" -G sudo dev; \
    echo "dev ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Containers nao setam $USER automaticamente (falta de login via PAM).
# Alguns scripts (ex: PX4 ubuntu.sh) dependem dessa variavel para escrever em /home/$USER/.
# Containers do not set $USER automatically (no PAM login).
# Some scripts (e.g. PX4 ubuntu.sh) rely on this variable to write to /home/$USER/.
ENV USER=dev

USER dev
WORKDIR /home/dev

# Forca todos os clones "git@github.com:..." a usar HTTPS em vez de SSH,
# evitando falhas em submodulos que exigem SSH configurado (ex: PX4-Autopilot).
# Forces all "git@github.com:..." clones to use HTTPS instead of SSH,
# avoiding failures on submodules that require SSH set up (e.g. PX4-Autopilot).
RUN git config --global url."https://github.com/".insteadOf "git@github.com:"

CMD ["/bin/bash"]
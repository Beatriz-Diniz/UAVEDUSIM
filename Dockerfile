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

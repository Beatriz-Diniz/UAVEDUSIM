#!/usr/bin/env bash

set -e

## Bash script para configurar o ambiente de desenvolvimento PX4 no Fedora 44.
## Traduzido e adaptado da versão original do Ubuntu.

INSTALL_NUTTX="true"
INSTALL_SIM="true"
INSTALL_ARCH=$(uname -m)

# Parse arguments
for arg in "$@"
do
	if [[ $arg == "--no-nuttx" ]]; then
		INSTALL_NUTTX="false"
	fi

	if [[ $arg == "--no-sim-tools" ]]; then
		INSTALL_SIM="false"
	fi
done

echo "[fedora.sh] Iniciando..."
echo "[fedora.sh] Arquitetura: $INSTALL_ARCH"

# Diretório do script
DIR=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )

# Verifica se o requirements.txt existe
REQUIREMENTS_FILE="requirements.txt"
if [[ ! -f "${DIR}/${REQUIREMENTS_FILE}" ]]; then
	echo "[fedora.sh] FALHA: ${REQUIREMENTS_FILE} é necessário no mesmo diretório que o script (${DIR})."
	exit 1
fi

echo "[fedora.sh] Instalando dependências gerais do PX4 via DNF"

sudo dnf upgrade --refresh -y
sudo dnf install -y --setopt=install_weak_deps=False \
	astyle \
	@development-tools \
	ccache \
	cmake \
	cppcheck \
	file \
	gcc-c++ \
	gcc \
	gdb \
	git \
	lcov \
	openssl-devel \
	libxml2-devel \
	libxml2 \
	make \
	ninja-build \
	python3 \
	python3-devel \
	python3-pip \
	python3-setuptools \
	python3-wheel \
	rsync \
	ShellCheck \
	unzip \
	zip
	
# Dependências adicionais para o Gazebo Harmonic / OGRE2 / DART
sudo dnf install -y \
    swig \
    assimp-devel \
    libzip-devel \
    libccd-devel \
    eigen3-devel \
    fmt-devel \
    ode-devel \
    urdfdom \
    urdfdom-headers-devel \
    console-bridge-devel \
    qt5-qtquickcontrols

# Dependências Python3
echo
echo "[fedora.sh] Instalando dependências Python3 do PX4"
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
REQUIRED_VERSION="3.11"

# No Fedora mais recente, o pip bloqueia instalações globais por padrão (PEP 668).
# A flag --break-system-packages permite contornar isso (como no Ubuntu 24.04).
if [[ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" == "$REQUIRED_VERSION" ]]; then
	python3 -m pip install --break-system-packages -r "${DIR}/requirements.txt"
else
	if [ -n "$VIRTUAL_ENV" ]; then
		python -m pip install -r "${DIR}/requirements.txt"
	else
		python3 -m pip install --user -r "${DIR}/requirements.txt"
	fi
fi

# NuttX toolchain (arm-none-eabi-gcc)
if [[ $INSTALL_NUTTX == "true" ]]; then

	echo
	echo "[fedora.sh] Instalando Dependências do NuttX ($INSTALL_ARCH)"
	
	sudo dnf install -y --setopt=install_weak_deps=False \
		automake \
		binutils-devel \
		bison \
		curl \
		clang \
		clang-tools-extra \
		flex \
		genromfs \
		gettext \
		gperf \
		elfutils-libelf-devel \
		expat-devel \
		gmp-devel \
		isl-devel \
		libmpc-devel \
		mpfr-devel \
		ncurses-devel \
		ncurses \
		arm-none-eabi-newlib \
		libtool \
		libunwind-devel \
		lldb \
		lld \
		pkgconf \
		screen \
		texinfo \
		uboot-tools \
		util-linux \
		vim-common

	if [[ "${INSTALL_ARCH}" == "x86_64" ]]; then
		sudo dnf install -y --setopt=install_weak_deps=False \
			glibc-devel.i686 \
			libstdc++-devel.i686 \
			arm-none-eabi-gcc-c++ \
			arm-none-eabi-binutils \
			esptool

		echo
		echo "Baixando compiladores Xtensa"
		XTENSA_FILE_NAME=xtensa-esp-elf-13.2.0_20240530-x86_64-linux-gnu.tar.xz
		wget -q -P "$DIR" "https://github.com/espressif/crosstool-NG/releases/download/esp-13.2.0_20240530/$XTENSA_FILE_NAME"
		sudo tar -xf "$DIR/$XTENSA_FILE_NAME" -C /opt
		rm "$DIR/$XTENSA_FILE_NAME"
		
		# Verifica se já está no .bashrc para não duplicar
		if ! grep -q "/opt/xtensa-esp-elf/bin/" "/home/$USER/.bashrc"; then
			echo 'export PATH=$PATH:/opt/xtensa-esp-elf/bin/' >> "/home/$USER/.bashrc"
		fi
	fi

	if [[ "${INSTALL_ARCH}" == "aarch64" ]]; then
		sudo dnf install -y --setopt=install_weak_deps=False \
			gcc-c++-aarch64-linux-gnu \
			gcc-c++-arm-linux-gnu
	fi

	if [ -n "$USER" ]; then
		# Adiciona o usuário ao grupo dialout (acesso porta serial)
		sudo usermod -aG dialout "$USER"
	fi
fi

# Ferramentas de Simulação
if [[ $INSTALL_SIM == "true" ]]; then

	echo
	echo "[fedora.sh] Instalando dependências de simulação do PX4"

	sudo dnf install -y bc

	echo "[fedora.sh] Preparando dependências do Gazebo Harmonic"
	
	# O Fedora não possui gz-harmonic nativamente no repositório padrão.
	# Estas são as dependências para compilação ou para quando adicionar um COPR.
	
	gazebo_packages="libunwind-devel cppzmq-devel"
	
	sudo dnf install -y --setopt=install_weak_deps=False \
		dmidecode \
		$gazebo_packages \
		gstreamer1-plugins-bad-free \
		gstreamer1-plugins-base \
		gstreamer1-plugins-good \
		gstreamer1-plugins-ugly-free \
		gstreamer1-libav \
		eigen3-devel \
		gstreamer1-plugins-base-devel \
		perl-Image-ExifTool \
		opencv-devel \
		libxml2 \
		pkgconf \
		protobuf-compiler \
		protobuf-devel

	echo "--------------------------------------------------------------------------------"
	echo "⚠️ AVISO GAZEMBO: A instalação oficial do 'gz-harmonic' é para Ubuntu."
	echo "Para o Fedora, você precisará adicionar um repositório COPR compatível"
	echo "(ex: sudo dnf copr enable ...) ou compilá-lo a partir do código fonte."
	echo "--------------------------------------------------------------------------------"

	if sudo dmidecode -t system | grep -q "Manufacturer: VMware, Inc." ; then
		if ! grep -q "export SVGA_VGPU10=0" ~/.profile; then
			echo "export SVGA_VGPU10=0" >> ~/.profile
		fi
	fi

fi

echo "[fedora.sh] Finalizado! Não se esqueça de reiniciar a sessão/computador para aplicar as permissões do grupo dialout."

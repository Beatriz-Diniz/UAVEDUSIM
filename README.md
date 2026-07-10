# UAVEDUSIM

🇧🇷 [Leia em Português](#portugues)

#### Prerequisites

- Linux (any current distribution — Ubuntu, Fedora, Arch, openSUSE, etc.)
- Graphical environment with X11 or Wayland/XWayland support
- Internet connection (required on first run, to download dependencies)
- **Docker does not need to be pre-installed** — the script installs it automatically if needed
- **No prior knowledge of Linux, Docker, Gazebo, or PX4 is required** — the whole process is automatic

#### Clone

```bash
git clone --recursive https://github.com/Beatriz-Diniz/UAVEDUSIM.git
```

> ⚠️ **This clone takes a while — that's expected.** The repository uses multiple Git submodules, especially `PX4-Autopilot`, which alone can add up to a few GB. Depending on your connection, it can take several minutes. Don't interrupt the command even if it looks stalled for a while — Git's progress output sometimes stops updating visibly while submodules are downloading.

#### Run

Enter the repository folder and run the script:

```bash
cd UAVEDUSIM
chmod +x uavedusim_gui_docker.sh
./uavedusim_gui_docker.sh
```

> If you get a "Permission denied" error, it means the file doesn't have execute permission yet — the `chmod +x` command above fixes that (only needs to be run once).

> ⚠️ **The first run takes 15 to 30 minutes — this is also expected.** The very first time, the script does all the heavy lifting by itself, with no manual steps required:
> - Installs Docker automatically, if it isn't already installed on your system
> - Creates an isolated Ubuntu container, with GPU acceleration (Intel, AMD, or NVIDIA — auto-detected)
> - Installs Gazebo, PX4, and all other simulator dependencies inside it
> - Builds the attack/mitigation tools (`UAVJamSim`)
>
> **This only happens once.** From the second run onward, the script recognizes everything is already set up and opens the interface within seconds.

#### What to expect when you start a simulation

Once the main UAVEDUSIM interface opens and you start a simulation, **three separate windows will appear** on your screen:

1. **PX4 terminal** — a console window showing the PX4 autopilot's console (SITL) log output. This is where you can see flight status messages, and even type autopilot commands directly (e.g. `commander takeoff`).
2. **Gazebo** — the 3D simulation window, showing the drone and the environment it's flying in.
3. **QGroundControl** — the ground control station, showing the map, flight telemetry, and drone controls (similar to what a real drone pilot would use).

This is expected behavior, not a bug — the three windows work together and all need to stay open during the simulation. Closing one of them (or clicking "Terminate processes" in the interface) will end the simulation for all three.

#### Removing the Docker environment (if needed)

If you ever need to start fresh — for example, if something seems broken, or to free up disk space — you can remove the container and image created by the script:

```bash
# Remove the simulator container (keeps the image)
sudo docker rm -f uavedusim-sim-uid$(id -u)

# Also remove the image (the next run rebuilds everything from scratch)
sudo docker rmi uavedusim:uid$(id -u)
```

This **does not delete anything in the cloned folder** — only the Docker environment. The next time you run `./uavedusim_gui_docker.sh`, it will detect that the container/image no longer exist and automatically redo the full setup (again, 15-30 minutes).

#### Troubleshooting

If something goes wrong during installation or while using the simulator, the full log of each run is saved to `uavedusim_gui.log`, at the root of the repository — that's the first place to check for error messages before asking for help.

---

<a name="portugues"></a>
# UAVEDUSIM (Português)

🇺🇸 [Read in English](#uavedusim)

#### Pré-requisitos

- Linux (qualquer distribuição atual — Ubuntu, Fedora, Arch, openSUSE etc.)
- Ambiente gráfico com suporte a X11 ou Wayland/XWayland
- Conexão com a internet (necessária na primeira execução, para baixar as dependências)
- **Não é necessário ter o Docker instalado previamente** — o script instala automaticamente, se preciso
- **Não é necessário nenhum conhecimento prévio de Linux, Docker, Gazebo ou PX4** — todo o processo é automático

#### Clone

```bash
git clone --recursive https://github.com/Beatriz-Diniz/UAVEDUSIM.git
```

> ⚠️ **Esse clone demora bastante — isso é esperado.** O repositório usa múltiplos submódulos Git, em especial o `PX4-Autopilot`, que sozinho pode chegar a alguns GB. Dependendo da sua conexão, pode levar vários minutos. Não interrompa o comando mesmo que pareça parado por um tempo — a barra de progresso do Git às vezes fica sem atualizar visivelmente durante o download dos submódulos.

#### Executar

Entre na pasta do repositório e rode o script:

```bash
cd UAVEDUSIM
chmod +x uavedusim_gui_docker.sh
./uavedusim_gui_docker.sh
```

> Se aparecer um erro de "Permission denied", é porque o arquivo ainda não tem permissão de execução — o comando `chmod +x` acima resolve isso (só precisa rodar essa linha uma vez).

> ⚠️ **A primeira execução demora entre 15 e 30 minutos — isso também é esperado.** Nessa primeira vez, o script faz *todo* o trabalho pesado sozinho, sem precisar de nenhuma ação manual:
> - Instala o Docker automaticamente, caso ainda não esteja instalado no seu sistema
> - Cria um container Ubuntu isolado, com aceleração de GPU (Intel, AMD ou NVIDIA — detectada automaticamente)
> - Instala dentro dele o Gazebo, o PX4 e todas as demais dependências do simulador
> - Compila as ferramentas de ataque/mitigação (`UAVJamSim`)
>
> **Isso acontece só uma vez.** Da segunda execução em diante, o script reconhece que tudo já está pronto e abre a interface em poucos segundos.

#### O que esperar ao iniciar uma simulação

Assim que a interface principal do UAVEDUSIM abrir e você iniciar uma simulação, **três janelas separadas vão aparecer** na sua tela:

1. **Terminal do PX4** — uma janela de console mostrando a saída do autopiloto PX4 (SITL). É onde você acompanha mensagens de status de voo, e pode até digitar comandos diretamente no autopiloto (ex: `commander takeoff`).
2. **Gazebo** — a janela de simulação 3D, mostrando o drone e o ambiente onde ele está voando.
3. **QGroundControl** — a estação de controle em solo, mostrando o mapa, telemetria de voo e controles do drone (parecido com o que um piloto de drone real usaria).

Isso é o comportamento esperado, não é um bug — as três janelas trabalham juntas e precisam continuar abertas durante a simulação. Fechar qualquer uma delas (ou clicar em "Encerrar processos" na interface) encerra a simulação nas três.

#### Removendo o ambiente Docker (se necessário)

Se em algum momento você precisar recomeçar do zero — por exemplo, se algo parecer quebrado, ou para liberar espaço em disco — pode remover o container e a imagem criados pelo script:

```bash
# Remove o container do simulador (mantém a imagem)
sudo docker rm -f uavedusim-sim-uid$(id -u)

# Remove também a imagem (a próxima execução reconstrói tudo do zero)
sudo docker rmi uavedusim:uid$(id -u)
```

Isso **não apaga nada da pasta clonada** — apenas o ambiente Docker. Na próxima vez que você rodar `./uavedusim_gui_docker.sh`, ele detecta que o container/imagem não existem mais e refaz a instalação completa automaticamente (mais uma vez, 15-30 minutos).

#### Solução de problemas

Se algo der errado durante a instalação ou o uso do simulador, o log completo de cada execução fica salvo em `uavedusim_gui.log`, na raiz do repositório — esse é o primeiro lugar para procurar mensagens de erro antes de pedir ajuda.

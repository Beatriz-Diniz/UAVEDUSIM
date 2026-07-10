# UAVEDUSIM

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

# Installation Guide: Fedora 44 (Wayland/Hyprland)

This document describes the complete and precise step-by-step process to configure the UAVJamSim simulation environment using the modern Gazebo Harmonic version coupled with PX4-Autopilot on Fedora 44+.

## 1. Clone the repository

```bash
git clone https://github.com/Beatriz-Diniz/UAVJamSim.git
cd UAVJamSim
```

## 2. Run the Fedora installation script

```bash
chmod +x fedora.sh
./fedora.sh
```

---

## 3. System Dependencies

### 3.1 Python Tools (colcon and vcstool)

```bash
python3 -m pip install --break-system-packages \
    colcon-common-extensions \
    vcstool
```

### 3.2 QGroundControl Dependencies

```bash
sudo dnf install -y \
    gstreamer1-plugins-bad-free \
    gstreamer1-plugins-bad-free-extras \
    gstreamer1-plugins-good \
    gstreamer1-plugins-ugly-free \
    gstreamer1-libav \
    fuse-libs \
    xcb-util-wm \
    xcb-util-image \
    xcb-util-keysyms \
    xcb-util-renderutil
```

---

## 4. Gazebo Workspace (Harmonic)

### 4.1 Create the workspace

```bash
mkdir -p ~/gazebo_workspace/src
cd ~/gazebo_workspace
```

### 4.2 Import Gazebo modules

```bash
wget https://raw.githubusercontent.com/gazebo-tooling/gazebodistro/master/collection-harmonic.yaml
vcs import src < collection-harmonic.yaml
```

---

### 4.3 Build gz-physics7

```bash
cd ~/gazebo_workspace

rm -rf build/gz-physics7 install/gz-physics7

colcon build \
    --merge-install \
    --packages-select gz-physics7 \
    --cmake-args \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH=/usr/local
```

---

## 5. OGRE-Next (OGRE2)

### 5.1 Clean old versions

```bash
sudo find /usr/local/lib /usr/local/lib64 \
    -maxdepth 1 -name "*Ogre*" -delete 2>/dev/null

sudo find /usr/local/lib/pkgconfig /usr/local/lib64/pkgconfig \
    -maxdepth 1 -iname "*ogre*" -delete 2>/dev/null

sudo rm -rf /usr/local/include/OGRE
sudo rm -rf /usr/local/share/OGRE

sudo ldconfig
```

---

### 5.2 Build OGRE-Next

```bash
cd ~/UAVJamSim

git clone --depth 1 --branch v2-3 https://github.com/OGRECave/ogre-next.git

cd ogre-next
mkdir build && cd build
```

#### CMake configuration

```bash
cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr/local \
    -DCMAKE_INSTALL_LIBDIR=lib64 \
    -DOGRE_LIB_DIRECTORY=lib64
```

#### Build and installation

```bash
make -j$(nproc)
sudo make install
sudo ldconfig
```

---

### 5.3 OGRE symbolic links

#### pkg-config

```bash
cd /usr/local/lib64/pkgconfig
sudo ln -sf OGRE.pc OGRE2.pc
sudo ln -sf OGRE.pc OGRE-Next.pc
```

#### Libraries

```bash
sudo find /usr/local/lib64 -maxdepth 1 -name "libOgre*" \
    -exec ln -sf {} /usr/local/lib/ \;
```

#### Plugins

```bash
sudo ln -s /usr/local/lib/OGRE /usr/local/lib64/OGRE
sudo ldconfig
```

---

## 6. Gazebo Rendering (gz-rendering8)

### 6.1 Cleanup

```bash
cd ~/gazebo_workspace
rm -rf build/gz-rendering8 install/gz-rendering8
```

### 6.2 Forced build

```bash
colcon build \
    --merge-install \
    --packages-select gz-rendering8 \
    --cmake-args \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH=/usr/local \
        -DOGRE2_LIBRARY=/usr/local/lib64/libOgreMain.so \
        -DOGRE2_INCLUDE=/usr/local/include/OGRE \
        -DOGRE2-HlmsPbs=/usr/local/lib64/libOgreHlmsPbs.so \
        -DOGRE2-HlmsUnlit=/usr/local/lib64/libOgreHlmsUnlit.so \
        -DOGRE2-Overlay=/usr/local/lib64/libOgreOverlay.so
```

### 6.3 Validation

```bash
find ~/gazebo_workspace/install -name "libgz-rendering8-ogre2.so"
```

---

## 7. QGroundControl

### 7.1 Download

[https://qgroundcontrol.com/](https://qgroundcontrol.com/)

### 7.2 Run

```bash
chmod +x QGroundControl-x86_64.AppImage
./QGroundControl-x86_64.AppImage
```

---

## 8. Environment Variables

```bash
echo 'export LD_LIBRARY_PATH=/usr/local/lib64:/usr/local/lib:$LD_LIBRARY_PATH' >> ~/.zshrc
echo 'export PKG_CONFIG_PATH=/usr/local/lib64/pkgconfig:/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH' >> ~/.zshrc

source ~/.zshrc
```

---

## 9. Final Execution

```bash
unset DRI_PRIME

# Every time you open a new terminal
source ~/gazebo_workspace/install/setup.zsh
make px4_sitl gz_x500
```

---

# Common Issues

## Error: `Could NOT find OGRE2`

Check:

```bash
ls /usr/local/lib64/libOgreMain.so
```

and:

```bash
pkg-config --list-all | grep -i ogre
```

---

## Error: `gz-rendering8-ogre2.so` missing

Redo:

```bash
rm -rf build/gz-rendering8
rm -rf install/gz-rendering8
```

and compile again.

---

## Linker Error (`libOgre*.so not found`)

Check:

```bash
echo $LD_LIBRARY_PATH
```

It must contain:

```bash
/usr/local/lib64
```

# UAVJamSim

#Dependencia para interface zenity

# Observacao temporaria 
para atualizar o repositorio agora que px4 é submodulo, antes ao clonar o repositorio dava problema com o make pela falta do .git dentro do PX4-Autopilot

#### CLone
```bash
git clone --recursive git@github.com:Beatriz-Diniz/UAVJamSim.git                                                               
```

#### Para fazer os commits
Commitar e dar push no fork do PX4
```bash
cd ~/UAVJamSim/PX4-Autopilot

git status -sb
git add .
git commit -m "Update UAVJamSim PX4 integration"
git push
```

Depois commita o UAVJamSim
```bash
cd ~/UAVJamSim

git status -sb
git add .
git commit -m "Update PX4 submodule"
git push
```

### Para colocar o nosso modelo no local correto no px4
```bash
cd ~/UAVJamSim
ln -sfn "$(pwd)/UAVJamSim/x500_uavjamsim" \
  PX4-Autopilot/Tools/simulation/gz/models/x500_uavjamsim
```


## Simulation Stack

This project is built upon the modern Gazebo Harmonic simulator and PX4 Autopilot v1.17.0, replacing the deprecated Gazebo Classic ecosystem.

| Component | Version |
|---|---|
| PX4 Autopilot | v1.17.0 |
| Gazebo Simulator | Harmonic |
| QGroundControl | Latest Stable |
| Operating System | Fedora 44+ |

## System Installation
If you are using Fedora 44+ with Wayland/Hyprland, follow our dedicated installation guide:
**[Installation Guide for Fedora 44+ (Wayland)](INSTALL_FEDORA_44_WAYLAND.md)**

## Prerequisites
To execute the attacks, you must manage three terminal windows simultaneously:

### 1. Terminal Window A (QGroundControl):
Launch the ground control station to monitor flight telemetry, EKF2 status, and sensor health before takeoff:

```bash
cd ~/path/to/directory/where/QGroundControl/is
chmod +x QGroundControl-x86_64.AppImage
./QGroundControl-x86_64.AppImage
```

### 2. Terminal Window B (Simulation Environment):
Navigate to your PX4-Autopilot directory and launch the desired simulation model:

```bash
cd ~/path/to/PX4-Autopilot
```
Then, choose the appropriate command based on the attack you intend to perform:

**Note:** Depending on the Linux distribution, it may be necessary to export the environment variables during the first setup:

> ```bash
> export LD_LIBRARY_PATH=/usr/local/lib64:/usr/local/lib:$LD_LIBRARY_PATH
> export PKG_CONFIG_PATH=/usr/local/lib64/pkgconfig:/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH
> ```
>
> Additionally, every time a new terminal is opened before running any `make px4_sitl` command, you must source the Gazebo workspace environment:
>
> ```bash
> source ~/gazebo_workspace/install/setup.zsh
> ```

For general sensor attacks (GPS, IMU, etc.):

```bash
make px4_sitl gz_x500
```

For Stream (Camera) attacks:

```bash
make px4_sitl gz_x500_depth
```

For Lidar/Sonar distance sensor attacks:

```bash
make px4_sitl gz_x500_lidar_down
```

For Visual Odometry:

```bash
make px4_sitl gz_x500_vision
```

### 2. Terminal Window C (Attack Framework):
Once the simulation is running, navigate to the build directory of the spoofer project to execute the attack commands:

```bash
cd ~/path/to/UAVJamSim/spoofer/build
```

## Available Attack Commands
### 1. GPS Attacks
#### Absolute Offset: 
Modify the GPS attack parameters to adjust the latitude and longitude offsets in degrees, as well as the altitude offset in meters.

```bash
./spoofer --abs <latitude:double> <longitude:double> <altitude:double>
```
#### Example:

```bash
# Inject a coordinate offset of 0.0005 deg (lat/long) and 10.0m (alt)
./spoofer --abs 0.0005 0.0005 10.0 
```
<video src="https://github.com/user-attachments/assets/2ad34c97-db40-442b-aa06-8d35fbf54dd2" autoplay loop muted playsinline width="100%"></video>


#### Rotation Offset: 
Configure the GPS attack parameters to apply a defined offset value in degrees.

```bash
./spoofer --rot <degrees:double> <rotation:double>
```

#### Example:

```bash
# Apply a heading rotation offset of 45.0 degrees
./spoofer --rot 0.0001 45.0
```

<video src="https://github.com/user-attachments/assets/029b110b-86eb-4dac-a4e8-7218a5dd4e18" autoplay loop muted playsinline width="100%"></video>

### 2. IMU Attacks (Inertial Measurement Unit)
Modify the IMU attack parameters to apply a specific bias value.

```bash
./spoofer --imu <option: int > <index:int > <offset:double>
```

#### Options:
* 1: Attack On (Applies the current values in the active array).
* 2: Disable IMU (Sets all sensor data to 0).
* 3: Load Offset (Saves the offset value into the temporary array; must be followed by Option 1 to apply).
* 4: Clear Arrays (Resets both temporary and active arrays to 0).
* 5: Stop Attack (Disables the injection without clearing the data).

Sensor Indices:
* 0: Linear Acceleration X
* 1: Linear Acceleration Y
* 2: Linear Acceleration Z
* 3: Angular Velocity X
* 4: Angular Velocity Y
* 5: Angular Velocity Z

#### Example:

```bash
# Load a 5.0 offset into the Angular Velocity Z (Index 5) buffer
./spoofer --imu 3 5 5.0

# Start the attack using the saved offsets
./spoofer --imu 1 2 2.0

# Stop the attack
./spoofer --imu 5 2 2.0

# Clear all attack buffers
./spoofer --imu 4 2 2.0
```

<video src="https://github.com/user-attachments/assets/1abda474-fdaf-48c0-8125-b9cea0393f09" autoplay loop muted playsinline width="100%"></video>

### 3. Stream Attacks
Alters the live video stream captured by the onboard camera in real time.

```bash
./spoofer --stream <option>
```

#### Options:
* 1: Flip Stream: Vertically or horizontally mirrors the camera feed.
* 2: Square Noise: Injects a square-shaped noise pattern into the center area of the frame, obscuring visual targets or navigation markers.

#### Example:
```bash
./spoofer --stream 1
```

#### Visualizing the Attack:
To observe the stream in real-time and confirm the impact of the attack, run the following command in a separate terminal:

```bash
gst-launch-1.0 udpsrc port=5600 ! application/x-rtp, media=video, clock-rate=90000, encoding-name=H264 ! rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! autovideosink
```

<video src="https://github.com/user-attachments/assets/9c4d6e62-b1d5-42ab-8e7a-af60e27115b0" autoplay loop muted playsinline width="100%"></video>

### 4. Motor Attacks
Modify the motor speed in the simulator, which indirectly affects the performance of the autopilot motor outputs.

```bash
./spoofer --motor <option:int> <index:int> <thrust:int>
```
#### Options:
* 1: Attack On/Off: Enables or disables the injection for a specific motor index.
* 2: Disable All: Instantly sets all motor speeds to 0.
* 0: Stop Attack: Stops the active motor injection command.

#### Parameters:
* Index: The ID of the motor (e.g., 0 for motor #0).

* Thrust: The raw speed value (Range: 0 to 1450).

#### Example:
```bash
# Apply a thrust speed of 500 to motor #0
./spoofer --motor 1 0 500

# Emergency kill: set all motor speeds to 0
./spoofer --motor 2 0 0
```

<video src="https://github.com/user-attachments/assets/63047101-f93a-428c-883b-af5a21a17d6c" autoplay loop muted playsinline width="100%"></video>

### 5. Magnetometer Spoofing
Modify the magnetometer attack parameters to adjust the magnetic field intensity and swap the X and Y axes of the magnetometer data.

```bash
./spoofer --mag <option:int>
```

#### Options:
* 1: Swap Axes: Swaps the `magnetic_field.x()` and `magnetic_field.y()` values.

#### Example:
```bash
# Corrupt the heading estimate by swapping the X and Y magnetic field axes
./spoofer --mag 1
```

#### Verification:
To confirm the success of the attack, you must compare the raw sensor readings before and after the attack using the PX4 console.

##### Monitor the data before the attack:
In the PX4 terminal (Terminal B), run:

```bash
listener vehicle_magnetometer
```
Observe the magnetometer_ga vector values.

<img width="432" height="162" alt="image" src="https://github.com/user-attachments/assets/034f01cd-df93-4734-b9ed-873218ae0baf" />

##### Execute the attack:
In the Spoofer terminal (Terminal C), run:

```bash
./spoofer --mag 1
```

##### Verify the change:
Run the listener command again:

```bash
listener vehicle_magnetometer
```

You will notice that the X and Y values in the magnetometer_ga vector have been swapped, confirming the data injection into the firmware.

<img width="441" height="144" alt="image" src="https://github.com/user-attachments/assets/b9a682a8-046f-4951-8082-d215ac5919eb" />


### 6. Distance Sensor Attacks (Lidar/Sonar)
Forces constant offsets on range-finding sensors.

#### Lidar: 
Modify the lidar sensor data by applying a constant offset to the distance measurements.

```bash
./spoofer --lidar <option:int> <distance_offset:double>
```

#### Options:
* 1: Attack On/Off: Enables or disables the constant offset injection.

#### Parameters:
* Distance Offset: The Lidar sensor has a functional range of [0.17, 15.0] meters. Note that the sensor will output null (no reading) if the final calculated distance exceeds 15 meters.

#### Example:
```bash
# Apply a 2.5m offset to the Lidar readings
./spoofer --lidar 1 2.5
```

#### Verification:
To confirm the success of the attack, you must compare the sensor distance readings before and after the attack using the PX4 console.

##### Monitor the data before the attack:
In the PX4 terminal (Terminal B), run:

```bash
listener distance_sensor
```

Observe the current_distance value.

<img width="679" height="241" alt="image" src="https://github.com/user-attachments/assets/14c3b3ad-340c-4930-921c-5021dd6c066e" />

##### Execute the attack:
In the Spoofer terminal (Terminal C), run the command with your desired offset (e.g., 2.5 meters):

```bash
./spoofer --lidar 1 2.5
```

##### Verify the change:
Run the listener command again in the PX4 terminal:

```bash
listener distance_sensor
```

You will observe that the current_distance value has been updated to reflect the injected offset, confirming the data manipulation within the firmware.

<img width="645" height="239" alt="image" src="https://github.com/user-attachments/assets/a0e3936a-942a-45a2-95e7-7d5112e505e1" />


#### Sonar:
Modify the sonar sensor data by applying a constant offset to the distance measurements.

```bash
./spoofer --sonar <option:int> <distance_offset:double>
```

#### Options:
* 1: Attack On/Off: Enables or disables the constant offset injection.

#### Parameters:
* Distance Offset: The sonar sensor has a functional range of [0.02, 5.0] meters. Note that the sensor only reflects objects within this maximum distance; if no object is detected, the sensor defaults to the maximum range (5.0m).

#### Example:
```bash
# Apply a 0.5m offset to the Sonar readings
./spoofer --sonar 1 0.5
```
#### Verification:
To confirm the success of the attack, you must compare the sensor distance readings before and after the attack using the PX4 console.

##### Monitor the data before the attack:
In the PX4 terminal (Terminal B), run:

```bash
listener distance_sensor
```

Observe the current_distance value.

<img width="640" height="240" alt="image" src="https://github.com/user-attachments/assets/ecc181b9-6558-45a1-ac43-99fb0a94a5f0" />

##### Execute the attack:
In the Spoofer terminal (Terminal C), run the command with your desired offset (e.g., 0.5 meters):

```bash
./spoofer --sonar 1 0.5
```

##### Verify the change:
Run the listener command again in the PX4 terminal:

```bash
listener distance_sensor
```

You will observe that the current_distance value has been updated to reflect the injected offset, confirming the data manipulation within the firmware.

<img width="635" height="236" alt="image" src="https://github.com/user-attachments/assets/51cdab96-1c52-4bc1-b01a-adc7b931262e" />

### 7. Barometer Attacks
Modify the altitude attack parameters to apply a specific absolute altitude offset, automatically updating the corresponding pressure and temperature values based on the new altitude.

```bash
./spoofer --baro <option:int> <offset:double>
```

#### Options:
* 1: Attack On/Off: Enables or disables the altitude offset injection.

#### Parameters:
* Offset: The altitude offset (in meters) to be applied to the current reading.

#### Example:
```bash
# Apply a 10-meter altitude offset
./spoofer --baro 1 10.0
```
#### Verification:
To confirm the success of the attack, you must compare the pressure and temperature readings before and after the attack using the PX4 console.

##### Monitor the data before the attack:
In the PX4 terminal (Terminal B), run:

```bash
listener sensor_baro
```

Observe the pressure and temperature values.

<img width="415" height="141" alt="image" src="https://github.com/user-attachments/assets/2404de00-25ef-49f1-aaa6-d01bc78ccb81" />


##### Execute the attack:
In the Spoofer terminal (Terminal C), run the command:

```bash
./spoofer --baro 1 10.0
```

#### Verify the change:
Run the listener command again in the PX4 terminal:

```bash
listener sensor_baro
```

You will observe that the pressure value has decreased (simulating a higher altitude) and the temperature has adjusted according to the atmospheric lapse rate, confirming the barometric spoofing.

<img width="403" height="139" alt="image" src="https://github.com/user-attachments/assets/8dabc99e-3bd3-4269-be16-32bb63f29205" />

# New attacks

### GPS Jamming Attacks
GPS jamming attacks degrade or interrupt GNSS data before it is consumed by the PX4 estimator. These attacks are controlled from the spoofer terminal.

```bash
./spoofer --jam <type:int> <intensity:double>
```

#### Types
* 1: Noise Jamming: adds continuous noise to the GPS position, altitude, and velocity data.
* 2: Blackout Jamming: interrupts GPS publication, simulating GNSS signal loss.
* 3: Pulsed Jamming: alternates between ramp-up, full jamming, decay, and recovery periods.

#### Examples

```bash
# Noise jamming with intensity 80
./spoofer --jam 1 80

# GPS blackout
./spoofer --jam 2

# Pulsed jamming with intensity 0.8
./spoofer --jam 3 0.8
```

#### Noise Jamming Example:
<video src="https://github.com/user-attachments/assets/3dafd0f0-68dc-4be8-894b-f25e35913e78" autoplay loop muted playsinline width="100%"></video>

Blackout Jamming Example:
<video src="https://github.com/user-attachments/assets/81ed6763-288e-4139-be7c-d67d9ff427a8" autoplay loop muted playsinline width="100%"></video>

Pulsed Jamming Example:
**Note:** When running pulsed jamming with `--jam 3 0.3`, each execution may produce a different behavior. Depending on the timing of the jamming pulses, the UAV trajectory can be similar to the video or significantly different.
<video src="https://github.com/user-attachments/assets/366efe39-b1e4-4e13-a7a1-ab130a58ba49" autoplay loop muted playsinline width="100%"></video>


### GPS Jamming Mitigation
To mitigate GPS jamming attacks, the simulation must run with the X500 Quadrotor with Visual Odometry:

```bash
make px4_sitl gz_x500_vision
```

Then, open a new terminal in the same directory used by the spoofer and enable the mitigation:

```bash
cd ~/path/to/UAVJamSim/spoofer/build
./attack_mitigator --on
```

To disable the mitigation:

```bash
./attack_mitigator --off
```

# To do list

Mitigation status for the current attack set:

* [x] GPS Noise Jamming attack
* [x] GPS Blackout Jamming attack
* [x] GPS Pulsed Jamming attack
* [x] GPS Absolute Offset attack
* [x] GPS Rotation Offset attack
* [ ] IMU attack
* [ ] Stream Camera attack
* [ ] Motor attack
* [ ] Magnetometer attack
* [ ] Lidar attack
* [ ] Sonar attack
* [ ] Barometer attack

Future simulation improvements:

* [ ] Implement a UAV model with obstacle avoidance capability
* [ ] Implement a UAV model with the maximum number of available sensors


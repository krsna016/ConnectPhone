# 📱 ConnectPhone

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/Platform-macOS-blue.svg)]()
[![Backend: Python 3](https://img.shields.io/badge/Language-Python%203-blue.svg)]()
[![Bridges: Swift](https://img.shields.io/badge/Bridges-Swift-orange.svg)]()

`ConnectPhone` is an industry-grade integration engine and desktop dashboard designed to seamlessly bridge your Android device with macOS. It leverages high-performance screen/camera mirroring (`scrcpy` core) and custom native macOS bindings (Swift APIs) to deliver bi-directional media controls, live HD recording/snapshotting, automated file synchronizations, and an **Auto-Biometric Bridge (macOS Touch ID to Android Passcode)**.

The software offers both an **interactive Terminal CLI Command Center** and a **Web UI Dashboard** loaded with visual stats, low-latency preferences, and manufacturer-specific profiles.

---

## 🚀 Key Features

* **🖥️ Screen & Camera Mirroring**: High-fidelity, low-latency screen and camera previews via USB or Wireless Debugging utilizing customized `scrcpy` pipes.
* **🔒 Biometric Bridge (Touch ID)**: Automatically unlock your Android device or individual app locks using macOS native Touch ID biometrics, powered by a compiled Swift bridge and background daemon.
* **🎙️ Audio Selection & Calibration**: Route sound from your phone's microphone, system audio, or Mac earbuds/bluetooth devices. Features dynamic audio buffer adjustments and sync offsets to eliminate latency desync.
* **🎥 Live Media Controls**: Capture high-definition video clips or snapshot framebuffers directly from the mirroring stream to your Mac Desktop with a single click or keyboard command.
* **📂 Folder Sync Watcher**: Automatically monitor folder structures on your phone (like screenshots/recordings) and transfer new files instantly to your Mac.
* **⚙️ Manufacturer Tweak Engine**: Activate target presets to automatically handle device quirks (Nothing OS Glyph controls, Xiaomi overlays, Samsung Knox sync, OnePlus daemon keep-alive).

---

## 📋 System Requirements

To run this application on macOS, you must ensure the following system-level dependencies are installed:

1. **Android Debug Bridge (ADB)**: Standard Android console utility.
2. **scrcpy**: High-performance rendering engine (v2.0+ recommended).
3. **ffmpeg**: Media processor for audio routing, extraction, and video compilation.
4. **Xcode Command Line Tools**: Required to compile native Swift helpers (`swiftc`).

### 📦 Homebrew Installation
You can install all system requirements in a single command using Homebrew:
```bash
brew install android-platform-tools scrcpy ffmpeg
```

*Note: Xcode Command Line Tools are automatically downloaded and installed on first run if they are not already present on your system.*

---

## 🛠️ Installation & First-Time Setup

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/[your-username]/ConnectPhone.git
   cd ConnectPhone
   ```

2. **Connect Your Android Device**:
   - **USB Connection**: Connect your phone via USB and trust the computer when prompted for USB Debugging authorization.
   - **Wireless Connection**: 
     1. Enable **Wireless Debugging** in your phone's Developer Options.
     2. Tap **Pair device with pairing code**. Note the IP, Port, and Code.
     3. Start `ConnectPhone` and navigate to the connection manager to input pairing coordinates.

3. **Required Developer Options on Android**:
   - Enable **USB Debugging**.
   - **Xiaomi / Redmi / Poco OS**: Enable **USB Debugging (Security Settings)**. This is a crucial requirement to allow simulated remote key inputs and touch clicks.
   - **Samsung Galaxy**: Samsung profiles handle custom Knox PIN entries. Enter your Android PIN in the Preferences page to allow automated Touch ID unlocking.

---

## 🕹️ Project Architecture & Components

```
ConnectPhone/
├── ConnectPhone.py         # Main Interactive Terminal CLI Command Center
├── ConnectPhoneUI.py       # Local Web Dashboard Server (Python http.server)
├── unlock.py               # Biometric Watcher & Android Passcode Typist Engine
├── touch_id.swift          # Swift source bridging macOS LocalAuthentication Touch ID
├── get_window_id.swift     # Swift source referencing Quartz Window Services
├── requirements.txt         # Documentation of dependencies
├── LICENSE                 # MIT License details
└── ui/                     # Web UI Frontend Assets
    ├── index.html          # Web dashboard structure
    ├── index.css           # Vanilla CSS layout with premium design tokens
    └── index.js            # Frontend control behaviors and metrics polling
```

### Technical Blueprint:
- **`touch_id.swift`**: Compiles into a native binary `touch_id_helper`. When executed, it brings up the native macOS authentication modal. If successful, it exits with return code `0`.
- **`get_window_id.swift`**: Compiles into a native binary `get_window_id`. It dynamically queries Quartz Window Services for active mirroring window IDs, allowing snapshotting utilities to target the precise window coordinates instantly.
- **Biometric Watcher Daemon**: Runs a background thread parsing `adb logcat` output. It detects screen locks, secure lock prompts, or App Lock overlay displays, and automatically summons the Mac Touch ID helper, subsequently injecting the saved PIN keycodes upon authentication.

---

## 🖥️ Running the Application

### Option A: The Web Dashboard (Recommended)
Launch the premium web interface containing live metrics, diagnostics, preferences, connection configurations, and device profiles:
```bash
python3 ConnectPhoneUI.py
```
This starts a lightweight server and opens http://localhost:8282 in your default browser.

### Option B: The Terminal Command Center
Run the interactive CLI command deck:
```bash
python3 ConnectPhone.py
```
This launches a command menu to test screen mirroring, run file sync, adjust settings, and diagnose connection ports.

---

## 📱 Manufacturer Profiles & Quirks Tweak Engine

ConnectPhone includes target optimizations tailored to eliminate brand-specific bugs:

| Brand Profile | Tweak Strategy | Actionable Control / Workaround |
| :--- | :--- | :--- |
| **Generic Android** | Default Preset | 60 FPS Target Frame Rate, Opus audio encoding, standard UHID keyboard. |
| **Nothing Phone** | Glyph LEDs & High Refresh | Unlocks backend controls for back LED Glyphs (`settings put secure glyph_interface_enabled`), launches system Glyph settings, and automatically overrides video stream to **120 FPS**. |
| **Xiaomi / MIUI** | Input Permission Bypass | Provides visual guides to enable USB Security Debugging, preventing MIUI from silently ignoring simulated keystrokes and Touch ID entries. |
| **Samsung Galaxy** | Knox PIN Synchronization | Automatically appends `KEYCODE_ENTER` keyevents to biometric unlock queues to bypass Knox PIN verification delays. |
| **OnePlus / Oppo** | Persistent ADB Keep-Alive | Spawns a background daemon on the server that pings the device every 30 seconds, preventing OxygenOS from aggressively killing active ADB processes. |

---

## 🔧 Troubleshooting

#### 1. "Device Offline" or ADB Connection Dropouts
- Ensure your phone is connected on the same Wi-Fi subnet.
- Android randomizes the connection port for Wireless Debugging every time it connects. Check the **IP address & Port** in your phone settings (e.g., `192.168.29.201:38947`) and reconnect using the connection manager.

#### 2. Audio is Laggy or Desynced
- Navigate to **Preferences** on the dashboard and adjust the **Audio Preset** to `voice_communication` (lower bitrate/buffer) or configure the **Audio Sync Offset** (default: `0.80`s) to match your output speaker latency.

#### 3. Keystrokes Not Working on Password/Lockscreen
- Xiaomi phones require **USB Debugging (Security Settings)** to be turned ON under Developer Options. Ensure you have a SIM card inserted in the phone, as Xiaomi system security requires cell carrier validation to toggle this setting.

#### 4. Touch ID Daemon Not Prompting
- Verify that **Auto-Prompt macOS Touch ID** is checked under Preferences, and your Android PIN is stored correctly in the configuration page.

---

## 🛡️ License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for the full license text.

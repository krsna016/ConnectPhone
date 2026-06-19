# 📱 ConnectPhone

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/Platform-macOS-blue.svg)]()
[![Backend: Python 3](https://img.shields.io/badge/Language-Python%203-blue.svg)]()
[![Bridges: Swift](https://img.shields.io/badge/Bridges-Swift-orange.svg)]()

`ConnectPhone` is an industry-grade integration engine and desktop dashboard designed to seamlessly bridge your Android device with macOS. It leverages high-performance screen/camera mirroring (`scrcpy` core) and custom native macOS bindings (Swift APIs) to deliver bi-directional media controls, live HD recording, instant snapshotting, and real-time system metrics.

The software offers both an **interactive Terminal CLI Command Center** and a **Web UI Dashboard** loaded with visual stats, audio controls, and low-latency preference managers.

---

## 🚀 Key Features

* **🖥️ Screen & Camera Mirroring**: High-fidelity, low-latency screen and camera previews via USB or Wireless Debugging utilizing customized `scrcpy` pipes.
* **🎙️ Audio Selection & Calibration**: Route sound from your phone's microphone, system audio, or Mac earbuds/bluetooth devices. Features dynamic audio buffer adjustments and sync offsets to eliminate latency desync.
* **🎥 Live Media Controls**: Capture high-definition video clips or snapshot framebuffers directly from the mirroring stream to your Mac Desktop with a single click or keyboard command.
* **📈 Live Device Metrics & Diagnostics**: View real-time device stats, battery wear, memory allocation, and connection status in a clean visual format.

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

---

## 🛠️ Installation & First-Time Setup

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/krsna016/ConnectPhone.git
   cd ConnectPhone
   ```

2. **Connect Your Android Device**:
   - **USB Connection**: Connect your phone via USB and trust the computer when prompted for USB Debugging authorization.
   - **Wireless Connection**: 
     1. Enable **Wireless Debugging** in your phone's Developer Options.
     2. Tap **Pair device with pairing code** or check connection details to note IP and Port.
     3. Start `ConnectPhone` and navigate to the connection manager to input connection coordinates.

---

## 🕹️ Project Architecture & Components

```
ConnectPhone/
├── ConnectPhone.py         # Main Interactive Terminal CLI Command Center
├── ConnectPhoneUI.py       # Local Web Dashboard Server (Python http.server)
├── get_window_id.swift     # Swift source referencing Quartz Window Services
├── requirements.txt         # Documentation of dependencies
├── LICENSE                 # MIT License details
└── ui/                     # Web UI Frontend Assets
    ├── index.html          # Web dashboard structure
    ├── index.css           # Vanilla CSS layout with premium design tokens
    └── index.js            # Frontend control behaviors and metrics polling
```

---

## 🖥️ Running the Application

### Option A: The Web Dashboard (Recommended)
Launch the premium web interface containing live metrics, diagnostics, preferences, and connection configurations:
```bash
python3 ConnectPhoneUI.py
```
This starts a lightweight server and opens http://localhost:8282 in your default browser.

### Option B: The Terminal Command Center
Run the interactive CLI command deck:
```bash
python3 ConnectPhone.py
```
This launches a command menu to test screen mirroring, run diagnostic logs, and adjust preferences.

---

## 🛡️ License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for the full license text.

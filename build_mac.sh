#!/bin/bash
set -e

echo "🚀 Building ConnectPhone macOS App..."

# Build in an isolated environment so system Python policy cannot produce a
# partially populated bundle.
PYTHON_BIN=".venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    python3 -m venv .venv
fi
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt pyinstaller

# Convert logo.png to ConnectPhone.icns natively using macOS tools
if [ -f "ui/logo.png" ]; then
    echo "🎨 Generating App Icon..."
    mkdir -p build_icon.iconset
    sips -z 16 16     ui/logo.png --out build_icon.iconset/icon_16x16.png
    sips -z 32 32     ui/logo.png --out build_icon.iconset/icon_16x16@2x.png
    sips -z 32 32     ui/logo.png --out build_icon.iconset/icon_32x32.png
    sips -z 64 64     ui/logo.png --out build_icon.iconset/icon_32x32@2x.png
    sips -z 128 128   ui/logo.png --out build_icon.iconset/icon_128x128.png
    sips -z 256 256   ui/logo.png --out build_icon.iconset/icon_128x128@2x.png
    sips -z 256 256   ui/logo.png --out build_icon.iconset/icon_256x256.png
    sips -z 512 512   ui/logo.png --out build_icon.iconset/icon_256x256@2x.png
    sips -z 512 512   ui/logo.png --out build_icon.iconset/icon_512x512.png
    sips -z 1024 1024 ui/logo.png --out build_icon.iconset/icon_512x512@2x.png
    iconutil -c icns build_icon.iconset -o ui/ConnectPhone.icns
    rm -rf build_icon.iconset
else
    echo "⚠️ ui/logo.png not found, skipping icon generation..."
fi

echo "📦 Packaging App with PyInstaller..."
# We use --windowed (or --noconsole) to make it a standalone .app bundle
# We use --add-data to include the ui folder
"$PYTHON_BIN" -m PyInstaller --noconfirm \
    --name "ConnectPhone" \
    --windowed \
    --icon "ui/ConnectPhone.icns" \
    --add-data "ui:ui" \
    --add-data "core:core" \
    --hidden-import=uvicorn \
    --hidden-import=fastapi \
    --hidden-import=pydantic \
    --hidden-import=zeroconf \
    --hidden-import=qrcode \
    --hidden-import=PIL \
    --hidden-import=aiortc \
    --hidden-import=av \
    --hidden-import=numpy \
    --hidden-import=pyaudio \
    --hidden-import=pytesseract \
    --hidden-import=webview \
    --hidden-import=webview.platforms.cocoa \
    --hidden-import=core.keychain \
    --add-binary "touch_id_helper:." \
    --add-data "touch_id.swift:." \
    --add-data "get_window_id.swift:." \
    ConnectPhoneUI.py

# PyInstaller's initial ad-hoc signature can become invalid after nested
# resources are collected. Re-sign the completed bundle before distribution.
codesign --force --deep --sign - "dist/ConnectPhone.app"
codesign --verify --deep --strict "dist/ConnectPhone.app"

echo "🧹 Cleaning up temporary build files..."
rm -rf build
rm ConnectPhone.spec

echo "✅ Build Complete! Your App is located at: dist/ConnectPhone.app"
echo "You can move it to your Applications folder."

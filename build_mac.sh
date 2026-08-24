#!/bin/bash
set -e

echo "🚀 Building ConnectPhone macOS App..."

APP_VERSION="2.1.1"
APP_BUILD="211"
BUNDLE_ID="com.krsna016.ConnectPhone"
SIGN_IDENTITY="${CONNECTPHONE_SIGN_IDENTITY:--}"

# Build in an isolated environment so system Python policy cannot produce a
# partially populated bundle.
PYTHON_BIN=".venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    python3 -m venv .venv
fi
"$PYTHON_BIN" -m pip install --disable-pip-version-check -r requirements.txt

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
    --clean \
    --name "ConnectPhone" \
    --windowed \
    --osx-bundle-identifier "$BUNDLE_ID" \
    --icon "ui/ConnectPhone.icns" \
    --add-data "ui:ui" \
    --add-data "core:core" \
    --hidden-import=zeroconf \
    --hidden-import=ifaddr \
    --hidden-import=qrcode \
    --hidden-import=qrcode.image.svg \
    --exclude-module=PIL \
    --exclude-module=numpy \
    --hidden-import=webview \
    --hidden-import=webview.platforms.cocoa \
    --hidden-import=core.keychain \
    --add-binary "touch_id_helper:." \
    --add-data "touch_id.swift:." \
    --add-data "get_window_id.swift:." \
    ConnectPhoneUI.py

# PyInstaller does not expose all required macOS privacy metadata as CLI
# switches, so make the bundle identity and permission prompts explicit.
PLIST="dist/ConnectPhone.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $BUNDLE_ID" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $APP_VERSION" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $APP_BUILD" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 13.0" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSApplicationCategoryType string public.app-category.utilities" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSHumanReadableCopyright string Copyright 2026 ConnectPhone" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSLocalNetworkUsageDescription string ConnectPhone discovers and connects to Android devices on your local Wi-Fi network." "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string ConnectPhone uses the Mac microphone only when you select microphone audio for a recording." "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSScreenCaptureUsageDescription string ConnectPhone captures a mirrored phone frame only when you request a snapshot." "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity dict" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity:NSAllowsLocalNetworking bool true" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSBonjourServices array" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSBonjourServices:0 string _adb-tls-connect._tcp" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSBonjourServices:1 string _adb-tls-pairing._tcp" "$PLIST"

# CONNECTPHONE_SIGN_IDENTITY may name a Developer ID Application identity.
# '-' deliberately produces an ad-hoc development build when no identity is
# available; such a build is not eligible for notarized distribution.
if [ "$SIGN_IDENTITY" = "-" ]; then
    # Hardened-runtime library validation requires all nested code to share a
    # real signing team. Ad-hoc development builds have no Team ID, so sign
    # without runtime; Developer ID release builds retain hardened runtime.
    codesign --force --deep --entitlements ConnectPhone.entitlements --sign - "dist/ConnectPhone.app"
else
    codesign --force --deep --options runtime --entitlements ConnectPhone.entitlements --sign "$SIGN_IDENTITY" "dist/ConnectPhone.app"
fi
codesign --verify --deep --strict "dist/ConnectPhone.app"

echo "🧹 Cleaning up temporary build files..."
rm -rf build
rm ConnectPhone.spec

echo "✅ Build Complete! Your App is located at: dist/ConnectPhone.app"
echo "You can move it to your Applications folder."

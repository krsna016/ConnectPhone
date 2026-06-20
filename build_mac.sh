#!/bin/bash
set -e

echo "🚀 Building ConnectPhone macOS App..."

# Check for pyinstaller
if ! command -v pyinstaller &> /dev/null
then
    echo "PyInstaller not found. Installing..."
    pip3 install pyinstaller --break-system-packages || pip3 install pyinstaller
fi

# Convert logo.png to ConnectPhone.icns natively using macOS tools
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

echo "📦 Packaging App with PyInstaller..."
# We use --windowed (or --noconsole) to make it a standalone .app bundle
# We use --add-data to include the ui folder
pyinstaller --noconfirm \
    --name "ConnectPhone" \
    --windowed \
    --icon "ui/ConnectPhone.icns" \
    --add-data "ui:ui" \
    ConnectPhoneUI.py

echo "🧹 Cleaning up temporary build files..."
rm -rf build
rm ConnectPhone.spec

echo "✅ Build Complete! Your App is located at: dist/ConnectPhone.app"
echo "You can move it to your Applications folder."

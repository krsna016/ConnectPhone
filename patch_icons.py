import re

html_file = "/Users/anurag/ConnectPhone/ui/index.html"
js_file = "/Users/anurag/ConnectPhone/ui/index.js"

replacements = {
    "⚠️": "warning",
    "📱": "smartphone",
    "🔗": "link",
    "🖥️": "desktop_windows",
    "📈": "monitoring",
    "📁": "folder",
    "⚙️": "settings",
    "📚": "menu_book",
    "🔄": "refresh",
    "▶️": "play_arrow",
    "⏹️": "stop",
    "⏸️": "pause",
    "📸": "photo_camera",
    "🎥": "videocam",
    "⌨️": "keyboard",
    "⬇️": "download",
    "🖼️": "image",
    "⚡": "bolt",
    "🔍": "search",
    "🚀": "rocket_launch",
    "🔌": "cable",
    "✅": "check_circle",
    "❌": "cancel",
    "📋": "content_paste",
    "💾": "save",
    "🔋": "battery_full"
}

def patch_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for emoji, icon in replacements.items():
        html_icon = f'<i class="material-symbols-outlined">{icon}</i>'
        content = content.replace(emoji, html_icon)
    
    # Add stylesheet if not present
    if filepath.endswith('.html') and 'material-symbols-outlined' not in content:
        content = content.replace('</head>', '    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" />\n</head>')

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

patch_file(html_file)
patch_file(js_file)
print("Done patching icons!")

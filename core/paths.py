"""macOS-native writable locations and one-time legacy migration."""

import os
import shutil

APP_SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/ConnectPhone")
LOG_DIR = os.path.expanduser("~/Library/Logs/ConnectPhone")
CONFIG_PATH = os.path.join(APP_SUPPORT_DIR, "config.json")
LOG_PATH = os.path.join(LOG_DIR, "connectphone.log")
LEGACY_CONFIG_PATH = os.path.expanduser("~/.connectphone_config.json")


def prepare_directories():
    for directory in (APP_SUPPORT_DIR, LOG_DIR):
        os.makedirs(directory, mode=0o700, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass


def migrate_legacy_config():
    prepare_directories()
    if not os.path.exists(CONFIG_PATH) and os.path.isfile(LEGACY_CONFIG_PATH):
        shutil.copy2(LEGACY_CONFIG_PATH, CONFIG_PATH)
        os.chmod(CONFIG_PATH, 0o600)
    return CONFIG_PATH

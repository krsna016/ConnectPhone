#!/bin/zsh
set -euo pipefail

if (( $# != 1 )); then
  print -u2 "Usage: $0 /path/to/ConnectPhone.app.zip"
  exit 2
fi

archive=${1:A}
target=/Applications/ConnectPhone.app
backup_root=$HOME/ConnectPhone-backups
timestamp=$(date +%Y%m%d-%H%M%S)
work_dir=$(mktemp -d /private/tmp/connectphone-restore.XXXXXX)
trap 'rm -rf "$work_dir"' EXIT

if [[ ! -f "$archive" ]]; then
  print -u2 "Release archive not found: $archive"
  exit 1
fi

ditto -x -k "$archive" "$work_dir"
candidate=$work_dir/ConnectPhone.app

if [[ ! -d "$candidate" ]]; then
  print -u2 "Archive does not contain ConnectPhone.app"
  exit 1
fi

codesign --verify --deep --strict "$candidate"
mkdir -p "$backup_root"

if [[ -d "$target" ]]; then
  osascript -e 'tell application id "com.krsna016.ConnectPhone" to quit' 2>/dev/null || true
  mv "$target" "$backup_root/ConnectPhone-before-restore-$timestamp.app"
fi

ditto "$candidate" "$target"
codesign --verify --deep --strict "$target"
print "Restored ConnectPhone and verified its signature."

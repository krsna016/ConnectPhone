#!/bin/zsh
set -euo pipefail

repo_dir=${0:A:h:h}
app_dir=/Applications/ConnectPhone.app
checkpoint_dir=$repo_dir/metadata/checkpoints/v2.0.0-working

if [[ ! -d "$app_dir" ]]; then
  print -u2 "ConnectPhone is not installed at $app_dir"
  exit 1
fi

codesign --verify --deep --strict "$app_dir"

(
  cd "$app_dir"
  shasum -a 256 -c "$checkpoint_dir/bundle-files.sha256"
)

print "Installed ConnectPhone matches the v2.0.0 working checkpoint."

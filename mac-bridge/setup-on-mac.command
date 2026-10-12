#!/bin/bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"
if [[ ! -x .toolchain/bridge-venv/bin/python ]]; then
  printf '%s\n' 'Install the Mac bridge dependencies first; see mac-bridge/README.txt.'
  exit 1
fi
printf '%s\n' 'This setup runs only on your Mac. Your Apple password and device passcode are not saved.'
.toolchain/bridge-venv/bin/python mac-bridge/bridge.py setup
.toolchain/bridge-venv/bin/python mac-bridge/bridge.py invite
printf '%s\n' 'Press Return to close this window.'
read -r _

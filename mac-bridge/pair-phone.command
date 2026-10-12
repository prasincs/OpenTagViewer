#!/bin/bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"
.toolchain/bridge-venv/bin/python mac-bridge/bridge.py invite

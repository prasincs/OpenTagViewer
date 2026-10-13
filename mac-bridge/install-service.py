#!/usr/bin/env python3
"""Install the Mac bridge as a persistent, per-user launchd service."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

LABEL = "dev.homefind.macbridge"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="This Mac's specific Tailscale IP")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--skip-dependencies", action="store_true",
                        help="Use an already installed bridge virtual environment")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("The persistent service requires macOS")
    project = Path(__file__).resolve().parent.parent
    venv = project / ".toolchain/bridge-venv"
    python = venv / "bin/python"
    if not args.skip_dependencies:
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r",
                        str(project / "mac-bridge/requirements.txt")], check=True)
    if not python.is_file():
        parser.error("Bridge environment missing; rerun without --skip-dependencies")
    helper = project / ".toolchain/vault-helper"
    subprocess.run(["swiftc", "-O", str(project / "mac-bridge/SecureVault.swift"), "-o", str(helper)], check=True)
    bridge = project / "mac-bridge/bridge.py"
    state = Path.home() / "Library/Application Support/HomeFindBridge"
    existing = state / "server.json"
    if existing.exists():
        config = json.loads(existing.read_text())
        if config["host"] != args.host or config["port"] != args.port:
            parser.error("Existing pairing uses a different address; rotate explicitly as documented")
        for name in ("connection.json", "server.pem", "server-key.pem"):
            if not (state / name).is_file():
                parser.error("Incomplete existing pairing; repair or rotate explicitly")
    else:
        subprocess.run([str(python), str(bridge), "pair", "--host", args.host,
                        "--port", str(args.port)], check=True)
    logs = [state / "bridge.log", state / "bridge-error.log"]
    for log in logs:
        log.touch(mode=0o600, exist_ok=True)
        log.chmod(0o600)
    agents = Path.home() / "Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    target = agents / (LABEL + ".plist")
    config = dict(Label=LABEL, ProgramArguments=[str(python), str(bridge), "serve"],
                  WorkingDirectory=str(project), RunAtLoad=True, KeepAlive=True,
                  ThrottleInterval=30, StandardOutPath=str(logs[0]),
                  StandardErrorPath=str(logs[1]))
    domain = "gui/" + str(os.getuid())
    subprocess.run(["launchctl", "bootout", domain + "/" + LABEL],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    temporary = target.with_suffix(".plist.tmp")
    with temporary.open("wb") as out:
        plistlib.dump(config, out)
    temporary.chmod(0o600)
    temporary.replace(target)
    for attempt in range(5):
        started = subprocess.run(["launchctl", "bootstrap", domain, str(target)], capture_output=True)
        if started.returncode == 0:
            break
        if started.returncode != 5 or attempt == 4:
            raise RuntimeError("Mac service bootstrap failed; check the local LaunchAgent status")
        time.sleep(0.5)  # bootout can return before launchd releases the old service label.

    print("Mac bridge installed. Keep this checkout at its current path.")
    print("Complete Apple setup locally with mac-bridge/setup-on-mac.command.")
    print("Open mac-bridge/pair-phone.command, then tap Scan Mac QR code on Android.")


if __name__ == "__main__":
    main()

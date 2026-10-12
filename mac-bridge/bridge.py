"""Read-only HTTPS bridge. Apple credentials and accessory keys never enter its API."""
from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import secrets
import signal
import ssl
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_STATE = Path.home() / "Library/Application Support/HomeFindBridge"
MAX_STATE = 8 * 1024 * 1024
MAX_ITEMS = 500


def atomic_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    os.chmod(path, 0o600)


class SecureStore:
    """An encrypted file whose wrapping key is held in the login Keychain, never on disk."""
    def __init__(self, directory: Path):
        self.directory = directory
        self.identity = hashlib.sha256(str(directory.resolve()).encode()).hexdigest()

    def _key(self, create: bool = False) -> bytes:
        if sys.platform != "darwin":
            raise RuntimeError("Mac login Keychain is required")
        from keyring.backends.macOS import Keyring
        keychain = Keyring()
        value = keychain.get_password("HomeFindBridge", self.identity)
        if value is None:
            if not create:
                raise RuntimeError("Mac Keychain key is unavailable")
            value = base64.b64encode(secrets.token_bytes(32)).decode()
            keychain.set_password("HomeFindBridge", self.identity, value)
        return base64.b64decode(value, validate=True)

    def read(self) -> dict | None:
        path = self.directory / "apple-state.enc"
        if not path.exists():
            return None
        if path.stat().st_size > MAX_STATE:
            raise ValueError("Stored state exceeds the limit")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        blob = path.read_bytes()
        return json.loads(AESGCM(self._key()).decrypt(blob[:12], blob[12:], b"HomeFindBridge/v1"))

    def write(self, value: dict) -> None:
        # The original password is forbidden even inside the encrypted persisted state.
        if value.get("account", {}).get("account", {}).get("password") is not None:
            raise ValueError("Refusing to persist an Apple password")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = json.dumps(value, separators=(",", ":")).encode()
        if len(raw) > MAX_STATE:
            raise ValueError("Stored state exceeds the limit")
        nonce = secrets.token_bytes(12)
        atomic_private(self.directory / "apple-state.enc", nonce + AESGCM(self._key(True)).encrypt(
            nonce, raw, b"HomeFindBridge/v1"))


def session_without_password(account) -> dict:
    state = account.to_json()
    state["account"]["password"] = None
    return state


def public_item(accessory, report, label: str | None = None) -> dict:
    # Never use report.to_json(): that API can include the private encryption key.
    identifier = accessory.identifier or hashlib.sha256(
        json.dumps(accessory.to_json(), sort_keys=True).encode()).hexdigest()
    result = {"id": hashlib.sha256(identifier.encode()).hexdigest()[:24],
              "name": (label or accessory.name or "AirTag")[:120], "location": None}
    if report is not None:
        lat, lon = float(report.latitude), float(report.longitude)
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            return result
        result["location"] = {"latitude": lat, "longitude": lon,
                              "timestamp": int(report.timestamp.timestamp() * 1000),
                              "accuracy": float(report.horizontal_accuracy)}
    return result


class AppleSource:
    def __init__(self, store: SecureStore):
        self.store = store

    async def fetch(self) -> dict:
        state = self.store.read()
        if state is None:
            return {"schema": 1, "status": "setup_required", "items": [], "fetched_at": 0}
        from findmy import AsyncAppleAccount, FindMyAccessory
        account = AsyncAppleAccount.from_json(state["account"],
            anisette_libs_path=str(self.store.directory / "anisette-libs.bin"))
        accessories = [FindMyAccessory.from_json(x) for x in state["accessories"]]
        try:
            # Bound the number of keys searched: never scan a tag's entire pairing history.
            now = datetime.now(timezone.utc)
            items = []
            for accessory in accessories[:MAX_ITEMS]:
                keys = list(accessory.keys_between(now - timedelta(hours=24), now))
                if len(keys) > 500:
                    raise ValueError("Accessory key window exceeds the query limit")
                histories = await asyncio.wait_for(account.fetch_location_history(keys), timeout=90)
                reports = [r for rows in histories.values() for r in rows] if isinstance(histories, dict) else histories
                latest = max(reports, key=lambda r: r.timestamp) if reports else None
                items.append(public_item(accessory, latest))
            self.store.write({"account": session_without_password(account),
                              "accessories": [a.to_json() for a in accessories]})
            return {"schema": 1, "status": "ready", "items": items,
                    "fetched_at": int(time.time() * 1000)}
        finally:
            await account.close()


class Snapshot:
    def __init__(self, source, interval: int = 60):
        self.source, self.interval = source, interval
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.value = {"schema": 1, "status": "setup_required", "items": [], "fetched_at": 0}

    def read(self) -> dict:
        with self.lock:
            return dict(self.value)

    def run(self) -> None:
        # The same event loop lives across polls, including Anisette's async resources.
        loop = asyncio.new_event_loop()
        try:
            while not self.stop.is_set():
                try:
                    value = loop.run_until_complete(self.source.fetch())
                except Exception:
                    # No tracebacks, server responses, usernames or tokens in HTTP/log output.
                    value = self.read()
                    value["status"] = "mac_error"
                with self.lock:
                    self.value = value
                self.stop.wait(self.interval)
        finally:
            loop.close()


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, snapshot: Snapshot, token_hash: str):
        self.snapshot, self.token_hash = snapshot, token_hash
        super().__init__(address, BridgeHandler)
        self.rate_lock = threading.Lock()
        self.requests = {}

    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(10)
        if hasattr(self, "tls_context"):
            conn = self.tls_context.wrap_socket(conn, server_side=True)
        return conn, address


class BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "HomeFindBridge"
    sys_version = ""

    def log_message(self, *args):
        pass

    def reply(self, code: int, value: dict) -> None:
        raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        # Each device gets only a read-only location token; no Apple credentials or key endpoints.
        header = self.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
        if len(token) != 43 or not hmac.compare_digest(
                hashlib.sha256(token.encode()).hexdigest(), self.server.token_hash):
            self.reply(401, {"error": "unauthorized"})
            return
        if self.path != "/v1/items":
            self.reply(404, {"error": "not_found"})
            return
        now = time.monotonic()
        host = self.client_address[0]
        with self.server.rate_lock:
            previous = self.server.requests.get(host, 0)
            if now - previous < 1:
                self.reply(429, {"error": "rate_limited"})
                return
            if len(self.server.requests) > 100:
                self.server.requests.clear()
            self.server.requests[host] = now
        self.reply(200, self.server.snapshot.read())

    def do_POST(self):
        self.reply(405, {"error": "read_only"})


def create_pairing(directory: Path, host: str, port: int) -> dict:
    # A pasted/file-imported certificate fingerprint establishes trust without a public CA.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Home Find Mac Bridge")])
    now = datetime.now(timezone.utc)
    san = x509.IPAddress(ipaddress.ip_address(host))
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=365)).add_extension(x509.SubjectAlternativeName([san]), False)
            .sign(key, hashes.SHA256()))
    atomic_private(directory / "server.pem", cert.public_bytes(serialization.Encoding.PEM))
    atomic_private(directory / "server-key.pem", key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    token = secrets.token_urlsafe(32)
    endpoint = f"https://[{host}]:{port}" if ":" in host else f"https://{host}:{port}"
    pairing = {"version": 1, "endpoint": endpoint,
               "certificate_sha256": cert.fingerprint(hashes.SHA256()).hex(), "token": token}
    atomic_private(directory / "connection.json", json.dumps(pairing).encode())
    atomic_private(directory / "server.json", json.dumps({"host": host, "port": port,
        "token_hash": hashlib.sha256(token.encode()).hexdigest()}).encode())
    return pairing


async def setup(directory: Path) -> None:
    # Interactive ONLY. Inputs never appear in argv, chat, files or server output.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
    from exporter import icloud
    from findmy import FindMyAccessory
    from findmy.reports.anisette import LocalAnisetteProvider
    store = SecureStore(directory)
    previous = store.read()
    if previous is not None:
        from findmy import AsyncAppleAccount
        account = AsyncAppleAccount.from_json(previous["account"],
            anisette_libs_path=str(directory / "anisette-libs.bin"))
    else:
        account = icloud.make_account(libs_path=str(directory / "anisette-libs.bin"),
                                     identity_path=directory / "device-identity.json")
    email = input("Apple account email: ").strip()
    password = getpass.getpass("Apple account password (used once, not saved): ")
    async def choose(options):
        for i, option in enumerate(options):
            print(f"{i + 1}. {option}")
        return int(input("Choose a verification method: ")) - 1
    async def code():
        return getpass.getpass("Verification code: ").strip()
    try:
        await icloud.log_in(account, email, password, choose_second_factor=choose, get_code=code)
        del password
        client = await icloud.open_client(account, identity=icloud.ClientIdentity(
            serial=account.serial, device_name=account.device_name or "Home Find Mac Bridge"))
        async with client:
            options = await client.recovery_options()
            if not options.recoverable:
                raise RuntimeError("No recoverable Apple device was found")
            for i, record in enumerate(options.recoverable):
                print(f"{i + 1}. {record.serial}")
            record = options.recoverable[int(input("Choose an Apple device whose unlock passcode you know: ")) - 1]
            passcode = getpass.getpass("That device's unlock passcode (used once, not saved): ")
            await client.unlock(record, passcode)
            del passcode
            accessories = await client.accessories()
            if not accessories:
                raise RuntimeError("No locatable tags were found")
            for i, accessory in enumerate(accessories):
                print(f"{i + 1}. {accessory.name or 'AirTag'}")
            choices = input("Tag numbers to share with this phone, separated by commas: ").strip()
            indexes = sorted({int(x.strip()) - 1 for x in choices.split(",")})
            if not indexes or any(i < 0 or i >= len(accessories) for i in indexes):
                raise ValueError("Invalid tag selection")
            selected = [accessories[i] for i in indexes]
            store.write({"account": session_without_password(account),
                         "accessories": [a.to_json() for a in selected]})
        # Only selected tag keys and a password-free session survive setup.
        # No keychain peer is enrolled and no broad keychain/zone keys are persisted.
        print(f"Stored {len(selected)} selected tag(s) securely on this Mac. You can close this window.")
    finally:
        await account.close()


def main() -> None:
    logging.getLogger().setLevel(logging.CRITICAL)
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["pair", "setup", "serve"])
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.command == "pair":
        if not args.host:
            parser.error("--host must be the Mac's specific LAN or Tailscale IP")
        if (args.state / "server.json").exists():
            parser.error("Existing pairing left untouched. Rotate it deliberately by removing only bridge connection files.")
        create_pairing(args.state, args.host, args.port)
        print("Created private connection file:", args.state / "connection.json")
    elif args.command == "setup":
        try:
            asyncio.run(setup(args.state))
        except Exception as error:
            print("Setup did not complete:", type(error).__name__, "— check the local Apple setup and try again.")
            raise SystemExit(1)
    else:
        config = json.loads((args.state / "server.json").read_text())
        source = AppleSource(SecureStore(args.state))
        snapshot = Snapshot(source)
        server = BridgeServer((config["host"], config["port"]), snapshot, config["token_hash"])
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(args.state / "server.pem", args.state / "server-key.pem")
        server.tls_context = tls
        thread = threading.Thread(target=snapshot.run, daemon=True)
        thread.start()
        print("Read-only Mac bridge is running; Apple setup is performed locally.", flush=True)
        try:
            server.serve_forever()
        finally:
            snapshot.stop.set()
            server.server_close()


if __name__ == "__main__":
    main()

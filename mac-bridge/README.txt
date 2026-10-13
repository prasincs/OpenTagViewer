Home Find Mac bridge

Mode: selected AirTag locations are fetched/decrypted on your Mac. Android
connects using a separate read-only token and pinned HTTPS certificate. It does
not receive your Apple password, Apple session, device passcode or tag keys.
The map retrieves OpenStreetMap tiles; tile providers can infer the viewed area.
This is an unofficial FindMy.py bridge, not an Apple-supported API.

Persistent service:
- Server: https://<your-Mac-Tailscale-IP>:8766 (specific interface only)
- Private pairing file: ~/Library/Application Support/HomeFindBridge/connection.json
- User launch agent: ~/Library/LaunchAgents/dev.homefind.macbridge.plist
- Service: dev.homefind.macbridge (starts at login and restarts after exit)
- The Mac must be awake, online and connected to Tailscale. Android must be on
  the same authorized tailnet. Nothing is opened to the public internet here.

Finish Apple setup on the Mac:
1. Open setup-on-mac.command in this folder.
2. Type the owning Apple account and password in that LOCAL terminal, answer
   2FA, choose an existing Apple device and type its unlock passcode.
3. Choose only the tag numbers you want this phone to see.
4. Wait for the stored-tag confirmation. The bridge picks it up on its next poll.
   If macOS asks to access the HomeFindBridge login-Keychain item, review and
   approve that local request. Never put these passwords/passcodes into chat.

Setup reads the Find My key material once, but does not enrol a permanent
keychain peer. It can register an authentication client on your Apple account,
using the exporter identity with a serial beginning 0PENTAGX. Keep that identity
if you want the session to keep working. The original Apple password/device
passcode are not persisted. Selected tag keys and the Apple session are stored
in apple-state.enc with AES-256-GCM; the random wrapping key is stored through
macOS's native login Keychain backend. When Apple expires/revokes the session,
rerun setup locally. New tags must be selected locally in a new setup run.
Do not confuse the bridge TLS certificate/private key with your AirTag keys.
Those transport files and the pairing file are private files (0600 in a 0700
folder). A pairing file gives access to locations: do not publish it.

First-setup pairing (recommended):
Open mac-bridge/pair-phone.command on your Mac, then tap Scan Mac QR code
on Android. Camera permission is needed only for scanning. The Mac displays
a live pairing window and a single-use QR. Each code expires after five minutes,
and the service replaces it automatically while the window is waiting. Both
devices need Tailscale connectivity. No connection file transfer is required.
Successful Apple setup reopens the existing pairing window, or creates one.
Keep the window open: it polls every two seconds and shows waiting, confirming,
paired, failed or cancelled. After pairing, it also waits for Apple setup to be
ready (or reports a Mac retrieval error). Local Apple sign-in failure is shown
as a negative result and cancels pending QR authorization. No Apple password
or raw authentication error enters the window. No manual refresh is necessary.

Creating the QR locally authorizes access to the selected tags' locations.
The QR includes endpoint, certificate pin and a random 256-bit temporary secret,
not the permanent bearer token. The phone verifies certificate/hostname/expiry
before POST /v1/pair with that secret in its Authorization header. The Mac
consumes the invitation atomically and returns only the read-only connection
credential plus a temporary confirmation receipt. The phone verifies the API,
saves its credential encrypted, then confirms success. The Mac only reports
paired after that confirmation. A phone failure sends a negative result; an
unconfirmed claim times out after two minutes. Wrong guesses do not consume it; attempts are rate limited. Replay,
expiry and reissued invitations are rejected. An intercepted or photographed
live QR could authorize the first claimant: show it privately. To cancel an
unused invitation, use Cancel pairing in the window. Closing/inactivating the
window stops renewal; after two minutes without a heartbeat its pending code
cannot be claimed. Cancellation after a claim does not independently revoke a
location token already delivered; it rejects confirmation and the new phone
build removes its pending credential. Token rotation revokes all copies.
A new QR invalidates the previous one. After a lost response, generate a new QR.
The phone stores only the returned connection after a successful location-API
check. Existing manually imported tokens continue working. This version shares
the existing location token; revocation still rotates all copies, not one phone.
The pairing window is served by the persistent bridge on loopback only, at an
ephemeral 127.0.0.1 port. It checks Host/Origin, requires an independent UI
capability, disables caching and never returns Apple account state. A private
pairing-flow.json holds only temporary setup state outside Git. A new window
invalidates the previous controller/QR/receipt. Pairing needs app version 9 or
newer for the final confirmation; older builds cannot confirm this live flow.

On Android (manual fallback):
Open the app, choose Import Mac connection file (or paste its contents), and
connect. Connection credentials are encrypted with Android Keystore. A pinned
certificate mismatch is an error, not an invitation to bypass TLS. The first
screen reports that Mac setup is still needed until the Mac has real tag data.
Reports can be old or unavailable; their timestamps are shown. No fake tags or
positions are inserted. Viewing/refreshing does not trigger arbitrary Mac code.
No automatic phone Apple login occurs. The existing direct login option remains
available only through an explicit button. Data from earlier direct use is not
erased by switching modes; unlink/logout/erase are separate choices.
The new mode displays locations; phone-side authenticated ringing, pairing,
UWB finding and owner-tag BLE matching are not provided by this bridge.

Server API:
GET /v1/items, Authorization: Bearer <read-only bridge token>.
Schema 1, status ready/setup_required/mac_error, items with opaque ID/name and
optional latitude/longitude/report timestamp/accuracy. No key/account-export
route exists. POST /v1/pair issues a read-only connection; POST /v1/pair/confirm reports the
phone result using a temporary receipt. Other POSTs are rejected.
Responses are no-store. Polls and pairing attempts are rate limited.
The phone does not cache report coordinates in its SQLite tag database.

Stop/revoke access:
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/dev.homefind.macbridge.plist
This stops the server. Phone Disconnect erases the phone's connection, but
cannot revoke another copy of the token. To re-pair with new credentials, stop
the server, remove ONLY connection.json, server.json, server.pem and
server-key.pem from the private state directory, run the pair command below,
then bootstrap the launch agent again. Preserve apple-state.enc and its Keychain
key if you want the existing Mac session/tag keys to survive. Do not casually
clear the entire state folder or delete the Keychain wrapping key.

Install from a fresh fork checkout (Python 3.12 and Tailscale required):
python3.12 mac-bridge/install-service.py --host <your-Mac-Tailscale-IP>
The installer creates the virtual environment, pins dependencies, generates
private pairing material, and installs/restarts the per-user LaunchAgent.
Keep the checkout at its installed path; rerun the installer after moving it.
Existing pairing credentials are preserved. Complete local setup afterwards.
The LaunchAgent starts at user login, not before login, and does not prevent sleep.
Secrets stay outside the checkout in Application Support and login Keychain.

Manual dependency setup / foreground service:
python3.12 -m venv .toolchain/bridge-venv
.toolchain/bridge-venv/bin/python -m pip install -r mac-bridge/requirements.txt
.toolchain/bridge-venv/bin/python mac-bridge/bridge.py pair --host <specific-Mac-IP>
.toolchain/bridge-venv/bin/python mac-bridge/bridge.py setup
.toolchain/bridge-venv/bin/python mac-bridge/bridge.py serve

Verification:
.toolchain/bridge-venv/bin/python -m unittest discover -s mac-bridge/tests -v
JVM and managed UI commands are indexed in CONTRIBUTING.md.
Mac credential onboarding and real tag retrieval need human inputs and hardware;
passing synthetic tests must not be reported as live Apple/AirTag verification.

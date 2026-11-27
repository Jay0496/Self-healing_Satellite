# Space–ground link security

This document explains how the onboard agent (Raspberry Pi) and the ground station
(Mac) protect every packet they exchange, how keys are provisioned and rotated, and how
this behaves during the simulated communication blackout. It covers **REQ-SEC-01** and
**REQ-SEC-02** from PR#1.

## 1. What is protected

```
             Pi (flight)                          broker (Pi Mosquitto,           Mac (ground)
                                                   reached over Tailscale)
 simulator ─► satellite/flight/telemetry/#  (onboard sensor bus, local)
                    │
           collector + SQLite buffer
                    │ seal()                                                  open()
                    ├──► .../downlink/telemetry ───────────────────────────► mqtt_ground ─► SQLite
                    ├──► .../downlink/events    ───────────────────────────►     │
                    ├──► .../downlink/command-result ──────────────────────► send_command
                    │ open()                                                  seal()
                    ◄─── .../uplink/ack     ◄──────────────────────────────  mqtt_ground
                    ◄─── .../uplink/command ◄── (Ed25519-signed, then sealed) send_command
```

Every message on a `downlink/*` or `uplink/*` topic, and every body on the legacy HTTP
path (`blackout_collector` → `receiver` `/ingest`, `/ingest-events` and their replies), is
a sealed envelope. A receiver that gets anything else (plain JSON, a tampered packet, a
replay, a packet meant for another topic, or one made with the wrong key) drops it,
prints a `SECURITY REJECT ... reason=<CODE>` line, and counts it. **Nothing rejected is
ever stored, acknowledged or executed.**

## 2. Envelope format (`shared/security/channel.py`)

```json
{"v":1,"alg":"AES-256-GCM","kid":59860453,"sid":"pi01-flight","sess":"9f1c...","seq":42,
 "ts":1795813606.12,"iv":"<12 random bytes, base64>","ct":"<ciphertext + 16-byte tag, base64>"}
```

| Field | Purpose |
|---|---|
| `kid` | Key epoch = `floor(ts / rotation_seconds)`; selects the derived AES key |
| `sid`, `sess`, `seq` | Sender id, random per-process session id, counter → replay detection |
| `ts` | Seal time; packets older/newer than 300 s are rejected (`STALE`) |
| `iv` | Fresh `os.urandom(12)` for **every** packet (GCM nonce is never reused) |
| `ct` | AES-256-GCM ciphertext and authentication tag |

The header fields **and the channel name** (MQTT topic or HTTP path) are bound into GCM
as Additional Authenticated Data. Changing any header byte, or re-publishing a valid
downlink packet onto the uplink command topic, fails tag verification (`AUTH_FAILED`).

Receive-side checks, in order: well-formed envelope → freshness window → key epoch
matches timestamp and is within ±1 epoch of now → GCM tag → replay window. The replay
window is updated only **after** the tag verifies, so forged packets cannot block real ones.
MQTT QoS-1 re-deliveries are therefore rejected as `REPLAY`, which is harmless: the
existing application ACK/retry logic re-sends a batch as a *new* packet if no ACK arrives.

Rejection codes: `UNENCRYPTED`, `MALFORMED`, `STALE`, `KEY_EPOCH`, `AUTH_FAILED`, `REPLAY`
(and `UNSIGNED` / `BAD_SIGNATURE` for commands and updates, see §4).

## 3. Keys and rotation (`shared/security/keystore.py`)

| File in the secrets directory | Pi | Mac | Purpose |
|---|---|---|---|
| `link_master.key` | ✔ | ✔ | 32-byte master secret (hex). Link keys are derived from it |
| `command_signing_ed25519.pem` | ✘ | ✔ | Ground's private key for signing commands and updates |
| `command_verify_ed25519.pem` | ✔ | ✔ | Public key the Pi uses to verify those signatures |

* The secrets directory is `~/.g29_secrets` (override with `G29_SECRETS_DIR`). The code
  **refuses to load keys from inside the repository**, `.gitignore` blocks `*.key`/`*.pem`,
  and `tests/security` includes a secret scan that fails if key material is ever tracked.
  Keys are never printed; only 16-character SHA-256 fingerprints are, so you can check the
  Pi and the Mac hold the same keys.
* On Linux/macOS, key files must be `chmod 600`; the loader rejects group/world-readable keys.
* **Rotation (≤ 24 h):** the AES key for epoch *n* is `HKDF-SHA256(master, info="epoch=n;period=P")`.
  A new key automatically comes into use every `P` seconds (default 86 400 s = 24 h; set
  `G29_KEY_ROTATION_SECONDS`, 10 … 86 400, **identically on both devices**, e.g. `30` for a
  live demonstration). Both ends derive the key independently, so rotation needs no key
  exchange and keeps working when the link is down. Knowing one epoch key reveals neither
  the master nor any other epoch's key. A `KEY_ROTATION link key epoch a -> b` line is
  printed when the sender changes key.
* Replacing the master secret itself is a manual re-provisioning step (`keytool init --force`).

### Provisioning (once)

On the Mac (ground):

```bash
python -m pip install -r requirements.txt
python -m shared.security.keytool init      # creates ~/.g29_secrets/*
```

Copy only the two Pi files (never the signing key) and lock down permissions:

```bash
ssh pi 'mkdir -p ~/.g29_secrets && chmod 700 ~/.g29_secrets'
scp ~/.g29_secrets/link_master.key ~/.g29_secrets/command_verify_ed25519.pem pi:~/.g29_secrets/
ssh pi 'chmod 600 ~/.g29_secrets/*'
```

On the Pi: `python -m pip install -r requirements.txt` (inside `.venv`), then
`python -m shared.security.keytool status` — the `link_master.key` fingerprint and
`signing_identity` must match the Mac's. `scripts/demo.py` performs the same check and
prints the fingerprints before it starts anything.

## 4. Signed commands and software updates (`shared/security/signing.py`, `updates.py`)

* `ground.ingestion.send_command` signs each command (`command_id`, `type`, `expires_at`,
  `issued_by`) with Ed25519, **then** seals it. The Pi opens the envelope, verifies the
  signature with the public key, and only then runs the existing allow-list/expiry checks
  in `shared/mqtt_protocol.validate_command`. Someone who stole only the link key still
  cannot command the satellite: the Pi replies `REJECTED: UNSIGNED` / `BAD_SIGNATURE`.
* Software fixes / updated FSM rules: after sandbox tests and operator approval,
  `python -m ground.approvals.sign_update --package fix.tar.gz --approved-by NAME` writes a
  signed manifest (SHA-256, size, name, approver). On the Pi,
  `python -m flight.security.update_verifier --package fix.tar.gz` (or
  `shared.security.updates.verify_package`) must succeed before anything is installed.
  Unsigned, edited, wrong-key and swapped-package cases are all rejected.

## 5. Behaviour with the blackout simulator and normal link

* **ONLINE:** batches are sealed when published; ground opens, stores, then returns a sealed ACK.
* **BLACKOUT:** nothing is transmitted and uplink packets are ignored (unchanged behaviour).
  Telemetry and events keep accumulating in the Pi's SQLite buffer in plaintext (data at
  rest stays on the device).
* **RECOVERING:** buffered rows are sealed **at transmission time**, so a blackout of any
  length (including one that crosses a key rotation) is delivered under the current key
  with fresh timestamps. A packet an attacker captured before the blackout is `STALE`
  afterwards.

## 6. How it was verified

| Requirement | Target | Evidence |
|---|---|---|
| REQ-SEC-01 AES-256-GCM, unique IV, 100 % packets | all link packets | All ground-facing publishes/HTTP bodies go through `SecureChannel.seal`; `test_unique_iv_per_packet` (2 000 packets, 2 000 IVs) |
| REQ-SEC-01 reject tampered | 100 % | `test_tampered_ciphertext_rejected` flips every ciphertext/tag byte; `test_every_header_field_is_authenticated`; live attack 2 |
| REQ-SEC-01 reject replayed | 100 % | `TestReplay`; live attacks 1 and 7 |
| REQ-SEC-01 ≤ 5 ms / 1 KB | ≤ 5 ms | `test_1kb_frame_under_5ms` (seal + open measured 0.15 ms on a Windows laptop; re-run on the Pi with `pytest -s`) |
| REQ-SEC-02 rotation ≤ 24 h | ≤ 24 h | Default 86 400 s, policy capped at 24 h (`test_rotation_policy_capped_at_24h`), `TestKeyRotation`, live run with 30 s rotation across a blackout |
| REQ-SEC-02 no plaintext keys in source/logs | 0 | Repo-path refusal, `.gitignore`, `TestSecretScan`, `test_key_never_in_repr_or_packet` |
| REQ-SEC-02 reject unsigned updates | 100 % | `TestSignedUpdates`, `TestCommandSignatures`; live attack 6 |

Run the automated checks: `python -m pytest` (from the repository root).

Live check (both roles on one machine, Docker Mosquitto on `localhost:1883`):

```bash
export G29_KEY_ROTATION_SECONDS=30
python scripts/demo.py pi --blackout-after 40 --blackout-duration 15        # terminal 1
python scripts/demo.py mac --broker-host 127.0.0.1 --frames 70 --rate-hz 1  # terminal 2
python scripts/security_attack_demo.py --broker-host 127.0.0.1             # terminal 3, while ONLINE
python -m ground.ingestion.send_command --broker-host 127.0.0.1 --command GET_STATUS
```

Expected: ground receives every record (910/910) with `ONLINE -> BLACKOUT -> RECOVERING ->
ONLINE`; the Pi and ground print 6 `SECURITY REJECT` lines between them plus
`Command attack-…: REJECTED` (unsigned); the signed `GET_STATUS` succeeds and reports
security counters; `KEY_ROTATION` lines appear.

## 7. Limitations (honest scope)

* The link key is symmetric and shared by Pi and ground, so either side could forge the
  other's *link* packets; command authority is protected separately by Ed25519, which only
  ground holds. A compromised master secret compromises all epoch keys until re-provisioned.
* The onboard sensor topic `satellite/flight/telemetry/#` is treated as the spacecraft's
  internal bus and is not encrypted. In the two-computer demo the simulator runs on the Mac
  and publishes over Tailscale, which provides transport encryption for that hop.
* The Mosquitto broker has no per-client accounts; Tailscale device authorisation controls
  who can reach it. Encryption above means a reachable attacker can still only drop or
  delay packets, not read, forge or replay them.
* Clocks on the Pi and Mac must agree within 300 s (NTP is normally enough).
* Plain `GET /status` on the legacy HTTP receiver is unauthenticated (counts only).

"""Authenticated encryption for every packet on the space-ground link (REQ-SEC-01).

Wire format (one JSON object per MQTT message or HTTP body)::

    {"v":1, "alg":"AES-256-GCM", "kid":<key epoch>, "sid":<sender id>,
     "sess":<random per-process session>, "seq":<per-session counter>,
     "ts":<unix seconds at sealing>, "iv":<b64 12-byte nonce>, "ct":<b64 ciphertext+tag>}

* Confidentiality + integrity: AES-256-GCM with a fresh random 96-bit IV per packet.
* Every header field **and the channel name** (MQTT topic / HTTP path) are bound as
  Additional Authenticated Data, so a packet cannot be edited, re-labelled, or replayed
  onto a different topic (e.g. a downlink ACK cannot be reflected as an uplink command).
* Replay rejection: a packet must be fresh (``|now - ts| <= freshness``) and its
  ``(sid, sess, seq)`` must not have been accepted before (sliding window per session).
  The replay check runs only *after* the GCM tag verifies, so forged packets can never
  poison the replay window.
* Key rotation (REQ-SEC-02): the AES key is derived per epoch from the provisioned master
  secret with HKDF-SHA256; a new epoch (and therefore a new key) starts every
  ``rotation_seconds`` (default 24 h). Both ends derive the same key independently, so
  rotation needs no key exchange and keeps working through a communication blackout.

Packets are sealed at *transmission* time, not when buffered, so telemetry stored during
a blackout is always sent under the current key with a fresh timestamp and sequence.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import os
import threading
import time
from collections import Counter
from typing import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ENVELOPE_VERSION = 1
ALGORITHM = "AES-256-GCM"
MASTER_KEY_BYTES = 32
IV_BYTES = 12
DEFAULT_ROTATION_SECONDS = 86_400
DEFAULT_FRESHNESS_SECONDS = 300.0
REPLAY_WINDOW = 4096
MAX_ENVELOPE_BYTES = 4_000_000
_HKDF_SALT = b"g29-space-ground-link-v1"
_HEADER_FIELDS = ("v", "alg", "kid", "sid", "sess", "seq", "ts")


class SecurityError(ValueError):
    """A packet, signature, or key failed a security check. ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode(value: object, field: str) -> bytes:
    if not isinstance(value, str):
        raise SecurityError("MALFORMED", f"{field} must be base64 text")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SecurityError("MALFORMED", f"{field} is not valid base64") from exc


def derive_epoch_key(master_key: bytes, epoch: int, rotation_seconds: int) -> bytes:
    """One-way per-epoch key: knowing one epoch key reveals neither the master nor other epochs."""
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT,
        info=f"epoch={epoch};period={rotation_seconds}".encode("ascii"),
    ).derive(master_key)


class _ReplayWindow:
    """Accepts each sequence number at most once, tolerating out-of-order delivery."""

    def __init__(self, size: int = REPLAY_WINDOW) -> None:
        self.size = size
        self.highest = 0
        self.seen: set[int] = set()

    def accept(self, seq: int) -> bool:
        if seq <= self.highest - self.size or seq in self.seen:
            return False
        self.seen.add(seq)
        if seq > self.highest:
            self.highest = seq
            floor = seq - self.size
            if len(self.seen) > 2 * self.size:
                self.seen = {s for s in self.seen if s > floor}
        return True


class SecureChannel:
    """Seals outgoing packets and opens incoming packets for one endpoint (Pi or ground)."""

    def __init__(
        self,
        master_key: bytes,
        sender_id: str,
        *,
        rotation_seconds: int = DEFAULT_ROTATION_SECONDS,
        freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS,
        clock: Callable[[], float] = time.time,
        on_key_epoch: Callable[[int, int], None] | None = None,
    ) -> None:
        if not isinstance(master_key, bytes) or len(master_key) != MASTER_KEY_BYTES:
            raise ValueError("master key must be exactly 32 bytes (AES-256)")
        if not sender_id or len(sender_id) > 64:
            raise ValueError("sender_id must be 1-64 characters")
        if rotation_seconds < 10:
            raise ValueError("rotation_seconds must be at least 10")
        if freshness_seconds <= 0:
            raise ValueError("freshness_seconds must be positive")
        self._master = master_key
        self.sender_id = sender_id
        self.rotation_seconds = int(rotation_seconds)
        self.freshness_seconds = float(freshness_seconds)
        self._clock = clock
        self._on_key_epoch = on_key_epoch
        self._session = os.urandom(8).hex()
        self._seq = 0
        self._lock = threading.Lock()
        self._ciphers: dict[int, AESGCM] = {}
        self._sessions: dict[tuple[str, str], tuple[_ReplayWindow, float]] = {}
        self._last_seal_epoch: int | None = None
        self.sealed = 0
        self.opened = 0
        self.rejected: Counter[str] = Counter()

    def __repr__(self) -> str:  # never expose key material in logs
        return f"SecureChannel(sender_id={self.sender_id!r}, rotation={self.rotation_seconds}s)"

    def epoch_at(self, timestamp: float) -> int:
        return int(timestamp // self.rotation_seconds)

    @property
    def current_epoch(self) -> int:
        return self.epoch_at(self._clock())

    def _cipher(self, epoch: int) -> AESGCM:
        cipher = self._ciphers.get(epoch)
        if cipher is None:
            cipher = AESGCM(derive_epoch_key(self._master, epoch, self.rotation_seconds))
            self._ciphers[epoch] = cipher
            for old in sorted(self._ciphers)[:-4]:  # keep only a few recent epoch keys in memory
                del self._ciphers[old]
        return cipher

    @staticmethod
    def _aad(channel: str, header: dict) -> bytes:
        return json.dumps({"channel": channel, **header}, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")

    # ------------------------------------------------------------------ sealing
    def seal(self, channel: str, payload: bytes | str) -> bytes:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        rotated = None
        with self._lock:
            self._seq += 1
            ts = float(self._clock())
            epoch = self.epoch_at(ts)
            if self._last_seal_epoch is not None and epoch != self._last_seal_epoch:
                rotated = (self._last_seal_epoch, epoch)
            self._last_seal_epoch = epoch
            header = {"v": ENVELOPE_VERSION, "alg": ALGORITHM, "kid": epoch,
                      "sid": self.sender_id, "sess": self._session, "seq": self._seq, "ts": ts}
            iv = os.urandom(IV_BYTES)
            ct = self._cipher(epoch).encrypt(iv, payload, self._aad(channel, header))
            self.sealed += 1
        if rotated and self._on_key_epoch:
            self._on_key_epoch(*rotated)
        return json.dumps({**header, "iv": b64encode(iv), "ct": b64encode(ct)},
                          separators=(",", ":")).encode("ascii")

    def seal_json(self, channel: str, value: object) -> bytes:
        return self.seal(channel, json.dumps(value, separators=(",", ":"), allow_nan=False))

    # ------------------------------------------------------------------ opening
    def open(self, channel: str, data: bytes | str) -> bytes:
        try:
            plaintext = self._open(channel, data)
        except SecurityError as exc:
            with self._lock:
                self.rejected[exc.reason] += 1
            raise
        with self._lock:
            self.opened += 1
        return plaintext

    def open_json(self, channel: str, data: bytes | str) -> object:
        plaintext = self.open(channel, data)
        try:
            return json.loads(plaintext)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecurityError("MALFORMED", "authenticated payload is not JSON") from exc

    def _open(self, channel: str, data: bytes | str) -> bytes:
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_ENVELOPE_BYTES:
            raise SecurityError("MALFORMED", "envelope missing or too large")
        try:
            env = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecurityError("UNENCRYPTED", "packet is not a sealed envelope") from exc
        if not isinstance(env, dict) or "ct" not in env:
            raise SecurityError("UNENCRYPTED", "packet is not a sealed envelope")
        header = {k: env.get(k) for k in _HEADER_FIELDS}
        if header["v"] != ENVELOPE_VERSION or header["alg"] != ALGORITHM:
            raise SecurityError("MALFORMED", "unsupported envelope version/algorithm")
        for field in ("kid", "seq"):
            if isinstance(header[field], bool) or not isinstance(header[field], int):
                raise SecurityError("MALFORMED", f"{field} must be an integer")
        for field in ("sid", "sess"):
            if not isinstance(header[field], str) or not 0 < len(header[field]) <= 64:
                raise SecurityError("MALFORMED", f"{field} invalid")
        ts = header["ts"]
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
            raise SecurityError("MALFORMED", "ts must be a finite number")
        if header["seq"] < 1:
            raise SecurityError("MALFORMED", "seq must be positive")
        now = float(self._clock())
        if abs(now - ts) > self.freshness_seconds:
            raise SecurityError("STALE", f"packet age {now - ts:.1f}s exceeds {self.freshness_seconds:g}s")
        if header["kid"] != self.epoch_at(ts) or abs(header["kid"] - self.epoch_at(now)) > 1:
            raise SecurityError("KEY_EPOCH", "key epoch does not match packet time")
        iv = b64decode(env.get("iv"), "iv")
        ct = b64decode(env.get("ct"), "ct")
        if len(iv) != IV_BYTES or len(ct) < 16:
            raise SecurityError("MALFORMED", "bad IV or ciphertext length")
        try:
            plaintext = self._cipher(header["kid"]).decrypt(iv, ct, self._aad(channel, header))
        except InvalidTag as exc:
            raise SecurityError("AUTH_FAILED", "GCM tag mismatch (tampered, wrong key or wrong topic)") from exc
        with self._lock:
            key = (header["sid"], header["sess"])
            window, _ = self._sessions.get(key, (None, 0.0))
            if window is None:
                window = _ReplayWindow()
            if not window.accept(header["seq"]):
                raise SecurityError("REPLAY", f"duplicate packet {header['sid']}#{header['seq']}")
            self._sessions[key] = (window, now)
            if len(self._sessions) > 64:
                horizon = now - 2 * self.freshness_seconds
                self._sessions = {k: v for k, v in self._sessions.items() if v[1] >= horizon}
        return plaintext

    def stats(self) -> dict:
        with self._lock:
            return {"sealed": self.sealed, "opened": self.opened,
                    "rejected": dict(self.rejected), "key_epoch": self.current_epoch,
                    "rotation_seconds": self.rotation_seconds}

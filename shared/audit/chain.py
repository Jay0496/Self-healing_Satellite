"""Append-only, SHA-256 hash-linked audit log (REQ-AUD-01).

Each entry stores the hash of the previous entry, and its own hash covers every audited
field plus that previous hash::

    entry_hash = SHA-256( canonical JSON of {chain_id, seq, entry_uid, recorded_at,
                                              event_type, actor, details, prev_hash} )

Changing, inserting, re-ordering or deleting any historical entry breaks every later
link, so ``verify_entries`` pinpoints the first altered entry. Delivery metadata
(``acknowledged_at``) is deliberately outside the hash so the store-and-forward queue can
mark entries delivered without touching audited content.

``AuditLog`` is the writer used on the Pi (flight agent + AI process share one chain in the
flight SQLite file) and on the ground for operator actions. Appends use ``BEGIN IMMEDIATE``
so several processes can append to the same chain without forking it. SQLite triggers also
reject UPDATE/DELETE of audited columns, so accidental modification fails loudly; the hash
chain is what detects deliberate tampering that bypasses those triggers.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

GENESIS_HASH = "0" * 64
HASHED_FIELDS = ("chain_id", "seq", "entry_uid", "recorded_at", "event_type", "actor",
                 "details", "prev_hash")
ENTRY_FIELDS = HASHED_FIELDS + ("entry_hash",)
MAX_DETAILS_BYTES = 8_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def canonical_details(details: dict | None) -> str:
    text = json.dumps(details or {}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False, default=str)
    if len(text.encode("utf-8")) > MAX_DETAILS_BYTES:
        raise ValueError("audit details too large")
    return text


def entry_hash(entry: dict) -> str:
    material = json.dumps({k: entry[k] for k in HASHED_FIELDS}, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def validate_entry_shape(entry: object) -> dict:
    """Type-check an entry received over the link before it is hashed or stored."""
    if not isinstance(entry, dict):
        raise ValueError("audit entry must be an object")
    limits = {"chain_id": 80, "entry_uid": 64, "recorded_at": 64, "event_type": 64,
              "actor": 64, "details": MAX_DETAILS_BYTES, "prev_hash": 64, "entry_hash": 64}
    for field, limit in limits.items():
        value = entry.get(field)
        if not isinstance(value, str) or not value or len(value) > limit:
            raise ValueError(f"invalid audit field: {field}")
    seq = entry.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise ValueError("invalid audit field: seq")
    return {k: entry[k] for k in ENTRY_FIELDS}


def verify_entries(entries: list[dict], *, prev_hash: str = GENESIS_HASH,
                   start_seq: int = 1) -> dict:
    """Verify a contiguous run of entries. Returns {"ok", "checked", "first_bad_seq", "reason", "head"}."""
    expected_seq, expected_prev = start_seq, prev_hash
    for count, entry in enumerate(entries):
        reason = None
        if entry["seq"] != expected_seq:
            reason = f"missing or reordered entry (expected seq {expected_seq}, found {entry['seq']})"
        elif entry["prev_hash"] != expected_prev:
            reason = "prev_hash does not link to previous entry"
        elif entry_hash(entry) != entry["entry_hash"]:
            reason = "entry content does not match its hash (modified)"
        if reason:
            return {"ok": False, "checked": count, "first_bad_seq": entry["seq"],
                    "reason": reason, "head": expected_prev}
        expected_seq, expected_prev = entry["seq"] + 1, entry["entry_hash"]
    return {"ok": True, "checked": len(entries), "first_bad_seq": None, "reason": None,
            "head": expected_prev}


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS audit_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS audit_log (
        seq INTEGER PRIMARY KEY, entry_uid TEXT NOT NULL UNIQUE, chain_id TEXT NOT NULL,
        recorded_at TEXT NOT NULL, event_type TEXT NOT NULL, actor TEXT NOT NULL,
        details TEXT NOT NULL, prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL,
        acknowledged_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS audit_pending_idx ON audit_log (acknowledged_at, seq)",
    "CREATE INDEX IF NOT EXISTS audit_type_idx ON audit_log (event_type, seq)",
    """CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE OF
       seq, entry_uid, chain_id, recorded_at, event_type, actor, details, prev_hash, entry_hash
       ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
)


class AuditLog:
    """Writer/reader for one hash chain stored in a SQLite file."""

    def __init__(self, path: str | Path, *, chain_prefix: str = "pi01", actor: str = "flight-agent"):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.actor = actor
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15,
                                   isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA busy_timeout=15000")
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for statement in _SCHEMA:
                    self._db.execute(statement)
                self._db.execute("INSERT OR IGNORE INTO audit_meta(key, value) VALUES ('chain_id', ?)",
                                 (f"{chain_prefix}-{uuid4().hex[:12]}",))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        self.chain_id = self._db.execute(
            "SELECT value FROM audit_meta WHERE key='chain_id'").fetchone()[0]

    # ------------------------------------------------------------------ writing
    def append(self, event_type: str, details: dict | None = None, *, actor: str | None = None) -> dict:
        if not event_type or len(event_type) > 64:
            raise ValueError("event_type must be 1-64 characters")
        entry = {"chain_id": self.chain_id, "entry_uid": uuid4().hex, "recorded_at": utc_now(),
                 "event_type": event_type, "actor": (actor or self.actor)[:64],
                 "details": canonical_details(details)}
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")   # serialises appends across processes
            try:
                last = self._db.execute(
                    "SELECT seq, entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
                entry["seq"] = (last[0] + 1) if last else 1
                entry["prev_hash"] = last[1] if last else GENESIS_HASH
                entry["entry_hash"] = entry_hash(entry)
                self._db.execute(
                    f"INSERT INTO audit_log ({', '.join(ENTRY_FIELDS)}) VALUES "
                    f"({', '.join('?' for _ in ENTRY_FIELDS)})", [entry[k] for k in ENTRY_FIELDS])
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return entry

    # ------------------------------------------------------- store-and-forward
    def pending(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {', '.join(ENTRY_FIELDS)} FROM audit_log WHERE acknowledged_at IS NULL "
                "ORDER BY seq LIMIT ?", (limit,)).fetchall()
        return [dict(zip(ENTRY_FIELDS, row)) for row in rows]

    def acknowledge(self, entry_uids: list[str]) -> None:
        if not entry_uids:
            return
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                stamp = utc_now()
                self._db.executemany(
                    "UPDATE audit_log SET acknowledged_at=? WHERE entry_uid=? AND acknowledged_at IS NULL",
                    [(stamp, uid) for uid in entry_uids])
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def counts(self) -> tuple[int, int]:
        with self._lock:
            total, pending = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(acknowledged_at IS NULL), 0) FROM audit_log").fetchone()
        return int(total), int(pending)

    # ------------------------------------------------------------------ reading
    def entries(self, after_seq: int = 0, limit: int | None = None) -> list[dict]:
        sql = f"SELECT {', '.join(ENTRY_FIELDS)} FROM audit_log WHERE seq > ? ORDER BY seq"
        params: tuple = (after_seq,)
        if limit:
            sql += " LIMIT ?"
            params += (limit,)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [dict(zip(ENTRY_FIELDS, row)) for row in rows]

    def verify(self) -> dict:
        result = verify_entries(self.entries())
        result["chain_id"] = self.chain_id
        return result

    def close(self) -> None:
        with self._lock:
            self._db.close()


class RateLimitedAudit:
    """Audits noisy events (e.g. SECURITY_REJECT under attack) without letting an attacker
    flood the chain: at most ``per_minute`` entries per event type per minute; the rest are
    counted and summarised in one ``<TYPE>_SUPPRESSED`` entry when the minute rolls over."""

    def __init__(self, audit: AuditLog, per_minute: int = 20, clock=None):

        self.audit = audit
        self.per_minute = per_minute
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._window: dict[str, tuple[float, int, int]] = {}  # type -> (start, written, suppressed)

    def append(self, event_type: str, details: dict | None = None) -> bool:
        now = self._clock()
        summary = None
        with self._lock:
            start, written, suppressed = self._window.get(event_type, (now, 0, 0))
            if now - start >= 60:
                if suppressed:
                    summary = suppressed
                start, written, suppressed = now, 0, 0
            write = written < self.per_minute
            self._window[event_type] = (start, written + write, suppressed + (not write))
        if summary:
            self.audit.append(f"{event_type}_SUPPRESSED"[:64], {"suppressed": summary, "window_s": 60})
        if write:
            self.audit.append(event_type, details)
        return write

    def flush(self) -> None:
        with self._lock:
            pending = {k: v[2] for k, v in self._window.items() if v[2]}
            self._window.clear()
        for event_type, count in pending.items():
            self.audit.append(f"{event_type}_SUPPRESSED"[:64], {"suppressed": count, "window_s": 60})

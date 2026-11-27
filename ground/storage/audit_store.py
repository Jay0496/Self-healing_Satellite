"""Ground copy of the flight audit chain, re-verified link by link as it arrives (REQ-AUD-01).

The Pi downlinks its hash-chained audit entries through the same store-and-forward path as
telemetry (buffered in SQLite during blackout, sent oldest-first, ACKed by ground). Ground
recomputes every hash and checks every ``prev_hash`` link, so an entry altered on the Pi,
in transit, or in this database is flagged instead of silently accepted.

Integrity status per entry:
    VERIFIED       hash correct and linked to the stored previous entry (or genesis)
    PENDING_LINK   hash correct, previous entry not received yet (resolved when it arrives)
    HASH_MISMATCH  content does not match its own hash (entry modified)
    BROKEN_LINK    prev_hash does not match the previous entry's hash (history rewritten)
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
from pathlib import Path

from shared.audit.chain import (ENTRY_FIELDS, GENESIS_HASH, entry_hash, utc_now,
                                validate_entry_shape, verify_entries)

_COLUMNS = ENTRY_FIELDS + ("integrity", "received_at")


class GroundAuditStore:
    def __init__(self, filename: str | Path):
        filename = Path(filename)
        filename.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(filename), check_same_thread=False, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS flight_audit (
                chain_id TEXT NOT NULL, seq INTEGER NOT NULL, entry_uid TEXT NOT NULL UNIQUE,
                recorded_at TEXT NOT NULL, event_type TEXT NOT NULL, actor TEXT NOT NULL,
                details TEXT NOT NULL, prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL,
                integrity TEXT NOT NULL, received_at TEXT NOT NULL,
                PRIMARY KEY (chain_id, seq))""")
            self.db.execute("CREATE INDEX IF NOT EXISTS flight_audit_type ON flight_audit(event_type)")
            self.db.execute("CREATE INDEX IF NOT EXISTS flight_audit_time ON flight_audit(recorded_at)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS flight_audit_conflicts (
                chain_id TEXT, seq INTEGER, entry_uid TEXT, entry_hash TEXT, received_at TEXT)""")
        self.alerts: list[str] = []

    def _status(self, entry: dict) -> str:
        if entry_hash(entry) != entry["entry_hash"]:
            return "HASH_MISMATCH"
        if entry["seq"] == 1:
            return "VERIFIED" if entry["prev_hash"] == GENESIS_HASH else "BROKEN_LINK"
        prev = self.db.execute("SELECT entry_hash FROM flight_audit WHERE chain_id=? AND seq=?",
                               (entry["chain_id"], entry["seq"] - 1)).fetchone()
        if prev is None:
            return "PENDING_LINK"
        return "VERIFIED" if prev[0] == entry["prev_hash"] else "BROKEN_LINK"

    def ingest(self, entries: list[dict]) -> list[str]:
        """Store a downlinked batch; returns entry_uids to ACK. Raises ValueError if malformed."""
        clean = [validate_entry_shape(e) for e in entries]
        acked = []
        with self.lock, self.db:
            for entry in sorted(clean, key=lambda e: (e["chain_id"], e["seq"])):
                existing = self.db.execute(
                    "SELECT entry_uid, entry_hash FROM flight_audit WHERE chain_id=? AND seq=?",
                    (entry["chain_id"], entry["seq"])).fetchone()
                if existing:
                    if existing != (entry["entry_uid"], entry["entry_hash"]):
                        # Same position, different content: a forked/rewritten chain.
                        self.db.execute("INSERT INTO flight_audit_conflicts VALUES (?,?,?,?,?)",
                                        (entry["chain_id"], entry["seq"], entry["entry_uid"],
                                         entry["entry_hash"], utc_now()))
                        self.alerts.append(f"CONFLICT {entry['chain_id']}#{entry['seq']}")
                    acked.append(entry["entry_uid"])
                    continue
                status = self._status(entry)
                if status in ("HASH_MISMATCH", "BROKEN_LINK"):
                    self.alerts.append(f"{status} {entry['chain_id']}#{entry['seq']} {entry['event_type']}")
                self.db.execute(
                    f"INSERT INTO flight_audit ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                    [entry[k] for k in ENTRY_FIELDS] + [status, utc_now()])
                nxt = self.db.execute(
                    f"SELECT {', '.join(ENTRY_FIELDS)} FROM flight_audit "
                    "WHERE chain_id=? AND seq=? AND integrity='PENDING_LINK'",
                    (entry["chain_id"], entry["seq"] + 1)).fetchone()
                if nxt:  # an out-of-order successor can now be linked
                    successor = dict(zip(ENTRY_FIELDS, nxt))
                    self.db.execute("UPDATE flight_audit SET integrity=? WHERE chain_id=? AND seq=?",
                                    (self._status(successor), successor["chain_id"], successor["seq"]))
                acked.append(entry["entry_uid"])
        return acked

    # ---------------------------------------------------------------- queries
    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM flight_audit").fetchone()[0]

    def chains(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute(
                "SELECT DISTINCT chain_id FROM flight_audit ORDER BY chain_id")]

    def verify_chain(self, chain_id: str) -> dict:
        """Full re-verification from genesis using only stored data."""
        with self.lock:
            rows = self.db.execute(f"SELECT {', '.join(ENTRY_FIELDS)} FROM flight_audit "
                                   "WHERE chain_id=? ORDER BY seq", (chain_id,)).fetchall()
            conflicts = self.db.execute("SELECT COUNT(*) FROM flight_audit_conflicts WHERE chain_id=?",
                                        (chain_id,)).fetchone()[0]
        result = verify_entries([dict(zip(ENTRY_FIELDS, r)) for r in rows])
        result.update(chain_id=chain_id, entries=len(rows), conflicts=conflicts)
        result["ok"] = result["ok"] and conflicts == 0
        return result

    def search(self, *, event_type: str | None = None, text: str | None = None,
               since: str | None = None, until: str | None = None, chain_id: str | None = None,
               limit: int = 200) -> list[dict]:
        clauses, params = [], []
        for column, value in (("event_type", event_type), ("chain_id", chain_id)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if since:
            clauses.append("recorded_at >= ?")
            params.append(since)
        if until:
            clauses.append("recorded_at <= ?")
            params.append(until)
        if text:
            clauses.append("(details LIKE ? OR actor LIKE ?)")
            params += [f"%{text}%", f"%{text}%"]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.lock:
            rows = self.db.execute(f"SELECT {', '.join(_COLUMNS)} FROM flight_audit {where} "
                                   "ORDER BY chain_id, seq LIMIT ?", params + [limit]).fetchall()
        return [dict(zip(_COLUMNS, r)) for r in rows]

    def export(self, rows: list[dict], fmt: str = "json") -> str:
        if fmt == "json":
            return json.dumps(rows, indent=2, ensure_ascii=False)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue()

    def close(self) -> None:
        with self.lock:
            self.db.close()

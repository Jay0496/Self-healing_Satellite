"""Ground receiver: idempotent telemetry and flight-event ingestion.

POST bodies must be AES-256-GCM sealed envelopes from the Pi (REQ-SEC-01); responses are
sealed too. Transport is still plain HTTP underneath, so bind to a trusted network only.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ground.storage.audit_store import GroundAuditStore
from shared.security import KeyMaterialError, SecurityError, open_channel


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class GroundStore:
    def __init__(self, filename: Path):
        filename.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(filename), check_same_thread=False, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS telemetry (
                sample_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, payload TEXT NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS events (
                event_uid TEXT PRIMARY KEY, received_at TEXT NOT NULL,
                event_time TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL)""")

    def ingest(self, records: list[dict]) -> list[str]:
        ids = []
        with self.lock, self.db:
            for rec in records:
                if not isinstance(rec, dict) or not isinstance(rec.get('sample_id'), str) or not rec['sample_id']:
                    raise ValueError('Each record must contain a non-empty sample_id')
                if len(rec['sample_id']) > 250:
                    raise ValueError('sample_id too long')
                from shared.protocol import TelemetryRecord
                TelemetryRecord.from_dict(rec)
                self.db.execute(
                    'INSERT OR IGNORE INTO telemetry (sample_id, received_at, payload) VALUES (?,?,?)',
                    (rec['sample_id'], utc_now(), json.dumps(rec, separators=(',', ':'), sort_keys=True)),
                )
                ids.append(rec['sample_id'])
        return ids

    def ingest_events(self, events: list[dict]) -> list[str]:
        ids = []
        for event in events:
            if not isinstance(event, dict):
                raise ValueError('Each event must be an object')
            for field, max_length in [('event_uid', 64), ('event_time', 64),
                                      ('state', 64), ('message', 1000)]:
                val = event.get(field)
                if not isinstance(val, str) or not val or len(val) > max_length:
                    raise ValueError(f'Invalid event field: {field}')
            ids.append(event['event_uid'])
        with self.lock, self.db:
            for event in events:
                self.db.execute(
                    """INSERT OR IGNORE INTO events
                       (event_uid, received_at, event_time, state, message)
                       VALUES (?, ?, ?, ?, ?)""",
                    (event['event_uid'], utc_now(), event['event_time'],
                     event['state'], event['message']),
                )
        return ids

    def count(self):
        with self.lock:
            return self.db.execute('SELECT COUNT(*) FROM telemetry').fetchone()[0]

    def count_events(self):
        with self.lock:
            return self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description='Ground control telemetry/event receiver')
    parser.add_argument('--host', default='127.0.0.1', help='Bind on a trusted network only')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', type=Path, default=Path('ground/ingestion/received.sqlite3'))
    parser.add_argument('--secrets', default=None, help='Key directory (default $G29_SECRETS_DIR or ~/.g29_secrets)')
    args = parser.parse_args()
    try:
        channel = open_channel('ground-station-http', secrets=args.secrets)
    except KeyMaterialError as exc:
        print(f'[GROUND] SECURITY key material unavailable: {exc}', flush=True)
        return 2
    store = GroundStore(args.db)
    audit_store = GroundAuditStore(args.db)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, data: dict, sealed: bool = False):
            payload = (channel.seal_json(f'http:{self.path}#ack', data) if sealed
                       else json.dumps(data).encode('utf-8'))
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == '/status':
                self.reply(200, {'received_records': store.count(),
                                 'received_events': store.count_events(),
                                 'received_audit_entries': audit_store.count(),
                                 'audit_chains': {c: audit_store.verify_chain(c)['ok']
                                                  for c in audit_store.chains()}})
            else:
                self.reply(404, {'error': 'Unknown endpoint'})

        def do_POST(self):
            if self.path not in ('/ingest', '/ingest-events', '/ingest-audit'):
                self.reply(404, {'error': 'Unknown endpoint'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2_000_000:
                    raise ValueError('Request length invalid')
                try:
                    payload = channel.open_json(f'http:{self.path}', self.rfile.read(length))
                except SecurityError as exc:
                    print(f'[GROUND] SECURITY REJECT path={self.path} reason={exc.reason}', flush=True)
                    self.reply(403, {'error': exc.reason})
                    return
                if not isinstance(payload, dict):
                    raise ValueError('Request must be a JSON object')
                if self.path == '/ingest':
                    records = payload.get('records')
                    if not isinstance(records, list) or not 0 < len(records) <= 100:
                        raise ValueError('Expected 1 to 100 records')
                    ids = store.ingest(records)
                    print(f'[GROUND] Accepted telemetry={len(ids)} total_unique={store.count()}', flush=True)
                    self.reply(200, {'acknowledged_ids': ids}, sealed=True)
                elif self.path == '/ingest-audit':
                    entries = payload.get('entries')
                    if not isinstance(entries, list) or not 0 < len(entries) <= 100:
                        raise ValueError('Expected 1 to 100 audit entries')
                    ids = audit_store.ingest(entries)
                    while audit_store.alerts:
                        print(f'[GROUND] AUDIT INTEGRITY ALERT {audit_store.alerts.pop(0)}', flush=True)
                    print(f'[GROUND] Accepted audit={len(ids)} total_unique={audit_store.count()}', flush=True)
                    self.reply(200, {'acknowledged_entry_uids': ids}, sealed=True)
                else:
                    events = payload.get('events')
                    if not isinstance(events, list) or not 0 < len(events) <= 100:
                        raise ValueError('Expected 1 to 100 events')
                    ids = store.ingest_events(events)
                    print(f'[GROUND] Accepted events={len(ids)} total_unique={store.count_events()}', flush=True)
                    self.reply(200, {'acknowledged_event_uids': ids}, sealed=True)
            except (ValueError, TypeError, KeyError) as exc:
                self.reply(400, {'error': str(exc)})

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'[GROUND] Listening on http://{args.host}:{args.port}; SQLite: {args.db}', flush=True)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print('\n[GROUND] Stopped', flush=True)
    finally:
        server.server_close()
        store.db.close()


if __name__ == '__main__':
    raise SystemExit(main())

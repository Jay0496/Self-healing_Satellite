"""Local ground station receiver with acknowledged, deduplicated telemetry ingestion.

For lab/LAN use only: plain HTTP without authentication or encryption.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


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

    def count(self):
        with self.lock:
            return self.db.execute('SELECT COUNT(*) FROM telemetry').fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description='Ground control telemetry receiver')
    parser.add_argument('--host', default='127.0.0.1', help='0.0.0.0 when the Pi is on another device')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', type=Path, default=Path('ground/ingestion/received.sqlite3'))
    args = parser.parse_args()
    store = GroundStore(args.db)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, data: dict):
            payload = json.dumps(data).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == '/status':
                self.reply(200, {'received_records': store.count()})
            else:
                self.reply(404, {'error': 'Unknown endpoint'})

        def do_POST(self):
            if self.path != '/ingest':
                self.reply(404, {'error': 'Unknown endpoint'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2_000_000:
                    raise ValueError('Request length invalid')
                payload = json.loads(self.rfile.read(length))
                records = payload.get('records') if isinstance(payload, dict) else None
                if not isinstance(records, list) or not 0 < len(records) <= 100:
                    raise ValueError('Expected 1 to 100 records')
                ids = store.ingest(records)
                print(f'[GROUND] Accepted batch={len(ids)} total_unique={store.count()}', flush=True)
                self.reply(200, {'acknowledged_ids': ids})
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
    main()

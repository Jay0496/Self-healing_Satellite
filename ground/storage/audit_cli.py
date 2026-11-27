"""Operator CLI to verify, search and export the audit trail received on the ground.

    python -m ground.storage.audit_cli --db ground/ingestion/<run>.sqlite3 verify
    python -m ground.storage.audit_cli --db ... search --type ANOMALY_DETECTED
    python -m ground.storage.audit_cli --db ... search --text Battery_Voltage --since 2026-11-27
    python -m ground.storage.audit_cli --db ... export --format csv --out audit.csv
    python -m ground.storage.audit_cli --operator-log verify     # ground's own operator chain
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ground.storage.audit_store import GroundAuditStore
from shared.audit import AuditLog

DEFAULT_OPERATOR_LOG = Path("ground/storage/operator_audit.sqlite3")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Verify / search / export audit records")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--db", type=Path, help="Ground SQLite holding downlinked flight audit entries")
    source.add_argument("--operator-log", action="store_true",
                        help="Use the ground operator audit chain (commands sent, security alerts)")
    p.add_argument("--operator-db", type=Path, default=DEFAULT_OPERATOR_LOG,
                   help=f"Operator audit chain file (default {DEFAULT_OPERATOR_LOG})")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("verify")
    for name in ("search", "export"):
        q = sub.add_parser(name)
        q.add_argument("--type", dest="event_type")
        q.add_argument("--text")
        q.add_argument("--since")
        q.add_argument("--until")
        q.add_argument("--chain")
        q.add_argument("--limit", type=int, default=200 if name == "search" else 1_000_000)
        q.add_argument("--format", choices=("json", "csv"), default="json" if name == "export" else "table")
        q.add_argument("--out", type=Path)
    args = p.parse_args(argv)

    if args.operator_log:
        if not args.operator_db.is_file():
            print(f"[AUDIT] No operator log at {args.operator_db}")
            return 2
        log = AuditLog(args.operator_db, chain_prefix="ground", actor="ground")
        try:
            if args.action != "verify":
                print("[AUDIT] Operator log supports 'verify' only; use --db for search/export")
                return 2
            result = log.verify()
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        finally:
            log.close()

    if not args.db.is_file():
        print(f"[AUDIT] No database at {args.db}")
        return 2
    store = GroundAuditStore(args.db)
    try:
        if args.action == "verify":
            ok = True
            chains = store.chains()
            if not chains:
                print("[AUDIT] No flight audit entries in this database")
            for chain in chains:
                result = store.verify_chain(chain)
                ok &= result["ok"]
                status = "INTACT" if result["ok"] else (f"BROKEN at seq {result['first_bad_seq']}: "
                                                         f"{result['reason'] or 'conflicting copies'}")
                print(f"[AUDIT] chain {chain}: {result['entries']} entries, {status}, "
                      f"head={result['head'][:16]}")
            return 0 if ok else 1
        started = time.perf_counter()
        rows = store.search(event_type=args.event_type, text=args.text, since=args.since,
                            until=args.until, chain_id=args.chain, limit=args.limit)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if args.format == "table":
            for r in rows:
                print(f"{r['chain_id']}#{r['seq']:<5} {r['recorded_at']} {r['event_type']:<24} "
                      f"{r['integrity']:<13} {r['details']}")
            print(f"[AUDIT] {len(rows)} match(es) in {elapsed_ms:.1f} ms", file=sys.stderr)
            return 0
        text = store.export(rows, args.format)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
            print(f"[AUDIT] Exported {len(rows)} entries to {args.out} ({elapsed_ms:.1f} ms query)")
        else:
            print(text)
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())

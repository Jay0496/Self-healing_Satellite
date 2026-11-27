"""REQ-AUD-01 verification: append-only SHA-256 hash chain, tamper detection, store-and-forward
through a blackout, ground re-verification, search (<= 2 s) and export.

Run: python -m pytest tests/audit -v
"""
import csv
import io
import json
import multiprocessing
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from ground.storage.audit_store import GroundAuditStore
from shared.audit import GENESIS_HASH, AuditLog, RateLimitedAudit, verify_entries


def _append_many(path: str, actor: str, count: int) -> None:
    log = AuditLog(path, actor=actor)
    for i in range(count):
        log.append('CONCURRENT', {'actor': actor, 'i': i})
    log.close()


def tamper(path: Path, sql: str, params=()):
    """Simulate an attacker with raw file access who bypasses the append-only triggers."""
    raw = sqlite3.connect(path)
    with raw:
        raw.execute('DROP TRIGGER IF EXISTS audit_log_no_update')
        raw.execute('DROP TRIGGER IF EXISTS audit_log_no_delete')
        raw.execute(sql, params)
    raw.close()


class TestFlightChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'flight.sqlite3'
        self.log = AuditLog(self.path)
        for event in ('AGENT_START', 'LINK_STATE', 'ANOMALY_DETECTED', 'COMMAND', 'ANOMALY_CLEARED'):
            self.log.append(event, {'note': event.lower()})

    def tearDown(self):
        self.log.close()
        self.tmp.cleanup()

    def test_chain_links_from_genesis(self):
        entries = self.log.entries()
        self.assertEqual([e['seq'] for e in entries], [1, 2, 3, 4, 5])
        self.assertEqual(entries[0]['prev_hash'], GENESIS_HASH)
        for prev, cur in zip(entries, entries[1:]):
            self.assertEqual(cur['prev_hash'], prev['entry_hash'])
        self.assertTrue(self.log.verify()['ok'])

    def test_append_only_triggers_block_update_and_delete(self):
        for sql in ("UPDATE audit_log SET details='{}' WHERE seq=2", 'DELETE FROM audit_log WHERE seq=2'):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                self.log._db.execute(sql)
        self.assertTrue(self.log.verify()['ok'])

    def test_acknowledging_delivery_does_not_change_chain(self):
        self.log.acknowledge([e['entry_uid'] for e in self.log.pending()])
        self.assertEqual(self.log.counts(), (5, 0))
        self.assertTrue(self.log.verify()['ok'])

    def test_modified_entry_detected(self):
        self.log.close()
        tamper(self.path, "UPDATE audit_log SET details=? WHERE seq=3", ('{"note":"nothing happened"}',))
        self.log = AuditLog(self.path)
        result = self.log.verify()
        self.assertFalse(result['ok'])
        self.assertEqual(result['first_bad_seq'], 3)

    def test_deleted_entry_detected(self):
        self.log.close()
        tamper(self.path, 'DELETE FROM audit_log WHERE seq=2')
        self.log = AuditLog(self.path)
        result = self.log.verify()
        self.assertFalse(result['ok'])
        self.assertEqual(result['first_bad_seq'], 3)

    def test_rewritten_history_with_recomputed_hash_detected(self):
        """Even recomputing the altered entry's own hash breaks the next link."""
        from shared.audit.chain import entry_hash
        entry = self.log.entries()[1]
        entry['details'] = '{"note":"forged"}'
        forged = entry_hash(entry)
        self.log.close()
        tamper(self.path, 'UPDATE audit_log SET details=?, entry_hash=? WHERE seq=2',
               (entry['details'], forged))
        self.log = AuditLog(self.path)
        self.assertEqual(self.log.verify()['first_bad_seq'], 3)

    def test_reopen_continues_same_chain(self):
        chain = self.log.chain_id
        self.log.close()
        self.log = AuditLog(self.path)
        self.log.append('AGENT_START', {})
        self.assertEqual(self.log.chain_id, chain)
        self.assertTrue(self.log.verify()['ok'])

    def test_two_processes_share_one_unforked_chain(self):
        """Collector and AI detector append to the same file concurrently."""
        procs = [multiprocessing.Process(target=_append_many, args=(str(self.path), a, 100))
                 for a in ('flight-agent', 'ai-detector')]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        entries = self.log.entries()
        self.assertEqual(len(entries), 205)
        self.assertEqual([e['seq'] for e in entries], list(range(1, 206)))
        self.assertTrue(self.log.verify()['ok'])


class TestStoreAndForwardToGround(unittest.TestCase):
    def test_blackout_buffered_entries_delivered_and_verified(self):
        with tempfile.TemporaryDirectory() as td:
            flight = AuditLog(Path(td) / 'flight.sqlite3')
            ground = GroundAuditStore(Path(td) / 'ground.sqlite3')
            flight.append('LINK_STATE', {'to': 'ONLINE'})
            ground_ack = ground.ingest(flight.pending())
            flight.acknowledge(ground_ack)
            flight.append('LINK_STATE', {'to': 'BLACKOUT'})
            for i in range(250):                                  # recorded while link is down
                flight.append('ANOMALY_DETECTED' if i % 50 == 0 else 'RESOURCE_SAMPLE', {'i': i})
            flight.append('LINK_STATE', {'to': 'RECOVERING'})
            self.assertEqual(flight.counts(), (253, 252))
            while True:                                           # oldest-first 100-entry batches
                batch = flight.pending(100)
                if not batch:
                    break
                flight.acknowledge(ground.ingest(batch))
            self.assertEqual(flight.counts()[1], 0)
            result = ground.verify_chain(flight.chain_id)
            self.assertTrue(result['ok'])
            self.assertEqual(result['entries'], 253)
            self.assertEqual(result['head'], flight.verify()['head'])   # identical chain heads
            self.assertEqual(ground.ingest(flight.entries()[:5]), [e['entry_uid'] for e in flight.entries()[:5]])
            self.assertTrue(ground.verify_chain(flight.chain_id)['ok'])  # re-sent duplicates harmless
            flight.close()
            ground.close()

    def test_ground_flags_tampered_and_forked_entries(self):
        with tempfile.TemporaryDirectory() as td:
            flight = AuditLog(Path(td) / 'flight.sqlite3')
            for i in range(4):
                flight.append('E', {'i': i})
            entries = flight.entries()
            ground = GroundAuditStore(Path(td) / 'ground.sqlite3')
            modified = dict(entries[1], details='{"i":99}')                     # altered in transit
            ground.ingest([entries[0], modified])
            forked = dict(entries[2], prev_hash='f' * 64)
            from shared.audit.chain import entry_hash
            forked['entry_hash'] = entry_hash(forked)                          # self-consistent fake
            ground.ingest([forked])
            rows = {r['seq']: r['integrity'] for r in ground.search(chain_id=flight.chain_id)}
            self.assertEqual(rows, {1: 'VERIFIED', 2: 'HASH_MISMATCH', 3: 'BROKEN_LINK'})
            self.assertFalse(ground.verify_chain(flight.chain_id)['ok'])
            self.assertEqual(len(ground.alerts), 2)
            flight.close()
            ground.close()

    def test_out_of_order_arrival_resolves(self):
        with tempfile.TemporaryDirectory() as td:
            flight = AuditLog(Path(td) / 'flight.sqlite3')
            for i in range(3):
                flight.append('E', {'i': i})
            e = flight.entries()
            ground = GroundAuditStore(Path(td) / 'ground.sqlite3')
            ground.ingest([e[0]])
            ground.ingest([e[2]])
            self.assertEqual(ground.search(chain_id=flight.chain_id)[-1]['integrity'], 'PENDING_LINK')
            ground.ingest([e[1]])
            self.assertEqual({r['integrity'] for r in ground.search(chain_id=flight.chain_id)}, {'VERIFIED'})
            flight.close()
            ground.close()


class TestSearchExportAndRateLimit(unittest.TestCase):
    def test_search_10k_entries_under_2s_and_export(self):
        with tempfile.TemporaryDirectory() as td:
            flight = AuditLog(Path(td) / 'flight.sqlite3')
            ground = GroundAuditStore(Path(td) / 'ground.sqlite3')
            batch = []
            for i in range(10_000):
                kind = 'ANOMALY_DETECTED' if i % 500 == 0 else 'RESOURCE_SAMPLE'
                batch.append(flight.append(kind, {'i': i, 'sensor': 'Battery_Voltage' if kind != 'RESOURCE_SAMPLE' else None}))
                if len(batch) == 100:
                    ground.ingest(batch)
                    batch = []
            started = time.perf_counter()
            hits = ground.search(event_type='ANOMALY_DETECTED', text='Battery_Voltage')
            elapsed = time.perf_counter() - started
            print(f'\n[BENCH] search over 10,000 audit entries: {elapsed * 1000:.1f} ms (target <= 2000 ms)')
            self.assertEqual(len(hits), 20)
            self.assertLess(elapsed, 2.0)
            exported_json = json.loads(ground.export(hits, 'json'))
            exported_csv = list(csv.DictReader(io.StringIO(ground.export(hits, 'csv'))))
            self.assertEqual(len(exported_json), 20)
            self.assertEqual(exported_csv[0]['entry_hash'], hits[0]['entry_hash'])
            # Exported entries remain independently verifiable.
            self.assertTrue(verify_entries(ground.search(chain_id=flight.chain_id, limit=20_000))['ok'])
            flight.close()
            ground.close()

    def test_rate_limited_security_events_are_summarised(self):
        with tempfile.TemporaryDirectory() as td:
            log = AuditLog(Path(td) / 'a.sqlite3')
            now = [0.0]
            limited = RateLimitedAudit(log, per_minute=5, clock=lambda: now[0])
            written = sum(limited.append('SECURITY_REJECT', {'n': i}) for i in range(100))
            self.assertEqual(written, 5)
            now[0] = 61
            limited.append('SECURITY_REJECT', {'n': 'after'})
            types = [e['event_type'] for e in log.entries()]
            self.assertEqual(types.count('SECURITY_REJECT_SUPPRESSED'), 1)
            self.assertIn('"suppressed":95', log.entries()[5]['details'])
            self.assertTrue(log.verify()['ok'])
            log.close()


if __name__ == '__main__':
    unittest.main()

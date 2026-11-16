"""Verifies durable event queue, legacy DB migration, and ground deduplication."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flight.storage.buffer import TelemetryBuffer
from ground.ingestion.receiver import GroundStore


class TestEventSync(unittest.TestCase):
    def test_event_store_ack_and_reopen(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'flight.sqlite3'
            db = TelemetryBuffer(path)
            db.event('ONLINE', 'Ground link available')
            db.event('BLACKOUT', 'Simulated ground link unavailable')
            self.assertEqual(db.event_counts(), (2, 2))
            first_batch = db.pending_events()
            self.assertEqual(len({x['event_uid'] for x in first_batch}), 2)
            db.acknowledge_events([first_batch[0]['event_uid']])
            db.close()
            db = TelemetryBuffer(path)
            self.assertEqual(db.event_counts(), (2, 1))
            self.assertEqual(db.pending_events()[0]['state'], 'BLACKOUT')
            db.close()

    def test_migrates_previous_events_table_without_data_loss(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'previous.sqlite3'
            with sqlite3.connect(path) as raw:
                raw.execute('''CREATE TABLE events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_time TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL)''')
                raw.execute('INSERT INTO events(event_time,state,message) VALUES(?,?,?)',
                            ('2026-11-16T18:00:00+00:00', 'BLACKOUT', 'Legacy event'))
            db = TelemetryBuffer(path)
            self.assertEqual(db.event_counts(), (1, 1))
            self.assertEqual(db.pending_events()[0]['message'], 'Legacy event')
            db.close()

    def test_ground_deduplicates_event_ids(self):
        with tempfile.TemporaryDirectory() as td:
            store = GroundStore(Path(td) / 'ground.sqlite3')
            event = {'event_uid': 'abc123', 'event_time': '2026-11-16T18:00:00+00:00',
                     'state': 'RECOVERING', 'message': 'Link restored'}
            self.assertEqual(store.ingest_events([event]), ['abc123'])
            self.assertEqual(store.ingest_events([event]), ['abc123'])
            self.assertEqual(store.count_events(), 1)
            store.db.close()


if __name__ == '__main__':
    unittest.main()

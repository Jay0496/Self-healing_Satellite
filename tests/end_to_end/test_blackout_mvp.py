"""Fast, network-free validation of the blackout schedule and durable queue."""
import tempfile
import unittest
from pathlib import Path

from flight.storage.buffer import TelemetryBuffer
from simulator.link.blackout import BlackoutWindow
from shared.protocol import TelemetryRecord


class TestBlackoutMvp(unittest.TestCase):
    def test_window_boundaries(self):
        w = BlackoutWindow(20, 60)
        self.assertFalse(w.offline(19.9))
        self.assertTrue(w.offline(20.0))
        self.assertTrue(w.offline(79.9))
        self.assertFalse(w.offline(80))
        self.assertTrue(w.finished(80))

    def test_buffer_deduplicates_and_acknowledges(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'queue.sqlite3'
            record = TelemetryRecord(
                run_id='test', sample_id='test:0', sequence=0,
                sensor_id='bmp0', signal='temperature_c', value=25.0, unit='°C',
                measurement_time='2024-05-01T00:00:00Z',
                publish_time='2024-05-01T00:00:00Z',
                source='generator', transform_id='test', provenance='generated_mock')
            q = TelemetryBuffer(path)
            self.assertTrue(q.store(record))
            self.assertFalse(q.store(record))
            self.assertEqual(q.counts(), (1, 1))
            q.close()
            q = TelemetryBuffer(path)
            self.assertEqual(q.pending()[0]['sample_id'], 'test:0')
            q.acknowledge(['test:0'])
            self.assertEqual(q.counts(), (1, 0))
            q.close()


if __name__ == '__main__':
    unittest.main()

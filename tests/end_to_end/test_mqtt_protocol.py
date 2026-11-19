"""Network-free checks for safe uplink command contract."""
import time
import unittest

from shared.mqtt_protocol import ALLOWED_COMMANDS, topics, validate_command


class TestMqttContract(unittest.TestCase):
    def test_topics_are_bidirectional(self):
        tm = topics('capstone/g29/pi01')
        self.assertIn('downlink', tm['telemetry'])
        self.assertIn('uplink', tm['command'])

    def test_valid_readonly_command(self):
        cmd = {'command_id': 'x', 'type': 'GET_STATUS', 'expires_at': time.time() + 15}
        self.assertEqual(validate_command(cmd)['type'], 'GET_STATUS')

    def test_expired_command_rejected(self):
        with self.assertRaises(ValueError):
            validate_command({'command_id':'x','type':'GET_STATUS','expires_at': time.time()-10})

    def test_unknown_command_rejected(self):
        with self.assertRaises(ValueError):
            validate_command({'command_id':'x','type':'RUN_SHELL','expires_at': time.time()+15})


if __name__ == '__main__':
    unittest.main()

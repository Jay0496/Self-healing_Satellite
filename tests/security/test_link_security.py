"""REQ-SEC-01 / REQ-SEC-02 verification for the AES-256-GCM space-ground envelope.

Run: python -m pytest tests/security -v
"""
import base64
import json
import time
import unittest

from shared.security.channel import SecureChannel, SecurityError, derive_epoch_key

MASTER = bytes(range(32))
OTHER_MASTER = bytes(32)
TOPIC = 'capstone/g29/pi01/downlink/telemetry'


class FakeClock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def pair(clock=None, rotation=86_400, **kwargs):
    clock = clock or FakeClock()
    pi = SecureChannel(MASTER, 'pi01-flight', clock=clock, rotation_seconds=rotation, **kwargs)
    ground = SecureChannel(MASTER, 'ground-station', clock=clock, rotation_seconds=rotation)
    return pi, ground, clock


def edit(packet: bytes, **changes) -> bytes:
    env = json.loads(packet)
    env.update(changes)
    return json.dumps(env).encode()


class TestEncryption(unittest.TestCase):
    def test_round_trip_and_confidentiality(self):
        pi, ground, _ = pair()
        secret = {'items': [{'sample_id': 'run:1', 'value': 7.89}]}
        packet = pi.seal_json(TOPIC, secret)
        self.assertNotIn(b'sample_id', packet)          # payload is not readable on the wire
        self.assertEqual(ground.open_json(TOPIC, packet), secret)
        env = json.loads(packet)
        self.assertEqual(env['alg'], 'AES-256-GCM')
        self.assertEqual(len(base64.b64decode(env['iv'])), 12)

    def test_unique_iv_per_packet(self):
        pi, _, _ = pair()
        ivs = {json.loads(pi.seal(TOPIC, b'same payload'))['iv'] for _ in range(2000)}
        self.assertEqual(len(ivs), 2000)

    def test_tampered_ciphertext_rejected(self):
        pi, ground, _ = pair()
        packet = pi.seal(TOPIC, b'{"ok":true}')
        for index in range(len(base64.b64decode(json.loads(packet)['ct']))):  # every byte incl. tag
            raw = bytearray(base64.b64decode(json.loads(packet)['ct']))
            raw[index] ^= 0x01
            with self.assertRaises(SecurityError) as ctx:
                ground.open(TOPIC, edit(packet, ct=base64.b64encode(bytes(raw)).decode()))
            self.assertEqual(ctx.exception.reason, 'AUTH_FAILED')
        self.assertEqual(ground.open(TOPIC, packet), b'{"ok":true}')

    def test_every_header_field_is_authenticated(self):
        pi, ground, clock = pair()
        for field, value in [('seq', 999), ('sid', 'attacker'), ('sess', 'deadbeef'),
                             ('ts', clock.now - 1)]:
            with self.subTest(field=field):
                with self.assertRaises(SecurityError) as ctx:
                    ground.open(TOPIC, edit(pi.seal(TOPIC, b'{}'), **{field: value}))
                self.assertEqual(ctx.exception.reason, 'AUTH_FAILED')

    def test_packet_cannot_be_moved_to_another_topic(self):
        pi, ground, _ = pair()
        ack = pi.seal_json('capstone/g29/pi01/uplink/ack', {'kind': 'telemetry', 'ids': []})
        with self.assertRaises(SecurityError) as ctx:
            ground.open('capstone/g29/pi01/uplink/command', ack)
        self.assertEqual(ctx.exception.reason, 'AUTH_FAILED')

    def test_wrong_key_rejected(self):
        clock = FakeClock()
        outsider = SecureChannel(OTHER_MASTER, 'pi01-flight', clock=clock)
        _, ground, _ = pair(clock)
        with self.assertRaises(SecurityError) as ctx:
            ground.open(TOPIC, outsider.seal(TOPIC, b'{}'))
        self.assertEqual(ctx.exception.reason, 'AUTH_FAILED')

    def test_plaintext_and_garbage_rejected(self):
        _, ground, _ = pair()
        for packet, reason in [(b'{"items":[]}', 'UNENCRYPTED'), (b'not json', 'UNENCRYPTED'),
                               (b'{"v":1,"alg":"AES-128-CBC","ct":"AA=="}', 'MALFORMED')]:
            with self.subTest(packet=packet):
                with self.assertRaises(SecurityError) as ctx:
                    ground.open(TOPIC, packet)
                self.assertEqual(ctx.exception.reason, reason)
        self.assertEqual(sum(ground.rejected.values()), 3)


class TestReplay(unittest.TestCase):
    def test_duplicate_rejected_out_of_order_accepted(self):
        pi, ground, _ = pair()
        first, second = pi.seal(TOPIC, b'1'), pi.seal(TOPIC, b'2')
        self.assertEqual(ground.open(TOPIC, second), b'2')
        self.assertEqual(ground.open(TOPIC, first), b'1')   # reordered by broker: still fine
        for packet in (first, second):
            with self.assertRaises(SecurityError) as ctx:
                ground.open(TOPIC, packet)
            self.assertEqual(ctx.exception.reason, 'REPLAY')

    def test_old_packet_replayed_later_is_stale(self):
        pi, ground, clock = pair()
        captured = pi.seal(TOPIC, b'{}')
        clock.now += 301
        with self.assertRaises(SecurityError) as ctx:
            ground.open(TOPIC, captured)
        self.assertEqual(ctx.exception.reason, 'STALE')

    def test_forged_packet_does_not_poison_replay_window(self):
        pi, ground, _ = pair()
        good = pi.seal(TOPIC, b'{}')
        with self.assertRaises(SecurityError):
            ground.open(TOPIC, edit(good, ct=json.loads(pi.seal(TOPIC, b'zz'))['ct']))
        self.assertEqual(ground.open(TOPIC, good), b'{}')

    def test_new_process_session_is_accepted(self):
        clock = FakeClock()
        _, ground, _ = pair(clock)
        for _ in range(2):  # Pi restarts: new random session, sequence restarts at 1
            restarted = SecureChannel(MASTER, 'pi01-flight', clock=clock)
            self.assertEqual(ground.open(TOPIC, restarted.seal(TOPIC, b'ok')), b'ok')


class TestKeyRotation(unittest.TestCase):
    def test_epoch_keys_differ_and_rotate(self):
        rotations = []
        clock = FakeClock(now=1000 * 60 - 1)
        pi, ground, _ = pair(clock, rotation=60, on_key_epoch=lambda a, b: rotations.append((a, b)))
        before = pi.seal(TOPIC, b'before')
        clock.now += 2                                     # crosses an epoch boundary
        after = pi.seal(TOPIC, b'after')
        self.assertEqual(json.loads(after)['kid'], json.loads(before)['kid'] + 1)
        self.assertEqual(rotations, [(999, 1000)])
        # A packet sealed just before rotation still opens just after (previous epoch grace).
        self.assertEqual(ground.open(TOPIC, before), b'before')
        self.assertEqual(ground.open(TOPIC, after), b'after')
        self.assertNotEqual(derive_epoch_key(MASTER, 999, 60), derive_epoch_key(MASTER, 1000, 60))

    def test_default_rotation_is_24h(self):
        self.assertEqual(SecureChannel(MASTER, 'x').rotation_seconds, 86_400)

    def test_kid_must_match_timestamp(self):
        pi, ground, _ = pair(rotation=60)
        with self.assertRaises(SecurityError) as ctx:
            ground.open(TOPIC, edit(pi.seal(TOPIC, b'{}'), kid=0))
        self.assertEqual(ctx.exception.reason, 'KEY_EPOCH')

    def test_blackout_buffered_data_sent_after_rotation(self):
        """Store-and-forward seals at send time, so a 120-min blackout spanning rotation is fine."""
        clock = FakeClock(now=86_400 * 20_000 - 600)
        pi, ground, _ = pair(clock)
        buffered_rows = [{'sample_id': f'run:{i}'} for i in range(100)]   # plaintext SQLite rows
        sealed_during_contact = pi.seal_json(TOPIC, {'items': buffered_rows[:1]})
        clock.now += 120 * 60                                              # blackout crosses midnight
        packet = pi.seal_json(TOPIC, {'items': buffered_rows})
        self.assertEqual(ground.open_json(TOPIC, packet)['items'], buffered_rows)
        with self.assertRaises(SecurityError):                             # old capture is useless
            ground.open(TOPIC, sealed_during_contact)


class TestPerformanceAndHygiene(unittest.TestCase):
    def test_1kb_frame_under_5ms(self):
        pi = SecureChannel(MASTER, 'pi01-flight')
        ground = SecureChannel(MASTER, 'ground-station')
        frame = json.dumps({'items': [{'v': 'x' * 900}]}).encode()[:1024]
        n = 500
        started = time.perf_counter()
        for _ in range(n):
            ground.open(TOPIC, pi.seal(TOPIC, frame))
        per_frame_ms = (time.perf_counter() - started) * 1000 / n
        print(f'\n[BENCH] seal+open 1 KB frame: {per_frame_ms:.3f} ms (target <= 5 ms)')
        self.assertLess(per_frame_ms, 5.0)

    def test_key_never_in_repr_or_packet(self):
        pi = SecureChannel(MASTER, 'pi01-flight')
        text = repr(pi) + str(pi.stats()) + pi.seal(TOPIC, b'{}').decode()
        self.assertNotIn(MASTER.hex(), text)
        self.assertNotIn(base64.b64encode(MASTER).decode(), text)


if __name__ == '__main__':
    unittest.main()

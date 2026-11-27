"""Tests for simulator-to-hardware fault integration.

All hardware and MQTT operations are mocked.
No physical sensors are required.
"""

import json
import os
import random
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from simulator.cli import main
from simulator.hardware_faults import (
    HARDWARE_FAULTS,
    select_hardware_fault,
    validate_hardware_fault,
    corresponding_hardware_fault,
)
from simulator.mqtt import MqttTelemetryPublisher
from flight.fault_injection.injector import FAULTS
from flight.fault_injection.mqtt_listener import HardwareFaultReceiver


TEST_KEY = "test-only-key-not-for-real-use-123456"


class TestHardwareFaultIntegration(unittest.TestCase):

    def setUp(self):
        self.client = MagicMock()
        self.client.publish.return_value.rc = 0
        self.client.publish.return_value.is_published.return_value = True

        self.publisher = MqttTelemetryPublisher(self.client)

        self.injector = MagicMock(
            return_value={"status": "INJECTED"}
        )

        self.receiver = HardwareFaultReceiver(
            TEST_KEY, injector=self.injector
        )

        environment = patch.dict(
            os.environ, {"CAPSTONE_FAULT_KEY": TEST_KEY}
        )

        environment.start()
        self.addCleanup(environment.stop)

    def payload(self, sensor="mpu6050", fault="y_disabled"):
        self.publisher.publish_hardware_fault(sensor, fault)
        return self.client.publish.call_args.kwargs["payload"]

    def test_fault_definitions_match(self):
        self.assertEqual(set(HARDWARE_FAULTS), set(FAULTS))

        for sensor, faults in HARDWARE_FAULTS.items():
            self.assertEqual(set(faults), set(FAULTS[sensor]))

    def test_all_twelve_fault_messages(self):
        total = 0

        for sensor, faults in HARDWARE_FAULTS.items():
            for fault in faults:
                with self.subTest(sensor=sensor, fault=fault):
                    payload = self.payload(sensor, fault)

                    result = self.receiver.handle(
                        payload.encode()
                    )

                    self.injector.assert_called_with(sensor, fault)

                    self.assertEqual(result["status"], "INJECTED")

                    total += 1

        self.assertEqual(total, 12)

    def test_random_selection(self):
        rng = random.Random(42)

        for _ in range(100):
            selected = select_hardware_fault(rng)

            self.assertTrue(validate_hardware_fault(
                selected["sensor"], selected["fault"]
            ))

    def test_invalid_fault_rejected(self):
        with self.assertRaises(ValueError):
            self.publisher.publish_hardware_fault(
                "unknown", "sleep"
            )

        self.client.publish.assert_not_called()

    def test_duplicate_request_ignored(self):
        payload = self.payload().encode()

        self.receiver.handle(payload)
        self.receiver.handle(payload)

        self.injector.assert_called_once()

    def test_invalid_signature_rejected(self):
        message = json.loads(self.payload())
        message["signature"] = "invalid"

        with self.assertRaises(ValueError):
            self.receiver.handle(
                json.dumps(message).encode()
            )

        self.injector.assert_not_called()

    def test_retained_request_rejected(self):
        with self.assertRaises(ValueError):
            self.receiver.handle(
                self.payload().encode(),
                retained=True,
            )

        self.injector.assert_not_called()

    def test_expired_request_rejected(self):
        payload = self.payload().encode()
        expiry = json.loads(payload)["request"]["expires_at"]

        with patch(
            "flight.fault_injection.mqtt_listener.time.time",
            return_value=expiry + 1,
        ):
            with self.assertRaises(ValueError):
                self.receiver.handle(payload)

        self.injector.assert_not_called()

    def test_supported_mappings(self):
        mappings = {
            "temperature_c": ("bmp280", "sleep"),
            "pressure_hpa": ("bmp280", "sleep"),
            "Battery_Temp": ("bmp280", "sleep"),
            "NanoCom_Temp": ("bmp280", "sleep"),
            "Battery_Voltage": ("ina219", "power_down"),
        }

        for signal, expected in mappings.items():
            with self.subTest(signal=signal):
                result = corresponding_hardware_fault(
                    signal, "stuck"
                )

                self.assertEqual(
                    (result["sensor"], result["fault"]),
                    expected,
                )

    def test_unsupported_mappings(self):
        for kind in ("spike", "noise", "drift", "voltage_sag"):
            self.assertIsNone(
                corresponding_hardware_fault(
                    "Battery_Voltage", kind
                )
            )

        self.assertIsNone(
            corresponding_hardware_fault(
                "Background_RSSI", "stuck"
            )
        )

    def run_simulator(self, frames, extra_args):
        publisher = MagicMock()
        generator = MagicMock()
        generator.frames.return_value = frames

        with tempfile.TemporaryDirectory() as directory:
            csv = Path(directory) / "test.csv"
            csv.touch()

            with (
                patch(
                    "simulator.cli.TelemetryGenerator",
                    return_value=generator,
                ),
                patch(
                    "simulator.cli.create_paho_publisher",
                    return_value=publisher,
                ),
            ):
                result = main([
                    "--uhf-csv", str(csv),
                    "--frames", str(len(frames)),
                    "--rate-hz", "1000",
                    *extra_args,
                ])

        self.assertEqual(result, 0)
        publisher.close.assert_called_once()

        return publisher

    def test_normal_mode(self):
        frames = [
            SimpleNamespace(tick=i, records=())
            for i in range(5)
        ]

        publisher = self.run_simulator(frames, [])

        publisher.publish_hardware_fault.assert_not_called()

    def test_automatic_mode(self):
        frames = [
            SimpleNamespace(tick=i, records=())
            for i in range(5)
        ]

        publisher = self.run_simulator(frames, [
            "--hardware-faults",
            "--hardware-fault-tick", "2",
        ])

        publisher.publish_hardware_fault.assert_called_once()

        sensor, fault = (
            publisher.publish_hardware_fault.call_args.args
        )

        self.assertTrue(validate_hardware_fault(sensor, fault))

    def test_matched_mode(self):

        def record(signal, kind):
            return SimpleNamespace(
                signal=signal,
                provenance="fault_injected",
                transform_id=f"nominal|fault_{kind}_v1",
            )

        records = [
            record("Battery_Temp", "stuck"),
            record("Battery_Temp", "stuck"),
            record("Battery_Voltage", "voltage_sag"),
            record("Battery_Voltage", "stuck"),
            record("NanoCom_Temp", "stuck"),
        ]

        frames = [
            SimpleNamespace(tick=i, records=[item])
            for i, item in enumerate(records)
        ]

        publisher = self.run_simulator(
            frames, ["--match-hardware-faults"]
        )

        self.assertEqual(
            publisher.publish_hardware_fault.call_args_list,
            [
                call("bmp280", "sleep"),
                call("ina219", "power_down"),
            ],
        )

        self.assertEqual(publisher.publish.call_count, 5)


if __name__ == "__main__":
    unittest.main()

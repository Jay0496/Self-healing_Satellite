"""Unit tests for physical sensor fault injection."""

import tempfile
import unittest

from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

from flight.fault_injection import fault_state
from flight.fault_injection import injector


class TestSensorFaultInjection(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        state_patch = patch.object(
            fault_state,
            "STATE_DIR",
            Path(self.temp_dir.name),
        )

        state_patch.start()
        self.addCleanup(state_patch.stop)

    def test_all_fault_definitions(self):
        cases = [
            ("ina219", "power_down", 0x00, 0x219F, 0x2198),
            ("ina219", "adc_disabled", 0x00, 0x219F, 0x219C),
            ("ina219", "calibration", 0x05, 0x8000, 0x2000),
            ("ina219", "zero_calibration", 0x05, 0x8000, 0x0000),

            ("mpu6050", "sleep", 0x6B, 0x00, 0x40),
            ("mpu6050", "accel_disabled", 0x6C, 0x00, 0x38),
            ("mpu6050", "x_disabled", 0x6C, 0x00, 0x20),
            ("mpu6050", "y_disabled", 0x6C, 0x00, 0x10),
            ("mpu6050", "z_disabled", 0x6C, 0x00, 0x08),

            ("bmp280", "sleep", 0xF4, 0xB7, 0xB4),
            ("bmp280", "temp_disabled", 0xF4, 0xB7, 0x17),
            ("bmp280", "pressure_disabled", 0xF4, 0xB7, 0xA3),
        ]

        for sensor, fault, register, original, expected in cases:
            with self.subTest(sensor=sensor, fault=fault):

                bus = MagicMock()

                with (
                    patch.object(
                        injector, "open_bus",
                        return_value=nullcontext(bus)
                    ),
                    patch.object(
                        injector, "read_register",
                        return_value=original
                    ),
                    patch.object(
                        injector, "write_register"
                    ) as write,
                    patch.object(
                        injector, "verify_register",
                        return_value=expected
                    ) as verify,
                ):

                    result = injector.inject_fault(sensor, fault)

                    self.assertEqual(result["modified"], expected)

                    write.assert_called_once_with(
                        bus, sensor, register, expected
                    )

                    verify.assert_called_once_with(
                        bus, sensor, register, expected
                    )

                    state = fault_state.load_state(sensor)

                    self.assertEqual(state["fault"], fault)

                    self.assertEqual(
                        state["original_registers"][str(register)],
                        original,
                    )

                fault_state.clear_state(sensor)

    def test_duplicate_fault_rejected(self):

        fault_state.save_state(
            "mpu6050", "sleep", {0x6B: 0x00}
        )

        with patch.object(injector, "open_bus") as bus:

            with self.assertRaises(RuntimeError):
                injector.inject_fault("mpu6050", "x_disabled")

            bus.assert_not_called()

        state = fault_state.load_state("mpu6050")
        self.assertEqual(state["fault"], "sleep")

    def test_invalid_sensor_rejected(self):

        with self.assertRaises(ValueError):
            injector.inject_fault("unknown", "sleep")

    def test_invalid_fault_rejected(self):

        with self.assertRaises(ValueError):
            injector.inject_fault("ina219", "unknown")

    def test_no_configuration_change(self):

        with (
            patch.object(
                injector, "open_bus",
                return_value=nullcontext(MagicMock())
            ),
            patch.object(
                injector, "read_register",
                return_value=0x40
            ),
            patch.object(
                injector, "write_register"
            ) as write,
        ):

            with self.assertRaises(RuntimeError):
                injector.inject_fault("mpu6050", "sleep")

            write.assert_not_called()

        self.assertIsNone(fault_state.load_state("mpu6050"))

    def test_failed_injection_rolls_back(self):

        bus = MagicMock()

        with (
            patch.object(
                injector, "open_bus",
                return_value=nullcontext(bus)
            ),
            patch.object(
                injector, "read_register",
                return_value=0x00
            ),
            patch.object(
                injector, "write_register"
            ) as write,
            patch.object(
                injector, "verify_register",
                side_effect=[
                    RuntimeError("Verification failed"),
                    0x00,
                ]
            ),
        ):

            with self.assertRaises(RuntimeError):
                injector.inject_fault("mpu6050", "sleep")

            self.assertEqual(write.call_count, 2)

            write.assert_called_with(
                bus, "mpu6050", 0x6B, 0x00
            )

        self.assertIsNone(fault_state.load_state("mpu6050"))

    def test_failed_rollback_preserves_backup(self):

        with (
            patch.object(
                injector, "open_bus",
                return_value=nullcontext(MagicMock())
            ),
            patch.object(
                injector, "read_register",
                return_value=0x00
            ),
            patch.object(
                injector, "write_register"
            ),
            patch.object(
                injector, "verify_register",
                side_effect=RuntimeError("Verification failed")
            ),
        ):

            with self.assertRaises(RuntimeError):
                injector.inject_fault("mpu6050", "sleep")

        state = fault_state.load_state("mpu6050")

        self.assertIsNotNone(state)
        self.assertEqual(
            state["original_registers"]["107"], 0x00
        )


if __name__ == "__main__":
    unittest.main()

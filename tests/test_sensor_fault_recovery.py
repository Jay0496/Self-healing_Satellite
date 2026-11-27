"""Unit tests for software-only physical sensor recovery."""

import tempfile
import unittest

from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

from flight.fault_injection import fault_state
from flight.fault_injection import injector
from flight.recovery import recovery_manager


class TestSensorFaultRecovery(unittest.TestCase):

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

    def test_all_fault_recoveries(self):

        cases = [
            ("ina219", "power_down", 0x00, 0x219F),
            ("ina219", "adc_disabled", 0x00, 0x219F),
            ("ina219", "calibration", 0x05, 0x8000),
            ("ina219", "zero_calibration", 0x05, 0x8000),

            ("mpu6050", "sleep", 0x6B, 0x00),
            ("mpu6050", "accel_disabled", 0x6C, 0x00),
            ("mpu6050", "x_disabled", 0x6C, 0x00),
            ("mpu6050", "y_disabled", 0x6C, 0x00),
            ("mpu6050", "z_disabled", 0x6C, 0x00),

            ("bmp280", "sleep", 0xF4, 0xB7),
            ("bmp280", "temp_disabled", 0xF4, 0xB7),
            ("bmp280", "pressure_disabled", 0xF4, 0xB7),
        ]

        for sensor, fault, register, original in cases:

            with self.subTest(sensor=sensor, fault=fault):

                fault_state.save_state(
                    sensor, fault, {register: original}
                )

                bus = MagicMock()

                with (
                    patch.object(
                        recovery_manager, "open_bus",
                        return_value=nullcontext(bus)
                    ),
                    patch.object(
                        recovery_manager, "write_register"
                    ) as write,
                    patch.object(
                        recovery_manager, "verify_register",
                        return_value=original
                    ) as verify,
                ):

                    result = recovery_manager.recover_fault(
                        sensor, fault
                    )

                    self.assertEqual(
                        result["status"], "RECOVERED"
                    )

                    write.assert_called_once_with(
                        bus, sensor, register, original
                    )

                    verify.assert_called_once_with(
                        bus, sensor, register, original
                    )

                self.assertIsNone(
                    fault_state.load_state(sensor)
                )

    def test_no_active_fault(self):

        with patch.object(
            recovery_manager, "open_bus"
        ) as bus:

            result = recovery_manager.recover_fault("ina219")

            self.assertEqual(
                result["status"], "NO_ACTIVE_FAULT"
            )

            bus.assert_not_called()

    def test_wrong_recovery_rejected(self):

        fault_state.save_state(
            "mpu6050", "sleep", {0x6B: 0x00}
        )

        with patch.object(
            recovery_manager, "open_bus"
        ) as bus:

            with self.assertRaises(RuntimeError):
                recovery_manager.recover_fault(
                    "mpu6050", "x_disabled"
                )

            bus.assert_not_called()

        self.assertIsNotNone(
            fault_state.load_state("mpu6050")
        )

    def test_invalid_sensor(self):

        with self.assertRaises(ValueError):
            recovery_manager.recover_fault("unknown")

    def test_invalid_fault(self):

        with self.assertRaises(ValueError):
            recovery_manager.recover_fault(
                "ina219", "unknown"
            )

    def test_failed_recovery_preserves_backup(self):

        fault_state.save_state(
            "bmp280", "sleep", {0xF4: 0xB7}
        )

        with (
            patch.object(
                recovery_manager, "open_bus",
                return_value=nullcontext(MagicMock())
            ),
            patch.object(
                recovery_manager, "write_register"
            ),
            patch.object(
                recovery_manager, "verify_register",
                side_effect=RuntimeError("Verification failed")
            ),
        ):

            with self.assertRaises(RuntimeError):
                recovery_manager.recover_fault(
                    "bmp280", "sleep"
                )

        self.assertIsNotNone(
            fault_state.load_state("bmp280")
        )

    def test_invalid_backup_register(self):

        fault_state.save_state(
            "mpu6050", "sleep", {0x00: 0x00}
        )

        with patch.object(
            recovery_manager, "open_bus"
        ) as bus:

            with self.assertRaises(RuntimeError):
                recovery_manager.recover_fault("mpu6050")

            bus.assert_not_called()

    def test_invalid_backup_value(self):

        fault_state.save_state(
            "mpu6050", "sleep", {0x6B: 0x100}
        )

        with patch.object(
            recovery_manager, "open_bus"
        ) as bus:

            with self.assertRaises(RuntimeError):
                recovery_manager.recover_fault("mpu6050")

            bus.assert_not_called()

    def test_injection_recovery_round_trip(self):

        for sensor, faults in injector.FAULTS.items():

            for fault, (register, transform) in faults.items():

                with self.subTest(sensor=sensor, fault=fault):

                    original = {
                        "ina219": (
                            0x8000 if register == 0x05
                            else 0x219F
                        ),
                        "mpu6050": 0x00,
                        "bmp280": 0xB7,
                    }[sensor]

                    registers = {register: original}
                    bus = MagicMock()

                    def read(bus, sensor, register):
                        return registers[register]

                    def write(bus, sensor, register, value):
                        registers[register] = value

                    def verify(bus, sensor, register, expected):
                        if registers[register] != expected:
                            raise RuntimeError("Mismatch")
                        return expected

                    with (
                        patch.object(
                            injector, "open_bus",
                            return_value=nullcontext(bus)
                        ),
                        patch.object(
                            injector, "read_register",
                            side_effect=read
                        ),
                        patch.object(
                            injector, "write_register",
                            side_effect=write
                        ),
                        patch.object(
                            injector, "verify_register",
                            side_effect=verify
                        ),
                        patch.object(
                            recovery_manager, "open_bus",
                            return_value=nullcontext(bus)
                        ),
                        patch.object(
                            recovery_manager, "write_register",
                            side_effect=write
                        ),
                        patch.object(
                            recovery_manager, "verify_register",
                            side_effect=verify
                        ),
                    ):

                        injector.inject_fault(sensor, fault)

                        self.assertEqual(
                            registers[register],
                            transform(original)
                        )

                        self.assertIsNotNone(
                            fault_state.load_state(sensor)
                        )

                        recovery_manager.recover_fault(
                            sensor, fault
                        )

                        self.assertEqual(
                            registers[register], original
                        )

                        self.assertIsNone(
                            fault_state.load_state(sensor)
                        )


if __name__ == "__main__":
    unittest.main()

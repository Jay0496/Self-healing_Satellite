"""Software-triggered faults on physical I2C sensors."""

from flight.fault_injection.hardware import (
    open_bus,
    read_register,
    write_register,
    verify_register,
)

from flight.fault_injection.fault_state import (
    state_lock,
    load_state,
    save_state,
    clear_state,
)


# Supported hardware fault definitions.
# Each entry contains a register and a reversible transformation.

FAULTS = {
    "ina219": {
        "power_down": (
            0x00,
            lambda value: value & 0xFFF8,
        ),
        "adc_disabled": (
            0x00,
            lambda value: (value & 0xFFF8) | 0x0004,
        ),
        "calibration": (
            0x05,
            lambda value: (value // 4) & 0xFFFE,
        ),
        "zero_calibration": (
            0x05,
            lambda value: 0x0000,
        ),
    },

    "mpu6050": {
        "sleep": (
            0x6B,
            lambda value: value | 0x40,
        ),
        "accel_disabled": (
            0x6C,
            lambda value: value | 0x38,
        ),
        "x_disabled": (
            0x6C,
            lambda value: value | 0x20,
        ),
        "y_disabled": (
            0x6C,
            lambda value: value | 0x10,
        ),
        "z_disabled": (
            0x6C,
            lambda value: value | 0x08,
        ),
    },

    "bmp280": {
        "sleep": (
            0xF4,
            lambda value: value & 0xFC,
        ),
        "temp_disabled": (
            0xF4,
            lambda value: value & 0x1F,
        ),
        "pressure_disabled": (
            0xF4,
            lambda value: value & 0xE3,
        ),
    },
}


def inject_fault(sensor, fault):
    """Inject a reversible fault directly into a physical sensor."""

    if sensor not in FAULTS:
        raise ValueError(f"Unsupported sensor: {sensor}")

    if fault not in FAULTS[sensor]:
        raise ValueError(
            f"Unsupported fault '{fault}' for {sensor}"
        )

    register, transform = FAULTS[sensor][fault]

    with state_lock():

        if load_state(sensor) is not None:
            raise RuntimeError(
                f"{sensor} already has an active fault. "
                "Recover it before injecting another fault."
            )

        with open_bus() as bus:

            original = read_register(
                bus, sensor, register
            )

            modified = transform(original)

            if modified == original:
                raise RuntimeError(
                    "Requested fault would not change the "
                    "current sensor configuration."
                )

            # Preserve the original configuration before writing.
            save_state(
                sensor,
                fault,
                {register: original},
            )

            try:
                write_register(
                    bus,
                    sensor,
                    register,
                    modified,
                )

                verify_register(
                    bus,
                    sensor,
                    register,
                    modified,
                )

            except Exception:
                # Attempt immediate rollback if injection fails.
                # Retain the saved state if rollback also fails.
                try:
                    write_register(
                        bus,
                        sensor,
                        register,
                        original,
                    )

                    verify_register(
                        bus,
                        sensor,
                        register,
                        original,
                    )

                    clear_state(sensor)

                except Exception as rollback_error:
                    raise RuntimeError(
                        "Injection and rollback failed. "
                        "Original configuration remains saved."
                    ) from rollback_error

                raise

            result = {
                "sensor": sensor,
                "fault": fault,
                "register": register,
                "original": original,
                "modified": modified,
                "status": "INJECTED",
            }

    print(f"[FAULT INJECTED] {sensor.upper()}")
    print(f"Fault: {fault}")
    print(f"Register: 0x{register:02X}")
    print(f"Original value: 0x{original:X}")
    print(f"Modified value: 0x{modified:X}")
    print("Original configuration saved for recovery.")

    return result

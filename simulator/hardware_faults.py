"""Supported physical sensor faults for hardware testing."""

import random

HARDWARE_FAULTS = {
    "ina219": (
        "power_down",
        "adc_disabled",
        "calibration",
        "zero_calibration",
    ),
    "mpu6050": (
        "sleep",
        "accel_disabled",
        "x_disabled",
        "y_disabled",
        "z_disabled",
    ),
    "bmp280": (
        "sleep",
        "temp_disabled",
        "pressure_disabled",
    ),
}


def select_hardware_fault(rng=None):
    """Select one supported physical sensor fault."""

    rng = rng if rng is not None else random.Random()

    candidates = [
        (sensor, fault)
        for sensor, faults in HARDWARE_FAULTS.items()
        for fault in faults
    ]

    sensor, fault = rng.choice(candidates)

    return {
        "sensor": sensor,
        "fault": fault,
    }


def validate_hardware_fault(sensor, fault):
    """Check whether a physical fault is supported."""

    return (
        isinstance(sensor, str)
        and isinstance(fault, str)
        and sensor in HARDWARE_FAULTS
        and fault in HARDWARE_FAULTS[sensor]
    )


def corresponding_hardware_fault(signal, fault_type):
    """Return a representative physical fault when supported."""

    mapping = {
        ("temperature_c", "stuck"): ("bmp280", "sleep"),
        ("pressure_hpa", "stuck"): ("bmp280", "sleep"),
        ("Battery_Temp", "stuck"): ("bmp280", "sleep"),
        ("NanoCom_Temp", "stuck"): ("bmp280", "sleep"),
        ("Battery_Voltage", "stuck"): ("ina219", "power_down"),
    }

    selected = mapping.get((signal, fault_type))

    if selected is None:
        return None

    sensor, fault = selected

    return {
        "sensor": sensor,
        "fault": fault,
    }

"""Software-only recovery of physical sensor faults."""

from flight.fault_injection.hardware import (
    open_bus,
    write_register,
    verify_register,
)

from flight.fault_injection.fault_state import (
    state_lock,
    load_state,
    clear_state,
    list_active_faults,
)

from flight.fault_injection.injector import FAULTS


def recover_fault(sensor, fault=None):
    """Restore the original physical sensor configuration."""

    if sensor not in FAULTS:
        raise ValueError(f"Unsupported sensor: {sensor}")

    if fault is not None and fault not in FAULTS[sensor]:
        raise ValueError(f"Unsupported fault: {fault}")

    with state_lock():

        state = load_state(sensor)

        if state is None:
            print(f"[NO ACTIVE FAULT] {sensor.upper()}")

            return {
                "sensor": sensor,
                "status": "NO_ACTIVE_FAULT",
            }

        active_fault = state["fault"]

        if state["sensor"] != sensor:
            raise RuntimeError("Invalid sensor backup")

        if active_fault not in FAULTS[sensor]:
            raise RuntimeError("Invalid saved fault")

        if fault is not None and fault != active_fault:
            raise RuntimeError(
                f"Requested recovery: {fault}. "
                f"Actual active fault: {active_fault}"
            )

        expected_register, _ = FAULTS[sensor][active_fault]

        registers = {
            int(register): value
            for register, value
            in state["original_registers"].items()
        }

        if set(registers) != {expected_register}:
            raise RuntimeError(
                "Backup contains unexpected registers"
            )

        max_value = 0xFFFF if sensor == "ina219" else 0xFF

        for value in registers.values():
            if type(value) is not int or not 0 <= value <= max_value:
                raise RuntimeError("Invalid saved register value")

        # Restore the original hardware configuration.
        with open_bus() as bus:

            for register, original in registers.items():

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

                print(
                    f"[RESTORED] Register 0x{register:02X}"
                    f" = 0x{original:X}"
                )

        # Remove backup only after successful verification.
        clear_state(sensor)

        result = {
            "sensor": sensor,
            "fault": active_fault,
            "restored_registers": registers,
            "status": "RECOVERED",
        }

    print(f"[FAULT RECOVERED] {sensor.upper()}")
    print(f"Fault: {active_fault}")
    print("Original configuration restored successfully.")

    return result


def recover_all():
    """Attempt recovery of every recorded sensor fault."""

    results = []

    with state_lock():
        states = list_active_faults()

    for state in states:

        sensor = state["sensor"]

        try:
            result = recover_fault(sensor, state["fault"])
            results.append(result)

        except Exception as error:

            results.append({
                "sensor": sensor,
                "status": "RECOVERY_FAILED",
                "error": str(error),
            })

    return results

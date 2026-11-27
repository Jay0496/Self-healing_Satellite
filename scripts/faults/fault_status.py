"""Display recorded active physical sensor faults."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from flight.fault_injection.fault_state import (
    state_lock,
    list_active_faults,
)


def main():

    with state_lock():
        faults = list_active_faults()

    if not faults:
        print("[STATUS] No recorded active sensor faults.")
        return

    print(f"\nRecorded active faults: {len(faults)}")

    for state in faults:

        print("-" * 50)
        print(f"Sensor: {state['sensor'].upper()}")
        print(f"Fault: {state['fault']}")
        print(f"Injected at: {state['created_at']}")
        print("Original register values:")

        for register, value in state["original_registers"].items():
            print(f"  0x{int(register):02X}: 0x{value:X}")


if __name__ == "__main__":
    main()

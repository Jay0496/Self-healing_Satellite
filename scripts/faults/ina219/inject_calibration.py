"""Inject calibration fault on the physical ina219 sensor."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from flight.fault_injection.injector import inject_fault


if __name__ == "__main__":
    try:
        inject_fault("ina219", "calibration")
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        sys.exit(1)

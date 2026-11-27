"""Recover sleep fault on the physical mpu6050 sensor."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from flight.recovery.recovery_manager import recover_fault


if __name__ == "__main__":
    try:
        recover_fault("mpu6050", "sleep")
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        sys.exit(1)

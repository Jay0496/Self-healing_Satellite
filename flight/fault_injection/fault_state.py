"""Persistent storage of original sensor register configurations."""

import json
import os
import tempfile

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import fcntl


STATE_DIR = Path.home() / ".capstone" / "sensor_faults"


@contextmanager
def state_lock():
    """Prevent simultaneous fault operations."""
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

    with (STATE_DIR / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def state_path(sensor):
    """Return the persistent state file for a sensor."""
    if sensor not in {"ina219", "mpu6050", "bmp280", "i2c"}:
        raise ValueError(f"Unknown sensor: {sensor}")

    return STATE_DIR / f"{sensor}.json"


def load_state(sensor):
    """Load an existing fault configuration."""
    path = state_path(sensor)

    if not path.exists():
        return None

    with path.open("r") as file:
        return json.load(file)


def save_state(sensor, fault, registers):
    """Save original register values before fault injection."""
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

    path = state_path(sensor)

    if path.exists():
        raise RuntimeError(
            f"{sensor} already has an active fault. "
            "Recover it before injecting another fault."
        )

    state = {
        "sensor": sensor,
        "fault": fault,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_registers": {
            str(register): value
            for register, value in registers.items()
        },
    }

    fd, temporary = tempfile.mkstemp(
        prefix=f"{sensor}_",
        suffix=".tmp",
        dir=STATE_DIR,
    )

    try:
        with os.fdopen(fd, "w") as file:
            json.dump(state, file, indent=4)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary, path)

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    return state


def clear_state(sensor):
    """Remove fault state after successful recovery."""
    path = state_path(sensor)

    if path.exists():
        path.unlink()


def list_active_faults():
    """Return all saved fault states."""
    if not STATE_DIR.exists():
        return []

    states = []

    for path in sorted(STATE_DIR.glob("*.json")):
        with path.open("r") as file:
            states.append(json.load(file))

    return states

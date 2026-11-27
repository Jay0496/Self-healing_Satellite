"""Small MQTT topic/command contract for the capstone IP-link demonstration."""
from __future__ import annotations

import time
from datetime import datetime, timezone

DEFAULT_PREFIX = 'capstone/g29/pi01'
ALLOWED_COMMANDS = frozenset({'GET_STATUS', 'REQUEST_LOGS'})


def topics(prefix: str = DEFAULT_PREFIX) -> dict[str, str]:
    if not prefix or '#' in prefix or '+' in prefix or prefix.startswith('/'):
        raise ValueError('Invalid MQTT topic prefix')
    prefix = prefix.rstrip('/')
    return {
        'telemetry': f'{prefix}/downlink/telemetry',
        'events': f'{prefix}/downlink/events',
        'audit': f'{prefix}/downlink/audit',
        'ack': f'{prefix}/uplink/ack',
        'command': f'{prefix}/uplink/command',
        'result': f'{prefix}/downlink/command-result',
    }


def validate_command(value: object, now: float | None = None) -> dict:
    """Accept only short-lived, named read-only commands, never arbitrary shell commands."""
    if not isinstance(value, dict):
        raise ValueError('Command must be an object')
    cid, command, expires = value.get('command_id'), value.get('type'), value.get('expires_at')
    if not isinstance(cid, str) or not (1 <= len(cid) <= 80):
        raise ValueError('Invalid command_id')
    if command not in ALLOWED_COMMANDS:
        raise ValueError('Unsupported command')
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        raise ValueError('Missing numeric expiry')
    current = time.time() if now is None else now
    if expires <= current or expires > current + 60:
        raise ValueError('Command expired or expiry exceeds 60 seconds')
    return {'command_id': cid, 'type': command, 'expires_at': float(expires)}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

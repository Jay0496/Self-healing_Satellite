"""Space-ground link security shared by flight and ground endpoints.

See docs/SECURITY.md for the threat model, wire format, and key provisioning steps.
"""
from __future__ import annotations

from .channel import SecureChannel, SecurityError
from .keystore import KeyMaterialError, load_link_master_key, rotation_seconds

__all__ = ["SecureChannel", "SecurityError", "KeyMaterialError", "open_channel"]


def open_channel(sender_id: str, *, secrets: str | None = None, on_key_epoch=None) -> SecureChannel:
    """Build an endpoint's channel from the provisioned master key and rotation policy."""
    return SecureChannel(load_link_master_key(secrets), sender_id,
                         rotation_seconds=rotation_seconds(), on_key_epoch=on_key_epoch)

"""Shared wire contracts for flight, ground, and simulator components."""

from .telemetry import PROTOCOL_VERSION, TelemetryRecord, TelemetryValidationError

__all__ = ["PROTOCOL_VERSION", "TelemetryRecord", "TelemetryValidationError"]

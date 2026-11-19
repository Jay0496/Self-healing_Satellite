"""Satellite-like telemetry generation and streaming tools."""

from .generation import GeneratorConfig, TelemetryFrame, TelemetryGenerator
from .pipeline import IdentityTransform, select_records, transform_frame

__all__ = [
    "GeneratorConfig",
    "IdentityTransform",
    "TelemetryFrame",
    "TelemetryGenerator",
    "select_records",
    "transform_frame",
]

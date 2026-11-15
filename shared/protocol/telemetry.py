"""Versioned, JSON-serializable telemetry record shared with the simulator."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

PROTOCOL_VERSION = 1
ALLOWED_PROVENANCE = {"real_replay", "derived", "generated_mock", "fault_injected"}
ALLOWED_QUALITY = {"observed", "held", "interpolated", "missing", "stale"}


class TelemetryValidationError(ValueError):
    """Raised when a telemetry record does not satisfy the shared contract."""


def _validate_timestamp(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise TelemetryValidationError(f"{name} must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TelemetryValidationError(f"{name} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise TelemetryValidationError(f"{name} must include a timezone")


@dataclass(frozen=True, slots=True)
class TelemetryRecord:
    """One sensor signal sample, with source and simulation provenance."""

    run_id: str
    sample_id: str
    sequence: int
    sensor_id: str
    signal: str
    value: float
    unit: str
    measurement_time: str
    publish_time: str
    source: str
    transform_id: str
    provenance: str
    quality: str = "observed"
    schema_version: int = PROTOCOL_VERSION

    def validate(self) -> None:
        for field_name in (
            "run_id",
            "sample_id",
            "sensor_id",
            "signal",
            "unit",
            "source",
            "transform_id",
        ):
            if not getattr(self, field_name):
                raise TelemetryValidationError(f"{field_name} must not be empty")
        if self.schema_version != PROTOCOL_VERSION:
            raise TelemetryValidationError(
                f"unsupported schema_version: {self.schema_version}"
            )
        if not isinstance(self.sequence, int) or self.sequence < 0:
            raise TelemetryValidationError("sequence must be a non-negative integer")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise TelemetryValidationError("value must be numeric")
        if not math.isfinite(float(self.value)):
            raise TelemetryValidationError("value must be finite")
        if self.provenance not in ALLOWED_PROVENANCE:
            raise TelemetryValidationError(f"unsupported provenance: {self.provenance}")
        if self.quality not in ALLOWED_QUALITY:
            raise TelemetryValidationError(f"unsupported quality: {self.quality}")
        _validate_timestamp("measurement_time", self.measurement_time)
        _validate_timestamp("publish_time", self.publish_time)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TelemetryRecord":
        if not isinstance(value, dict):
            raise TelemetryValidationError("telemetry payload must be a JSON object")
        try:
            record = cls(**value)
        except (TypeError, ValueError) as exc:
            raise TelemetryValidationError(f"invalid telemetry payload: {exc}") from exc
        record.validate()
        return record

    @classmethod
    def from_json(cls, value: str | bytes) -> "TelemetryRecord":
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise TelemetryValidationError("telemetry payload is not valid JSON") from exc
        return cls.from_dict(decoded)

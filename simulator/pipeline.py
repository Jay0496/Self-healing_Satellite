"""Pre-publish transformation boundary for future telemetry manipulation."""

from __future__ import annotations

from typing import Protocol

from shared.protocol import TelemetryRecord

from .generation import TelemetryFrame

AVAILABLE_SIGNALS = frozenset(
    {
        "temperature_c",
        "pressure_hpa",
        "bus_voltage_v",
        "current_ma",
        "power_mw",
        "gyro_x_dps",
        "gyro_y_dps",
        "gyro_z_dps",
        "accel_x_g",
        "accel_y_g",
        "accel_z_g",
        "roll_deg",
        "pitch_deg",
        "yaw_deg",
    }
)


class TelemetryTransform(Protocol):
    """Transform one nominal record before the simulator publishes it."""

    def apply(self, record: TelemetryRecord) -> TelemetryRecord:
        """Return a transformed record while preserving its sample identity."""


class IdentityTransform:
    """Default no-op transform; active fault behavior is intentionally absent."""

    def apply(self, record: TelemetryRecord) -> TelemetryRecord:
        return record


_IDENTITY_FIELDS = (
    "run_id",
    "sample_id",
    "sequence",
    "sensor_id",
    "signal",
    "unit",
    "measurement_time",
    "publish_time",
)


def validate_frame(frame: TelemetryFrame) -> None:
    """Validate frame identity, time alignment, records, and derived power."""
    if frame.tick < 0 or not frame.records:
        raise ValueError("frame must have a non-negative tick and at least one record")
    seen_ids: set[str] = set()
    previous_sequence = -1
    for record in frame.records:
        record.validate()
        if record.measurement_time != frame.measurement_time:
            raise ValueError("all records in a frame must share its measurement_time")
        if record.sample_id in seen_ids:
            raise ValueError(f"duplicate sample_id in frame: {record.sample_id}")
        if record.sequence <= previous_sequence:
            raise ValueError("record sequences must increase within a frame")
        seen_ids.add(record.sample_id)
        previous_sequence = record.sequence

    by_signal = {record.signal: record for record in frame.records}
    required = {"bus_voltage_v", "current_ma", "power_mw"}
    if required.issubset(by_signal):
        expected = by_signal["bus_voltage_v"].value * by_signal["current_ma"].value
        actual = by_signal["power_mw"].value
        if abs(expected - actual) > 0.002:
            raise ValueError("power_mw must equal bus_voltage_v multiplied by current_ma")


def transform_frame(
    frame: TelemetryFrame,
    transform: TelemetryTransform | None = None,
) -> TelemetryFrame:
    """Apply the configured transform to every record, defaulting to pass-through."""
    validate_frame(frame)
    processor = transform or IdentityTransform()
    transformed: list[TelemetryRecord] = []
    for original in frame.records:
        result = processor.apply(original)
        if not isinstance(result, TelemetryRecord):
            raise TypeError("telemetry transform must return a TelemetryRecord")
        result.validate()
        for field in _IDENTITY_FIELDS:
            if getattr(result, field) != getattr(original, field):
                raise ValueError(f"telemetry transform must preserve {field}")
        transformed.append(result)
    result_frame = TelemetryFrame(
        tick=frame.tick,
        measurement_time=frame.measurement_time,
        records=tuple(transformed),
    )
    validate_frame(result_frame)
    return result_frame


def select_records(
    frame: TelemetryFrame,
    signals: set[str] | frozenset[str] | None = None,
) -> tuple[TelemetryRecord, ...]:
    """Select output signals after the full frame passes through the transform."""
    if signals is None:
        return frame.records
    unknown = set(signals) - AVAILABLE_SIGNALS
    if unknown:
        raise ValueError(f"unknown simulator signals: {', '.join(sorted(unknown))}")
    if not signals:
        raise ValueError("at least one signal must be selected")
    selected = tuple(record for record in frame.records if record.signal in signals)
    if not selected:
        raise ValueError("selected signals are not present in this frame")
    return selected

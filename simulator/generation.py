
"""Deterministic synthetic sensor telemetry shaped by OPS-SAT UHF samples."""

from __future__ import annotations

import bisect
import csv
import hashlib
import math
import os
import random
import statistics

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from shared.protocol import TelemetryRecord
from simulator.fault_injector import ContinuousFaultInjector


UHF_CHANNELS = (
    "NanoMind_Temp",
    "Battery_Voltage",
    "X_Coarse_Spin",
    "Y_Coarse_Spin",
    "Z_Coarse_Spin",
)

# Exact input order expected by Jay's trained AI model.
MODEL_FEATURES = (
    "X_Coarse_Spin",
    "Y_Coarse_Spin",
    "Z_Coarse_Spin",
    "PD1_CSS_theta",
    "PD2_CSS_theta",
    "PD3_CSS_theta",
    "PD4_CSS_theta",
    "PD5_CSS_theta",
    "PD6_CSS_theta",
    "Battery_Temp",
    "Battery_Voltage",
    "Background_RSSI",
    "NanoCom_Temp",
)

# Nominal baselines from:
# flight/anomaly_detection/artifacts/scaler_params.json
MODEL_CENTERS = dict(zip(MODEL_FEATURES, (
    0.007477581966668367,
    0.0016997822094708681,
    -0.0006497010472230613,
    0.13866694407029587,
    0.04127725586295128,
    0.06689150631427765,
    0.04154961183667183,
    0.08108681972537722,
    0.0502205416560173,
    273.0,
    7875.388888888889,
    -112.0,
    303.25,
)))

MODEL_SCALES = dict(zip(MODEL_FEATURES, (
    0.03475774395822858,
    0.031239014778596662,
    0.04430750904915233,
    0.41116885165683925,
    0.16555884818080813,
    0.2258887022201504,
    0.1708751720143482,
    0.26722170412540436,
    0.2538888896815479,
    127.5,
    648.875,
    10.0,
    115.3625,
)))

MODEL_UNITS = dict(zip(MODEL_FEATURES, (
    "rad/s",
    "rad/s",
    "rad/s",
    "rad",
    "rad",
    "rad",
    "rad",
    "rad",
    "rad",
    "0.1 °C",
    "mV",
    "dBm",
    "0.1 °C",
)))

MODEL_SENSORS = dict(zip(MODEL_FEATURES, (
    "adcs",
    "adcs",
    "adcs",
    "css1",
    "css2",
    "css3",
    "css4",
    "css5",
    "css6",
    "eps",
    "eps",
    "uhf",
    "uhf",
)))

# The UHF temperature channel is not automatically equivalent
# to battery or communication subsystem temperatures.
OPTIONAL_UHF_CHANNELS = tuple(
    signal
    for signal in MODEL_FEATURES
    if signal not in UHF_CHANNELS
)


def _model_profile_from_environment() -> str:
    return os.environ.get(
        "SIMULATOR_OUTPUT_PROFILE",
        "legacy14",
    ).strip().lower()


def _model_demo_fault_from_environment() -> bool:
    return os.environ.get(
        "SIMULATOR_DEMO_FAULT",
        "0",
    ).strip().lower() in {"1", "true", "yes"}


def format_utc(value: datetime) -> str:
    """Format a timezone-aware timestamp in UTC."""
    if value.tzinfo is None:
        raise ValueError(
            "timestamp must include a timezone"
        )

    return value.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        raise ValueError(
            "start time must include a timezone"
        )

    return parsed.astimezone(timezone.utc)


def _parse_source_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(timezone.utc)


def _stable_seed(seed: int, name: str) -> int:
    digest = hashlib.sha256(
        f"{seed}:{name}".encode("utf-8")
    ).digest()

    return int.from_bytes(
        digest[:8],
        "big",
    )


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    seed: int = 42
    run_id: str = "sim-run-42"

    sample_rate_hz: float = 1.0
    start_time: str = "2024-05-01T00:00:00Z"

    template_max_gap_seconds: float = 300.0

    temperature_range_c: tuple[float, float] = (
        15.0, 35.0
    )
    pressure_range_hpa: tuple[float, float] = (
        950.0, 1050.0
    )
    voltage_range_v: tuple[float, float] = (
        7.0, 8.2
    )
    current_range_ma: tuple[float, float] = (
        0.0, 500.0
    )
    gyro_range_dps: tuple[float, float] = (
        -2.0, 2.0
    )

    include_orientation: bool = True
    mode_cycle_seconds: float = 600.0

    # Keep the original simulator behavior by default.
    # Set SIMULATOR_OUTPUT_PROFILE=model13 for AI mode.
    output_profile: str = field(
        default_factory=_model_profile_from_environment
    )

    # Set SIMULATOR_DEMO_FAULT=1 for a reproducible
    # Battery_Voltage fault demonstration.
    demo_fault: bool = field(
        default_factory=_model_demo_fault_from_environment
    )

    def __post_init__(self) -> None:
        if (
            self.sample_rate_hz <= 0
            or not math.isfinite(self.sample_rate_hz)
        ):
            raise ValueError(
                "sample_rate_hz must be positive and finite"
            )

        if self.template_max_gap_seconds <= 0:
            raise ValueError(
                "template_max_gap_seconds must be positive"
            )

        if self.mode_cycle_seconds <= 0:
            raise ValueError(
                "mode_cycle_seconds must be positive"
            )

        if not self.run_id.strip():
            raise ValueError(
                "run_id must not be empty"
            )

        parse_datetime(self.start_time)

        if self.output_profile not in {
            "legacy14", "model13"
        }:
            raise ValueError(
                "output_profile must be "
                "'legacy14' or 'model13'"
            )

        for name in (
            "temperature_range_c",
            "pressure_range_hpa",
            "voltage_range_v",
            "current_range_ma",
            "gyro_range_dps",
        ):
            lower, upper = getattr(self, name)

            if not (
                math.isfinite(lower)
                and math.isfinite(upper)
                and lower < upper
            ):
                raise ValueError(
                    f"{name} must contain two "
                    "finite increasing values"
                )


@dataclass(frozen=True, slots=True)
class ShapeTemplate:
    """Cleaned UHF time-series shape reference."""

    channel: str
    timestamps: tuple[datetime, ...]
    values: tuple[float, ...]
    minimum: float
    maximum: float

    def sample_at(
        self,
        timestamp: datetime,
        max_gap_seconds: float,
    ) -> float | None:

        if (
            not self.timestamps
            or timestamp < self.timestamps[0]
            or timestamp > self.timestamps[-1]
        ):
            return None

        right = bisect.bisect_left(
            self.timestamps,
            timestamp,
        )

        if (
            right < len(self.timestamps)
            and self.timestamps[right] == timestamp
        ):
            return self.values[right]

        if right == 0 or right == len(self.timestamps):
            return None

        left = right - 1

        before = self.timestamps[left]
        after = self.timestamps[right]

        gap = (after - before).total_seconds()

        if gap <= 0 or gap > max_gap_seconds:
            return None

        fraction = (
            (timestamp - before).total_seconds()
            / gap
        )

        return (
            self.values[left]
            + fraction
            * (self.values[right] - self.values[left])
        )


@dataclass(frozen=True, slots=True)
class TelemetryFrame:
    """All synchronized sensor records for one tick."""

    tick: int
    measurement_time: str
    records: tuple[TelemetryRecord, ...]


def load_uhf_templates(
    csv_path: str | Path,
) -> dict[str, ShapeTemplate]:
    """Load and clean OPS-SAT UHF reference channels."""

    path = Path(csv_path)

    grouped: dict[
        str,
        dict[datetime, list[float]],
    ] = {
        name: {}
        for name in (
            *UHF_CHANNELS,
            *OPTIONAL_UHF_CHANNELS,
        )
    }

    with path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as stream:

        reader = csv.DictReader(stream)
        headers = set(reader.fieldnames or ())

        missing = {
            "Timestamp",
            *UHF_CHANNELS,
        } - headers

        if missing:
            raise ValueError(
                "UHF CSV is missing required columns: "
                + ", ".join(sorted(missing))
            )

        for row_number, row in enumerate(
            reader,
            start=2,
        ):
            raw_timestamp = (
                row.get("Timestamp") or ""
            ).strip()

            if not raw_timestamp:
                continue

            try:
                timestamp = _parse_source_datetime(
                    raw_timestamp
                )
            except ValueError as exc:
                raise ValueError(
                    "invalid Timestamp on CSV row "
                    f"{row_number}: {raw_timestamp}"
                ) from exc

            for channel in UHF_CHANNELS:
                raw_value = (
                    row.get(channel) or ""
                ).strip()

                if not raw_value:
                    continue

                try:
                    value = float(raw_value)
                except ValueError:
                    continue

                if not math.isfinite(value):
                    continue

                grouped[channel].setdefault(
                    timestamp,
                    [],
                ).append(value)

    templates: dict[str, ShapeTemplate] = {}

    for channel, samples in grouped.items():
        timestamps = tuple(sorted(samples))

        values = tuple(
            statistics.median(samples[timestamp])
            for timestamp in timestamps
        )

        if not values:
            continue

        if len(values) >= 2:
            percentiles = statistics.quantiles(
                values,
                n=100,
                method="inclusive",
            )
            robust_minimum = percentiles[0]
            robust_maximum = percentiles[-1]
        else:
            robust_minimum = values[0]
            robust_maximum = values[0]

        templates[channel] = ShapeTemplate(
            channel=channel,
            timestamps=timestamps,
            values=values,
            minimum=robust_minimum,
            maximum=robust_maximum,
        )

    return templates


def _scale(
    value: float,
    source_min: float,
    source_max: float,
    target: tuple[float, float],
) -> float:
    target_min, target_max = target

    if source_max <= source_min:
        return (target_min + target_max) / 2.0

    ratio = min(
        1.0,
        max(
            0.0,
            (value - source_min)
            / (source_max - source_min),
        ),
    )

    return (
        target_min
        + ratio * (target_max - target_min)
    )


class TelemetryGenerator:
    """Generate reproducible satellite telemetry frames."""

    def __init__(
        self,
        csv_path: str | Path,
        config: GeneratorConfig | None = None,
    ) -> None:

        self.config = config or GeneratorConfig()

        self.templates = load_uhf_templates(
            csv_path
        )

        self._start_time = parse_datetime(
            self.config.start_time
        )

        # Independent RNG for each legacy sensor.
        self._rng = {
            name: random.Random(
                _stable_seed(
                    self.config.seed,
                    name,
                )
            )
            for name in (
                "temperature",
                "pressure",
                "voltage",
                "current",
                "gyro_x",
                "gyro_y",
                "gyro_z",
                "accel_x",
                "accel_y",
                "accel_z",
            )
        }

        self._angles = [0.0, 0.0, 0.0]
        self._tick = 0

        
        self.fault_injector = ContinuousFaultInjector(
    # Keep the original 14-channel nominal tests fault-free.
    # Allow random faults in model13 mode unless the
    # deterministic demo fault has been enabled.
    probability=(
        0.05
        if (
            self.config.output_profile == "model13"
            and not self.config.demo_fault
        )
        else 0.0
    ),
    seed=self.config.seed + 1001,
    demo_fault_start_tick=(
        15 if self.config.demo_fault else None
    ),
    demo_fault_duration_ticks=14,
)


        # Independent RNG for each AI model channel.
        self._model_rng = {
            name: random.Random(
                _stable_seed(
                    self.config.seed,
                    "model:" + name,
                )
            )
            for name in MODEL_FEATURES
        }

    def _template_value(
        self,
        channel: str,
        tick: int,
    ) -> float | None:
        template = self.templates.get(channel)

        if template is None:
            return None

        timestamp = (
            template.timestamps[0]
            + timedelta(
                seconds=(
                    tick / self.config.sample_rate_hz
                )
            )
        )

        return template.sample_at(
            timestamp,
            self.config.template_max_gap_seconds,
        )

    def _mode(
        self,
        elapsed_seconds: float,
    ) -> str:
        phase = (
            elapsed_seconds
            % self.config.mode_cycle_seconds
        ) / self.config.mode_cycle_seconds

        if phase < 0.60:
            return "nominal"

        if phase < 0.85:
            return "payload"

        return "safe"

    def _record(
        self,
        *,
        tick: int,
        index: int,
        timestamp: str,
        sensor_id: str,
        signal: str,
        value: float,
        unit: str,
        source: str,
        transform_id: str,
        provenance: str = "generated_mock",
    ) -> TelemetryRecord:

        record = TelemetryRecord(
            run_id=self.config.run_id,
            sample_id=(
                f"{self.config.run_id}:"
                f"{tick:08d}:{index:02d}"
            ),
            sequence=tick * 100 + index,
            sensor_id=sensor_id,
            signal=signal,
            value=round(float(value), 6),
            unit=unit,
            measurement_time=timestamp,
            publish_time=timestamp,
            source=source,
            transform_id=transform_id,
            provenance=provenance,
        )

        record.validate()
        return record

    def _model_frame(self) -> TelemetryFrame:
        """Produce exactly 13 model-compatible signals.

        Values are generated near the AI model's training
        baseline. Real UHF data is used only as a shape
        reference, not proof of calibrated sensor identity.
        """

        tick = self._tick
        self._tick += 1

        elapsed = (
            tick / self.config.sample_rate_hz
        )

        timestamp = format_utc(
            self._start_time
            + timedelta(seconds=elapsed)
        )

        records: list[TelemetryRecord] = []

        for index, signal in enumerate(MODEL_FEATURES):
            center = MODEL_CENTERS[signal]
            scale = MODEL_SCALES[signal]

            sample = self._template_value(
                signal,
                tick,
            )

            if sample is not None:
                template = self.templates[signal]

                baseline = _scale(
                    sample,
                    template.minimum,
                    template.maximum,
                    (
                        center - 0.22 * scale,
                        center + 0.22 * scale,
                    ),
                )

                source = (
                    f"ops_sat_uhf_template:{signal}"
                )

                transform = "model13_uhf_shape_v1"

            else:
                phase = 0.43 * index

                baseline = center + scale * (
                    0.10
                    * math.sin(
                        elapsed
                        / (80.0 + 7.0 * index)
                        + phase
                    )
                    + 0.035
                    * math.sin(
                        elapsed / 33.0
                        + 0.14 * index
                    )
                )

                source = "synthetic_model"
                transform = "model13_nominal_v1"

            baseline += (
                scale
                * self._model_rng[signal].uniform(
                    -0.012,
                    0.012,
                )
            )

            records.append(
                self._record(
                    tick=tick,
                    index=index,
                    timestamp=timestamp,
                    sensor_id=MODEL_SENSORS[signal],
                    signal=signal,
                    value=baseline,
                    unit=MODEL_UNITS[signal],
                    source=source,
                    transform_id=transform,
                )
            )

        # Inject faults before MQTT publishes the records.
        records = self.fault_injector.inject_records(
            records
        )

        print(
            f"[MODEL13] tick={tick} "
            f"time={timestamp} records=13"
        )

        for record in records:
            if record.provenance == "fault_injected":
                print(
                    f"[FAULT] {record.signal}="
                    f"{record.value:.4f} {record.unit}"
                )

        return TelemetryFrame(
            tick=tick,
            measurement_time=timestamp,
            records=tuple(records),
        )

    def next_frame(self) -> TelemetryFrame:
        # New AI-compatible generation mode.
        if self.config.output_profile == "model13":
            return self._model_frame()

        # Original 14-channel simulator mode.
        tick = self._tick
        self._tick += 1

        dt = 1.0 / self.config.sample_rate_hz
        elapsed = tick * dt

        timestamp = format_utc(
            self._start_time
            + timedelta(seconds=elapsed)
        )

        records: list[TelemetryRecord] = []

        # ---------------------------------------
        # BMP280: Temperature
        # ---------------------------------------
        temperature_template = self._template_value(
            "NanoMind_Temp",
            tick,
        )

        if temperature_template is None:
            temperature = (
                25.0
                + 3.0
                * math.sin(
                    2.0 * math.pi
                    * elapsed / 86400.0
                )
                + 0.8
                * math.sin(
                    2.0 * math.pi
                    * elapsed / 5400.0
                )
            )
            temperature_source = "synthetic_model"
            temperature_transform = (
                "temperature_fallback_v1"
            )
        else:
            template = self.templates["NanoMind_Temp"]

            temperature = _scale(
                temperature_template,
                template.minimum,
                template.maximum,
                self.config.temperature_range_c,
            )

            temperature_source = (
                "ops_sat_uhf_template:NanoMind_Temp"
            )
            temperature_transform = (
                "temperature_uhf_rescale_v1"
            )

        temperature += (
            self._rng["temperature"].uniform(
                -0.08, 0.08
            )
        )

        temperature = min(
            self.config.temperature_range_c[1],
            max(
                self.config.temperature_range_c[0],
                temperature,
            ),
        )

        records.append(
            self._record(
                tick=tick,
                index=len(records),
                timestamp=timestamp,
                sensor_id="bmp0",
                signal="temperature_c",
                value=temperature,
                unit="°C",
                source=temperature_source,
                transform_id=temperature_transform,
            )
        )

        # ---------------------------------------
        # BMP280: Pressure
        # ---------------------------------------
        pressure = (
            1000.0
            + 4.0
            * math.sin(
                2.0 * math.pi
                * elapsed / 5400.0
            )
            + 1.5
            * math.sin(
                2.0 * math.pi
                * elapsed / 1800.0
            )
            + self._rng["pressure"].uniform(
                -0.15, 0.15
            )
        )

        pressure = min(
            self.config.pressure_range_hpa[1],
            max(
                self.config.pressure_range_hpa[0],
                pressure,
            ),
        )

        records.append(
            self._record(
                tick=tick,
                index=len(records),
                timestamp=timestamp,
                sensor_id="bmp0",
                signal="pressure_hpa",
                value=pressure,
                unit="hPa",
                source="synthetic_model",
                transform_id="pressure_trend_periodic_noise_v1",
            )
        )

        # ---------------------------------------
        # INA219: Voltage
        # ---------------------------------------
        voltage_template = self._template_value(
            "Battery_Voltage",
            tick,
        )

        if voltage_template is None:
            voltage = (
                7.6
                + 0.12
                * math.sin(
                    2.0 * math.pi
                    * elapsed / 7200.0
                )
            )

            voltage_source = "synthetic_model"
            voltage_transform = (
                "voltage_fallback_v1"
            )
        else:
            template = self.templates["Battery_Voltage"]

            voltage = _scale(
                voltage_template,
                template.minimum,
                template.maximum,
                self.config.voltage_range_v,
            )

            voltage_source = (
                "ops_sat_uhf_template:Battery_Voltage"
            )
            voltage_transform = (
                "voltage_uhf_rescale_v1"
            )

        mode = self._mode(elapsed)

        current_baseline = {
            "nominal": 120.0,
            "payload": 250.0,
            "safe": 70.0,
        }[mode]

        current = (
            current_baseline
            + self._rng["current"].uniform(
                -5.0, 5.0
            )
        )

        current = min(
            self.config.current_range_ma[1],
            max(
                self.config.current_range_ma[0],
                current,
            ),
        )

        voltage -= (
            max(0.0, current - 120.0)
            * 0.0002
        )

        voltage += (
            self._rng["voltage"].uniform(
                -0.008, 0.008
            )
        )

        voltage = min(
            self.config.voltage_range_v[1],
            max(
                self.config.voltage_range_v[0],
                voltage,
            ),
        )

        records.append(
            self._record(
                tick=tick,
                index=len(records),
                timestamp=timestamp,
                sensor_id="ina219",
                signal="bus_voltage_v",
                value=voltage,
                unit="V",
                source=voltage_source,
                transform_id=voltage_transform,
            )
        )

        # INA219: Current
        records.append(
            self._record(
                tick=tick,
                index=len(records),
                timestamp=timestamp,
                sensor_id="ina219",
                signal="current_ma",
                value=current,
                unit="mA",
                source="synthetic_load_model",
                transform_id=f"current_mode_{mode}_v1",
            )
        )

        # INA219: Calculated power
        records.append(
            self._record(
                tick=tick,
                index=len(records),
                timestamp=timestamp,
                sensor_id="ina219",
                signal="power_mw",
                value=voltage * current,
                unit="mW",
                source=(
                    "derived:bus_voltage_v*current_ma"
                ),
                transform_id="power_product_v1",
                provenance="derived",
            )
        )

        # ---------------------------------------
        # MPU6050: Angular velocity
        # ---------------------------------------
        gyro_values: list[float] = []

        for axis, channel in zip(
            ("x", "y", "z"),
            (
                "X_Coarse_Spin",
                "Y_Coarse_Spin",
                "Z_Coarse_Spin",
            ),
        ):
            raw = self._template_value(
                channel,
                tick,
            )

            if raw is None:
                raw = (
                    0.15
                    * math.sin(
                        2.0 * math.pi
                        * elapsed
                        / (1800.0 + ord(axis))
                    )
                )

                template_source = (
                    "synthetic_motion_model"
                )
                transform_id = (
                    f"gyro_{axis}_fallback_v1"
                )

            else:
                template = self.templates[channel]

                raw = _scale(
                    raw,
                    template.minimum,
                    template.maximum,
                    self.config.gyro_range_dps,
                )

                template_source = (
                    f"ops_sat_uhf_template:{channel}"
                )
                transform_id = (
                    f"gyro_{axis}_uhf_rescale_v1"
                )

            gyro = (
                raw
                + self._rng[
                    f"gyro_{axis}"
                ].uniform(-0.01, 0.01)
            )

            gyro = min(
                self.config.gyro_range_dps[1],
                max(
                    self.config.gyro_range_dps[0],
                    gyro,
                ),
            )

            gyro_values.append(gyro)

            records.append(
                self._record(
                    tick=tick,
                    index=len(records),
                    timestamp=timestamp,
                    sensor_id="imu0",
                    signal=f"gyro_{axis}_dps",
                    value=gyro,
                    unit="°/s",
                    source=template_source,
                    transform_id=transform_id,
                )
            )

        # Integrate gyro values for orientation.
        if self.config.include_orientation:
            for index, rate in enumerate(gyro_values):
                self._angles[index] = (
                    self._angles[index]
                    + rate * dt
                    + 180.0
                ) % 360.0 - 180.0

        roll, pitch, _yaw = (
            math.radians(angle)
            for angle in self._angles
        )

        # ---------------------------------------
        # MPU6050: Simulated accelerometer
        # ---------------------------------------
        accel_values = (
            -math.sin(pitch)
            + gyro_values[1] * 0.0005,

            math.sin(roll)
            + gyro_values[0] * 0.0005,

            math.cos(roll)
            * math.cos(pitch),
        )

        for axis, value in zip(
            ("x", "y", "z"),
            accel_values,
        ):
            value += self._rng[
                f"accel_{axis}"
            ].uniform(-0.005, 0.005)

            records.append(
                self._record(
                    tick=tick,
                    index=len(records),
                    timestamp=timestamp,
                    sensor_id="imu0",
                    signal=f"accel_{axis}_g",
                    value=value,
                    unit="g",
                    source="synthetic_gravity_motion_model",
                    transform_id=(
                        f"accel_{axis}_gravity_vibration_v1"
                    ),
                )
            )

        # Derived roll, pitch, yaw values.
        if self.config.include_orientation:
            for axis, angle in zip(
                ("roll", "pitch", "yaw"),
                self._angles,
            ):
                records.append(
                    self._record(
                        tick=tick,
                        index=len(records),
                        timestamp=timestamp,
                        sensor_id="imu0",
                        signal=f"{axis}_deg",
                        value=angle,
                        unit="°",
                        source="derived:integrated_gyro",
                        transform_id=(
                            "orientation_gyro_integration_v1"
                        ),
                        provenance="derived",
                    )
                )

        # Fault injection supports both signal profiles.
        records = self.fault_injector.inject_records(
            records
        )

        print(
            f"\n--- Frame Tick: {tick} "
            f"| Time: {timestamp} ---"
        )

        for record in records:
            print(
                f"{record.signal: <15}: "
                f"{record.value:>8.2f} {record.unit}"
            )

        return TelemetryFrame(
            tick=tick,
            measurement_time=timestamp,
            records=tuple(records),
        )

    def frames(
        self,
        count: int | None = None,
    ) -> Iterator[TelemetryFrame]:
        """Yield a fixed number of frames, or indefinitely."""

        if count is not None and count < 0:
            raise ValueError(
                "count must be non-negative or None"
            )

        emitted = 0

        while count is None or emitted < count:
            yield self.next_frame()
            emitted += 1

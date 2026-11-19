
"""Seeded, model-compatible simulated satellite telemetry faults.

Runs on the simulator/laptop before MQTT publication.
"""

from __future__ import annotations

import random
from dataclasses import replace
from typing import Iterable

# Exact features expected by Jay's anomaly detection model.
MODEL_SIGNALS = (
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

# Scales from the model's saved RobustScaler configuration.
MODEL_SCALES = dict(zip(MODEL_SIGNALS, (
    0.034757744,
    0.031239015,
    0.044307509,
    0.411168852,
    0.165558848,
    0.225888702,
    0.170875172,
    0.267221704,
    0.253888890,
    127.5,
    648.875,
    10.0,
    115.3625,
)))

LEGACY_SAFE_SIGNALS = (
    "temperature_c",
    "pressure_hpa",
)

LEGACY_SCALE = {
    "temperature_c": 3.0,
    "pressure_hpa": 4.0,
}


class ContinuousFaultInjector:
    """Inject seeded spike, noise, drift, and stuck-at faults.

    An optional demo fault sets Battery_Voltage to 4200 mV
    for a known number of telemetry frames.
    """

    def __init__(
        self,
        probability: float = 0.08,
        seed: int = 42,
        demo_fault_start_tick: int | None = None,
        demo_fault_duration_ticks: int = 12,
    ) -> None:
        if not 0.0 <= probability <= 1.0:
            raise ValueError(
                "probability must be between 0 and 1"
            )

        if (
            demo_fault_start_tick is not None
            and demo_fault_start_tick < 0
        ):
            raise ValueError(
                "demo_fault_start_tick must be non-negative"
            )

        if demo_fault_duration_ticks <= 0:
            raise ValueError(
                "demo_fault_duration_ticks must be positive"
            )

        self.probability = probability
        self.rng = random.Random(seed)

        self.demo_fault_start_tick = demo_fault_start_tick
        self.demo_fault_duration_ticks = (
            demo_fault_duration_ticks
        )

        self.active_faults: dict[str, dict] = {}
        self.safe_sensors: list[str] = []
        self.tick = 0

    def _start_random_fault(
        self,
        available: list[str],
        scales: dict[str, float],
    ) -> None:
        candidates = [
            signal
            for signal in available
            if signal not in self.active_faults
        ]

        if not candidates:
            return

        target = self.rng.choice(candidates)

        fault_type = self.rng.choice(
            ("spike", "noise", "drift", "stuck")
        )

        duration = (
            1 if fault_type == "spike"
            else self.rng.randint(5, 24)
        )

        scale = scales[target]

        self.active_faults[target] = {
            "type": fault_type,
            "duration": duration,
            "scale": scale,
            "current_drift": 0.0,
            "drift_rate": (
                self.rng.choice((-1.0, 1.0))
                * self.rng.uniform(0.4, 1.0)
                * scale
            ),
            "stuck_value": None,
        }

        print(
            f"[FAULT ALERT] Started {fault_type} "
            f"on {target} for {duration} ticks"
        )

    def inject_records(
        self,
        records: Iterable,
    ) -> list:
        """Inject faults without changing sample IDs or timestamps."""

        records = list(records)
        present = {record.signal for record in records}

        # Determine whether the model13 or old simulator
        # signal profile is being used.
        if set(MODEL_SIGNALS).issubset(present):
            scales = MODEL_SCALES

            available = [
                s for s in (
                    "Battery_Voltage",
                    "Battery_Temp",
                    "NanoCom_Temp",
                    "Background_RSSI",
                    "X_Coarse_Spin",
                    "Y_Coarse_Spin",
                    "Z_Coarse_Spin",
                    *MODEL_SIGNALS[3:9],
                )
                if s in present
            ]
        else:
            scales = LEGACY_SCALE

            available = [
                s for s in LEGACY_SAFE_SIGNALS
                if s in present
            ]

        self.safe_sensors = list(available)

        # Deterministic demonstration:
        # Inject a large battery voltage drop.
        if (
            self.demo_fault_start_tick is not None
            and self.tick == self.demo_fault_start_tick
            and "Battery_Voltage" in present
        ):
            self.active_faults["Battery_Voltage"] = {
                "type": "voltage_sag",
                "duration": self.demo_fault_duration_ticks,
                "scale": MODEL_SCALES["Battery_Voltage"],
            }

            print(
                "[FAULT ALERT] DEMO voltage sag started "
                f"at tick {self.tick}; target=4200 mV"
            )

        elif (
            available
            and self.rng.random() < self.probability
        ):
            self._start_random_fault(
                available,
                scales,
            )

        updated = []

        for record in records:
            fault = self.active_faults.get(record.signal)

            if not fault:
                updated.append(record)
                continue

            kind = fault["type"]
            scale = fault["scale"]
            new_value = record.value

            if kind == "voltage_sag":
                new_value = 4200.0

            elif kind == "spike":
                new_value += (
                    self.rng.choice((-1.0, 1.0))
                    * self.rng.uniform(4.0, 7.0)
                    * scale
                )

            elif kind == "noise":
                new_value += (
                    self.rng.uniform(-3.0, 3.0)
                    * scale
                )

            elif kind == "drift":
                fault["current_drift"] += (
                    fault["drift_rate"]
                )

                new_value += fault["current_drift"]

            elif kind == "stuck":
                if fault["stuck_value"] is None:
                    fault["stuck_value"] = record.value

                new_value = fault["stuck_value"]

            # TelemetryRecord is frozen; replace() creates a
            # modified copy while preserving sample identity.
            updated.append(
                replace(
                    record,
                    value=round(float(new_value), 6),
                    provenance="fault_injected",
                    transform_id=(
                        f"{record.transform_id}|"
                        f"fault_{kind}_v1"
                    ),
                )
            )

            fault["duration"] -= 1

        expired = [
            signal
            for signal, fault in self.active_faults.items()
            if fault["duration"] <= 0
        ]

        for signal in expired:
            print(
                "[FAULT ALERT] "
                f"{self.active_faults[signal]['type']} "
                f"resolved on {signal}"
            )
            del self.active_faults[signal]

        self.tick += 1
        return updated

"""Command-line entry point for generated telemetry and flight MQTT streaming."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

from .generation import GeneratorConfig, TelemetryGenerator
from .mqtt import MqttConfig, create_paho_publisher
from .pipeline import select_records
from .hardware_faults import (
    select_hardware_fault,
    corresponding_hardware_fault,
)


def _default_csv_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "uhf_telemetry.csv"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate nominal satellite sensor telemetry and publish it to flight over MQTT."
    )
    parser.add_argument("--uhf-csv", type=Path, default=_default_csv_path())
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--rate-hz", type=float, default=1.0)
    parser.add_argument(
        "--signals",
        default=None,
        help="Optional comma-separated signal selection, e.g. bus_voltage_v for a 10 record/s test.",
    )
    parser.add_argument("--start-time", default="2024-05-01T00:00:00Z")
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=0.0,
        help="Stream indefinitely when zero; otherwise stop after this simulated duration.",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=None,
        help="Optional fixed frame count; useful for finite runs and testing.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print JSON records; do not connect to MQTT.")
    parser.add_argument("--mqtt-host", default=os.getenv("SIMULATOR_MQTT_HOST", "localhost"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.getenv("SIMULATOR_MQTT_PORT", "1883")))
    parser.add_argument(
        "--mqtt-topic-prefix",
        default=os.getenv("SIMULATOR_MQTT_TOPIC_PREFIX", "satellite/flight/telemetry"),
    )
    parser.add_argument("--mqtt-qos", type=int, default=int(os.getenv("SIMULATOR_MQTT_QOS", "1")))
    parser.add_argument("--mqtt-client-id", default=os.getenv("SIMULATOR_MQTT_CLIENT_ID", "satellite-simulator"))
    parser.add_argument("--mqtt-username", default=os.getenv("SIMULATOR_MQTT_USERNAME"))
    parser.add_argument("--mqtt-password", default=os.getenv("SIMULATOR_MQTT_PASSWORD"))
    parser.add_argument(
        "--hardware-faults",
        action="store_true",
        help="Enable physical sensor fault testing on the Pi.",
    )
    parser.add_argument(
        "--hardware-fault-tick",
        type=int,
        default=15,
        help="Frame number at which to inject a physical fault.",
    )
    parser.add_argument(
        "--match-hardware-faults",
        action="store_true",
        help="Mirror supported simulated faults on physical sensors.",
    )
    return parser


def _frame_count(args: argparse.Namespace) -> int | None:
    if args.frames is not None:
        if args.frames < 0:
            raise ValueError("--frames must be non-negative")
        return args.frames
    if args.duration_seconds < 0 or not math.isfinite(args.duration_seconds):
        raise ValueError("--duration-seconds must be finite and non-negative")
    if args.duration_seconds > 0:
        return math.ceil(args.duration_seconds * args.rate_hz)
    if args.dry_run:
        return 1
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.uhf_csv.is_file():
            raise ValueError(f"UHF CSV does not exist: {args.uhf_csv}")
        if args.rate_hz <= 0 or not math.isfinite(args.rate_hz):
            raise ValueError("--rate-hz must be finite and positive")
        if args.hardware_faults and args.match_hardware_faults:
            raise ValueError("Select only one hardware fault mode")

        if args.hardware_faults or args.match_hardware_faults:
            if args.dry_run:
                raise ValueError(
                    "Hardware faults cannot run in dry-run mode"
                )
            if args.hardware_fault_tick < 0:
                raise ValueError(
                    "Hardware fault tick must be non-negative"
                )
            if len(os.getenv("CAPSTONE_FAULT_KEY", "")) < 32:
                raise ValueError(
                    "Hardware fault key is missing or too short"
                )

        hardware_rng = random.Random(args.seed + 2001)

        count = _frame_count(args)
        selected_signals = (
            {signal.strip() for signal in args.signals.split(",") if signal.strip()}
            if args.signals is not None
            else None
        )
        run_id = args.run_id or f"sim-run-{args.seed}"
        generator = TelemetryGenerator(
            args.uhf_csv,
            GeneratorConfig(
                seed=args.seed,
                run_id=run_id,
                sample_rate_hz=args.rate_hz,
                start_time=args.start_time,
            ),
        )

        if args.dry_run:
            for frame in generator.frames(count):
                for record in select_records(frame, selected_signals):
                    print(record.to_json())
            return 0

        publisher = create_paho_publisher(
            MqttConfig(
                host=args.mqtt_host,
                port=args.mqtt_port,
                topic_prefix=args.mqtt_topic_prefix,
                qos=args.mqtt_qos,
                client_id=args.mqtt_client_id,
                username=args.mqtt_username,
                password=args.mqtt_password,
            )
        )
        publisher.connect()
        started = time.monotonic()
        published = 0
        requested_sensors = set()
        try:
            for frame in generator.frames(count):
                due = started + frame.tick / args.rate_hz
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                if (
                    args.hardware_faults
                    and frame.tick == args.hardware_fault_tick
                ):
                    selected = select_hardware_fault(hardware_rng)

                    publisher.publish_hardware_fault(
                        selected["sensor"],
                        selected["fault"],
                    )

                if args.match_hardware_faults:
                    for record in frame.records:

                        if record.provenance != "fault_injected":
                            continue

                        for tag in record.transform_id.split("|"):

                            if not (
                                tag.startswith("fault_")
                                and tag.endswith("_v1")
                            ):
                                continue

                            kind = tag[6:-3]

                            selected = corresponding_hardware_fault(
                                record.signal, kind
                            )

                            if selected is None:
                                continue

                            sensor = selected["sensor"]
                            fault = selected["fault"]

                            if sensor in requested_sensors:
                                continue

                            publisher.publish_hardware_fault(
                                sensor, fault
                            )

                            requested_sensors.add(sensor)

                            print(
                                f"[MATCHED FAULT] "
                                f"{record.signal}/{kind} -> "
                                f"{sensor}/{fault}"
                            )

                for record in select_records(frame, selected_signals):
                    publisher.publish(record)
                    published += 1
        except KeyboardInterrupt:
            print(f"\nStopped. Published {published} telemetry records.", file=sys.stderr)
        finally:
            publisher.close()
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"simulator: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
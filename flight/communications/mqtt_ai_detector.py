"""Pi MQTT AI consumer: assemble live 13-channel frames before inference.

Run alongside mqtt_blackout_collector. The AI consumes Pi-local telemetry
regardless of whether the simulated ground downlink is in BLACKOUT.
"""
from __future__ import annotations

import argparse
import json
from collections import OrderedDict, deque
from uuid import uuid4

import paho.mqtt.client as mqtt

from flight.anomaly_detection.edge_detector import EdgeAnomalyDetector
from shared.protocol import TelemetryRecord


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Raspberry Pi MQTT anomaly detector")
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    args = parser.parse_args()

    detector = EdgeAnomalyDetector()
    features = set(detector.features)
    incomplete: OrderedDict[tuple[str, str], dict[str, float]] = OrderedDict()
    completed = deque(maxlen=256)
    current_run: str | None = None

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"capstone-ai-{uuid4().hex[:8]}",
    )

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"[AI MQTT] Connection refused: {reason_code}", flush=True)
            return
        c.subscribe("satellite/flight/telemetry/#", qos=1)
        print("[AI MQTT] Listening for complete 13-channel frames", flush=True)

    def on_message(c, userdata, message):
        nonlocal current_run
        try:
            record = TelemetryRecord.from_json(message.payload)
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
            print(f"[AI MQTT] Discarded invalid message: {exc}", flush=True)
            return

        if record.signal not in features:
            return

        if current_run != record.run_id:
            current_run = record.run_id
            detector.window_buffer.clear()
            detector.score_history.clear()
            incomplete.clear()
            completed.clear()
            print(f"[AI] New run: {current_run}; model history reset", flush=True)

        key = (record.run_id, record.measurement_time)
        if key in completed:
            return  # MQTT QoS 1 duplicate of an already processed frame

        readings = incomplete.setdefault(key, {})
        readings[record.signal] = float(record.value)
        if len(readings) < len(features):
            while len(incomplete) > 32:
                incomplete.popitem(last=False)
            return

        incomplete.pop(key, None)
        completed.append(key)
        result = detector.push_and_evaluate(readings)
        if result["status"] == "WARMING_UP":
            print(
                f"[AI] Frame received: {len(completed)} | "
                f"warming up {result['buffered']}/{result['required']}",
                flush=True,
            )
        else:
            print(
                f"[AI] {result['status']} "
                f"score={result['anomaly_score']} "
                f"threshold={result['threshold']} "
                f"sensor={result['suspect_sensor']} "
                f"subsystem={result['suspect_subsystem']}",
                flush=True,
            )

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
    print(f"[AI MQTT] Connecting to {args.mqtt_host}:{args.mqtt_port}", flush=True)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[AI MQTT] Stopped", flush=True)
    finally:
        client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Pi MQTT AI consumer: assemble live 13-channel frames before inference.

Run alongside mqtt_blackout_collector. The AI consumes Pi-local telemetry
regardless of whether the simulated ground downlink is in BLACKOUT.

With --db (set by scripts/demo.py to the collector's SQLite file) every NOMINAL->ANOMALY and
ANOMALY->NOMINAL decision, with the suspect sensor/subsystem from triage, is appended to the
flight hash-chained audit log, so AI decisions reach ground through store-and-forward
(REQ-AUD-01). Inference latency is summarised periodically (REQ-ML-01, <= 50 ms).
"""
from __future__ import annotations

import argparse
import statistics
import time
from collections import OrderedDict, deque
from pathlib import Path
from uuid import uuid4

import paho.mqtt.client as mqtt

from flight.anomaly_detection.edge_detector import EdgeAnomalyDetector
from shared.audit import AuditLog
from shared.protocol import TelemetryRecord


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Raspberry Pi MQTT anomaly detector")
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--db", type=Path, default=None,
                        help="Flight SQLite file whose audit chain records AI decisions")
    parser.add_argument("--perf-every", type=int, default=50,
                        help="Audit an inference-latency summary every N evaluations")
    args = parser.parse_args()

    detector = EdgeAnomalyDetector()
    audit = AuditLog(args.db, chain_prefix="pi01", actor="ai-detector") if args.db else None
    latencies: deque[float] = deque(maxlen=max(args.perf_every, 1))
    status = {"anomaly": False, "evaluations": 0}
    if audit:
        audit.append("AI_START", {"model": "model_int8.tflite", "threshold": detector.threshold,
                                  "window": detector.window_size, "features": len(detector.features)})
        print(f"[AI] Audit chain {audit.chain_id} in {args.db}", flush=True)
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
        started = time.perf_counter()
        result = detector.push_and_evaluate(readings)
        latency_ms = (time.perf_counter() - started) * 1000
        if result["status"] != "WARMING_UP":
            record_decision(result, latency_ms, record)
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

    def record_decision(result: dict, latency_ms: float, record: TelemetryRecord) -> None:
        latencies.append(latency_ms)
        status["evaluations"] += 1
        if audit is None:
            return
        if result["is_anomaly"] != status["anomaly"]:
            status["anomaly"] = result["is_anomaly"]
            audit.append("ANOMALY_DETECTED" if result["is_anomaly"] else "ANOMALY_CLEARED", {
                "run_id": record.run_id, "measurement_time": record.measurement_time,
                "score": result["anomaly_score"], "threshold": result["threshold"],
                "suspect_sensor": result["suspect_sensor"],
                "suspect_subsystem": result["suspect_subsystem"],
                "top_channel_z": result["top_channel_z"], "latency_ms": round(latency_ms, 3)})
        if status["evaluations"] % max(args.perf_every, 1) == 0:
            ordered = sorted(latencies)
            audit.append("AI_PERFORMANCE", {
                "evaluations": status["evaluations"],
                "latency_ms_p50": round(statistics.median(ordered), 3),
                "latency_ms_p95": round(ordered[int(0.95 * (len(ordered) - 1))], 3),
                "latency_ms_max": round(ordered[-1], 3), "target_ms": 50})

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
        if audit:
            audit.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Two-terminal capstone demo supervisor (stdlib only).

Mac: one launcher supervises the simulator and ground receiver.
Pi:  one launcher supervises the MQTT blackout collector and AI detector.

No schema or transport changes. The Pi-local broker and Tailscale remain prerequisites.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def log(tag: str, message: str) -> None:
    print(f"[{tag}] {message}", flush=True)


def security_check(role: str) -> None:
    """Fail fast (before starting any process) if this device lacks its link keys."""
    from shared.security import keystore
    try:
        keystore.load_link_master_key()
        if role == "pi":
            keystore.load_verify_key()
        else:
            keystore.load_signing_key()
        info = keystore.describe()
        period = keystore.rotation_seconds()
    except keystore.KeyMaterialError as exc:
        raise RuntimeError(f"Security keys not provisioned: {exc}") from exc
    log("SECURITY", f"AES-256-GCM link key fp={info[keystore.LINK_KEY_FILE]} "
                    f"signing identity={info.get('signing_identity', 'missing')} rotation={period}s "
                    "(fingerprints must match on Pi and Mac)")


def broker_check(host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=5):
            pass
    except OSError as exc:
        raise RuntimeError(f"MQTT broker {host}:{port} is unreachable: {exc}") from exc


class Supervisor:
    def __init__(self) -> None:
        self.children: dict[str, subprocess.Popen[str]] = {}
        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()

    def launch(self, name: str, module: str, *arguments: str, env: dict | None = None) -> None:
        options = os.environ.copy()
        options["PYTHONUNBUFFERED"] = "1"
        if env:
            options.update(env)
        proc = subprocess.Popen(
            [PYTHON, "-u", "-m", module, *arguments],
            cwd=ROOT, env=options, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        self.children[name] = proc

        def read_lines() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.messages.put((name, line.rstrip("\r\n")))

        threading.Thread(target=read_lines, daemon=True).start()

    def next(self, timeout: float = 0.4) -> tuple[str, str] | None:
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def stopped_unexpectedly(self) -> str | None:
        for name, proc in self.children.items():
            if proc.poll() is not None:
                return f"{name} exited with code {proc.returncode}"
        return None

    def stop(self) -> None:
        for proc in self.children.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in self.children.values():
            if proc.poll() is None:
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)


def database_counts(path: Path, *, pi: bool = False) -> tuple[int, int, int]:
    if not path.is_file():
        return 0, 0, 0
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
            total = db.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
            events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            pending = 0
            if pi:
                pending = db.execute(
                    "SELECT COUNT(*) FROM telemetry WHERE acknowledged_at IS NULL"
                ).fetchone()[0]
            return total, events, pending
    except sqlite3.Error:
        return 0, 0, 0


def ground_report(path: Path, expected: int, min_events: int) -> bool:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT payload FROM telemetry").fetchall()
        event_states = [r[0] for r in db.execute(
            "SELECT state FROM events ORDER BY event_time, event_uid"
        )]
    records = [json.loads(row[0]) for row in rows]
    faults = [r for r in records if r.get("provenance") == "fault_injected"]
    log("GROUND FINAL", f"Telemetry: {len(records)}/{expected}; injected samples: {len(faults)}")
    log("GROUND FINAL", f"Link events: {' -> '.join(event_states) or 'none'}")
    log("GROUND FINAL", f"SQLite: {path.relative_to(ROOT)}")
    if len(records) != expected or len(event_states) < min_events:
        log("WARNING", "Run incomplete: check Pi collector, MQTT connectivity and downlink ACKs")
        return False
    log("SUCCESS", "All expected telemetry reached ground; Pi ACK status is shown by the Pi launcher")
    return True


def pi_demo(args: argparse.Namespace) -> int:
    security_check("pi")
    broker_check(args.mqtt_host, args.mqtt_port)
    filename = ROOT / "flight" / "storage" / f"demo_pi_{int(time.time())}.sqlite3"
    supervisor = Supervisor()
    collector_ready = ai_ready = ready_reported = False
    last_status_bucket = -1
    first_nominal = False
    warmup_reports = set()
    latest_ai = None
    ai_alerts = 0
    peak_score = float("-inf")
    try:
        log("PI", "Starting flight storage/blackout collector and AI detector")
        log("PI", f"Blackout starts {args.blackout_after:g}s after first sample; lasts {args.blackout_duration:g}s")
        supervisor.launch("FLIGHT", "flight.communications.mqtt_blackout_collector",
                          "--mqtt-host", args.mqtt_host, "--mqtt-port", str(args.mqtt_port),
                          "--blackout-after", str(args.blackout_after),
                          "--blackout-duration", str(args.blackout_duration), "--db", str(filename))
        supervisor.launch("AI", "flight.communications.mqtt_ai_detector",
                          "--mqtt-host", args.mqtt_host, "--mqtt-port", str(args.mqtt_port))
        started = time.monotonic()
        while True:
            item = supervisor.next()
            if item:
                source, line = item
                if source == "FLIGHT":
                    if "Connected; subscribed" in line:
                        collector_ready = True
                    elif "STATE=" in line:
                        log("LINK", line.split(" | ", 1)[0].removeprefix("[FLIGHT MQTT] ") +
                            (" | " + line.split(" | ", 1)[1] if " | " in line else ""))
                    elif "state=" in line and "saved=" in line:
                        match = re.search(r"t=\s*([\d.]+)s", line)
                        if match:
                            bucket = int(float(match.group(1))) // 15
                            if bucket != last_status_bucket:
                                last_status_bucket = bucket
                                log("PI STORAGE", line.removeprefix("[FLIGHT MQTT] ").strip())
                    elif "SECURITY" in line or "KEY_ROTATION" in line:
                        log("SECURITY", line.removeprefix("[FLIGHT MQTT] ").removeprefix("SECURITY "))
                    elif "Command" in line or "Invalid" in line or "disconnected" in line.lower():
                        log("PI", line)
                else:
                    if "Listening for complete" in line:
                        ai_ready = True
                    elif "New run:" in line:
                        log("AI", line.removeprefix("[AI] "))
                    elif "warming up" in line:
                        match = re.search(r"warming up (\d+)/", line)
                        if match:
                            n = int(match.group(1))
                            if n in (1, 5, 9) and n not in warmup_reports:
                                warmup_reports.add(n)
                                log("AI", f"WARMING_UP {n}/10 frames")
                    elif "[AI] ANOMALY" in line:
                        ai_alerts += 1
                        latest_ai = line.removeprefix("[AI] ")
                        m = re.search(r"score=([-\d.]+)", latest_ai)
                        if m:
                            peak_score = max(peak_score, float(m.group(1)))
                        log("AI ALERT", latest_ai)
                    elif "[AI] NOMINAL" in line and not first_nominal:
                        first_nominal = True
                        log("AI", line.removeprefix("[AI] "))
                    elif "ERROR" in line or "Traceback" in line or "Discarded" in line:
                        log("AI", line)
            if collector_ready and ai_ready and not ready_reported:
                ready_reported = True
                log("READY", "Pi services are subscribed. Start the Mac launcher NOW.")
                log("READY", "Keep this Pi terminal open; Ctrl+C after the Mac finishes")
            err = supervisor.stopped_unexpectedly()
            if err:
                log("ERROR", err)
                return 2
            if not ready_reported and time.monotonic() - started > 25:
                log("ERROR", "Pi services did not subscribe in 25 seconds. Check logs/dependencies")
                return 2
    except KeyboardInterrupt:
        log("PI", "Stopping both flight services...")
    finally:
        supervisor.stop()
        total, events, pending = database_counts(filename, pi=True)
        log("PI FINAL", f"Stored={total} pending={pending} link_events={events}")
        if latest_ai:
            log("PI FINAL", f"AI alerts={ai_alerts}; peak score={peak_score:.4f}")
            log("PI FINAL", f"Last AI alert: {latest_ai}")
        log("PI FINAL", f"SQLite: {filename.relative_to(ROOT)}")
    return 0


def mac_demo(args: argparse.Namespace) -> int:
    csv = Path(args.csv)
    if not csv.is_absolute():
        csv = ROOT / csv
    if not csv.is_file():
        raise RuntimeError(f"Missing telemetry CSV: {csv}. Keep it on your Mac; do not commit it")
    if args.frames <= 0 or args.rate_hz <= 0:
        raise RuntimeError("--frames and --rate-hz must both be positive")
    security_check("mac")
    broker_check(args.broker_host, args.broker_port)
    run_id = f"demo-{int(time.time())}"
    db = ROOT / "ground" / "ingestion" / f"demo_ground_{run_id}.sqlite3"
    expected = args.frames * 13
    manager = Supervisor()
    last_count = -1
    try:
        log("MAC", f"Run={run_id} | mode={args.mode} | {args.frames} frames x 13 = {expected} samples")
        log("MAC", "Starting ground receiver and waiting for MQTT subscription...")
        manager.launch("GROUND", "ground.ingestion.mqtt_ground",
                       "--broker-host", args.broker_host, "--broker-port", str(args.broker_port),
                       "--db", str(db))
        ready = False
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            item = manager.next()
            if item and item[0] == "GROUND":
                if "Subscribed" in item[1]:
                    ready = True
                    break
                if "Rejected" in item[1] or "Unable to connect" in item[1]:
                    log("GROUND", item[1])
            err = manager.stopped_unexpectedly()
            if err:
                raise RuntimeError(err)
        if not ready:
            raise RuntimeError("Ground MQTT subscription timed out; is Pi broker/Serve running?")
        log("GROUND", "Subscribed. Starting simulator; ground will receive the Pi's downlink")
        manager.launch("SIM", "simulator",
                       "--uhf-csv", str(csv), "--mqtt-host", args.broker_host,
                       "--mqtt-port", str(args.broker_port),
                       "--rate-hz", str(args.rate_hz), "--frames", str(args.frames),
                       "--run-id", run_id,
                       env={"SIMULATOR_OUTPUT_PROFILE": "model13",
                            "SIMULATOR_DEMO_FAULT": "1" if args.mode == "demo" else "0"})
        if args.mode == "random":
            log("MAC", "Random faults enabled (5% chance/frame); no anomaly is guaranteed")
        while True:
            item = manager.next()
            if item:
                name, line = item
                if name == "SIM":
                    if "[MODEL13] tick=" in line:
                        m = re.search(r"tick=(\d+)", line)
                        tick = int(m.group(1)) if m else -1
                        if tick == 0 or (tick + 1) % 5 == 0 or tick == args.frames - 1:
                            log("SIM", f"Frame {tick + 1}/{args.frames}: 13 sensor readings sent")
                    elif "[FAULT" in line:
                        log("FAULT", line.removeprefix("[FAULT ALERT] ").removeprefix("[FAULT] "))
                    elif line and ("Traceback" in line or "simulator:" in line):
                        log("SIM ERROR", line)
                elif "events_unique=" in line:
                    m = re.search(r"telemetry_unique=(\d+) events_unique=(\d+)", line)
                    if m:
                        n, e = map(int, m.groups())
                        if n == expected or n - last_count >= 26 or (e >= args.min_events and n > last_count):
                            last_count = n
                            log("GROUND", f"Received {n}/{expected} unique samples; {e} link events")
                elif "SECURITY" in line:
                    log("SECURITY", line.removeprefix("[GROUND MQTT] ").removeprefix("SECURITY "))
                elif "Rejected" in line or "Disconnected" in line:
                    log("GROUND", line)
            sim = manager.children["SIM"]
            if sim.poll() is not None:
                if sim.returncode != 0:
                    raise RuntimeError(f"Simulator exited with code {sim.returncode}")
                break
            if manager.children["GROUND"].poll() is not None:
                raise RuntimeError("Ground receiver stopped unexpectedly")
        log("MAC", "Simulator finished. Checking ground store-and-forward delivery...")
        deadline = time.monotonic() + args.sync_timeout
        while time.monotonic() < deadline:
            total, events, _ = database_counts(db)
            if total >= expected and events >= args.min_events:
                break
            time.sleep(1)
        total, events, _ = database_counts(db)
        if total < expected or events < args.min_events:
            log("WARNING", f"Ground currently has {total}/{expected} samples, {events} events")
        return 0 if ground_report(db, expected, args.min_events) else 2
    except KeyboardInterrupt:
        log("MAC", "Interrupted; stopping Mac simulator and ground receiver")
        return 130
    finally:
        manager.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Capstone demo: one Mac terminal + one Pi terminal")
    roles = parser.add_subparsers(dest="role", required=True)
    pi = roles.add_parser("pi", help="Run AI and blackout collector together on Raspberry Pi")
    pi.add_argument("--mqtt-host", default="127.0.0.1")
    pi.add_argument("--mqtt-port", type=int, default=1883)
    pi.add_argument("--blackout-after", type=float, default=65)
    pi.add_argument("--blackout-duration", type=float, default=60)
    mac = roles.add_parser("mac", help="Run simulator and ground together on Mac")
    mac.add_argument("--broker-host", default="100.83.109.119")
    mac.add_argument("--broker-port", type=int, default=1883)
    mac.add_argument("--csv", default="docs/uhf_telemetry.csv")
    mac.add_argument("--mode", choices=("demo", "random"), default="demo")
    mac.add_argument("--frames", type=int, default=36)
    mac.add_argument("--rate-hz", type=float, default=0.2)
    mac.add_argument("--sync-timeout", type=float, default=90)
    mac.add_argument("--min-events", type=int, default=4)
    args = parser.parse_args()
    try:
        return pi_demo(args) if args.role == "pi" else mac_demo(args)
    except (RuntimeError, OSError, sqlite3.Error) as exc:
        log("ERROR", str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

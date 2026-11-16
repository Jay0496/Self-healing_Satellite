"""Pi-side MQTT collector, deterministic ground-link blackout, durable resend.

Simulator -> local MQTT broker -> this collector -> local SQLite -> ground HTTP.
Use a LOCAL broker so simulated loss of GROUND never interrupts sensor ingress.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import paho.mqtt.client as mqtt

from flight.storage.buffer import TelemetryBuffer
from shared.protocol import TelemetryRecord
from simulator.link.blackout import BlackoutWindow


def main():
    parser = argparse.ArgumentParser(description='Onboard blackout/store-and-forward demonstration')
    parser.add_argument('--mqtt-host', default='127.0.0.1')
    parser.add_argument('--mqtt-port', type=int, default=1883)
    parser.add_argument('--ground-url', default='http://127.0.0.1:8765/ingest')
    parser.add_argument('--blackout-after', type=float, default=20.0)
    parser.add_argument('--blackout-duration', type=float, default=60.0)
    parser.add_argument('--db', type=Path, default=Path('flight/storage/telemetry_buffer.sqlite3'))
    args = parser.parse_args()
    window = BlackoutWindow(args.blackout_after, args.blackout_duration)
    db = TelemetryBuffer(args.db)
    start_lock = threading.Lock()
    mission_start = [None]  # Begin countdown when FIRST telemetry sample is actually received.
    state = 'WAITING_FOR_TELEMETRY'
    last_report = 0.0
    last_error = 0.0

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f'[FLIGHT] MQTT connection failed: {reason_code}', flush=True)
            return
        client.subscribe('satellite/flight/telemetry/#', qos=1)
        print('[FLIGHT] Subscribed to satellite/flight/telemetry/#', flush=True)

    def on_message(client, userdata, message):
        try:
            record = TelemetryRecord.from_json(message.payload)
            db.store(record)
            with start_lock:
                if mission_start[0] is None:
                    mission_start[0] = time.monotonic()
                    print('[FLIGHT] First sample received; blackout timer started', flush=True)
        except Exception as exc:
            print(f'[FLIGHT] Rejected telemetry: {exc}', flush=True)

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                         client_id='satellite-blackout-collector')
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(args.mqtt_host, args.mqtt_port, 30)
    except OSError as exc:
        print(f'[FLIGHT] Cannot connect to local MQTT broker: {exc}')
        db.close()
        return 2
    client.loop_start()
    print(f'[FLIGHT] Ground={args.ground_url} (outage starts {args.blackout_after}s '
          f'after first sample, lasts {args.blackout_duration}s)', flush=True)

    try:
        while True:
            time.sleep(0.5)
            now = time.monotonic()
            with start_lock:
                started = mission_start[0]
            if started is None:
                continue
            elapsed = now - started
            if window.offline(elapsed):
                next_state = 'BLACKOUT'
            elif state in ('BLACKOUT', 'RECOVERING') and db.counts()[1] > 0:
                next_state = 'RECOVERING'
            else:
                next_state = 'ONLINE'
            if next_state != state:
                state = next_state
                message = ('Simulated ground link unavailable' if state == 'BLACKOUT'
                           else 'Ground link restored; flushing buffered records' if state == 'RECOVERING'
                           else 'Ground link available')
                db.event(state, message)
                print(f'[FLIGHT] STATE={state} t={elapsed:.1f}s | {message}', flush=True)
            if state != 'BLACKOUT':
                batch = db.pending(100)
                if batch:
                    payload = json.dumps({'records': batch}).encode('utf-8')
                    req = urllib.request.Request(
                        args.ground_url, payload, {'Content-Type': 'application/json'}, method='POST')
                    try:
                        with urllib.request.urlopen(req, timeout=2.5) as response:
                            acknowledged = json.load(response).get('acknowledged_ids', [])
                        expected = {row['sample_id'] for row in batch}
                        if not isinstance(acknowledged, list) or not all(
                            isinstance(i, str) and i in expected for i in acknowledged
                        ):
                            raise ValueError('Invalid acknowledgement from ground')
                        db.acknowledge(acknowledged)
                    except (OSError, ValueError, urllib.error.HTTPError) as exc:
                        if now - last_error > 5:
                            last_error = now
                            print(f'[FLIGHT] Ground unavailable; buffering and retrying: {exc}', flush=True)
            if now - last_report >= 5:
                last_report = now
                total, pending = db.counts()
                print(f'[FLIGHT] t={elapsed:5.1f}s state={state:10s} '
                      f'saved={total} pending={pending} delivered={total-pending}', flush=True)
    except KeyboardInterrupt:
        print('\n[FLIGHT] Stopping collector', flush=True)
    finally:
        client.disconnect()
        client.loop_stop()
        db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

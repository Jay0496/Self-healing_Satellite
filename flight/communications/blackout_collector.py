"""Pi-side MQTT collector with deterministic blackout and telemetry/event recovery.

Simulator -> local MQTT broker -> collector -> local SQLite -> ground HTTP.
Ground blackout does not interrupt local MQTT, storage, or Pi remote access.
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
    events_url = args.ground_url.rsplit('/', 1)[0] + '/ingest-events'
    window = BlackoutWindow(args.blackout_after, args.blackout_duration)
    db = TelemetryBuffer(args.db)
    start_lock = threading.Lock()
    mission_start = [None]
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

    def submit_batch(url: str, key: str, rows: list[dict],
                     ack_key: str, id_key: str) -> list[str]:
        payload = json.dumps({key: rows}).encode('utf-8')
        req = urllib.request.Request(url, payload,
                                     {'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=2.5) as response:
            body = json.load(response)
        acknowledged = body.get(ack_key, [])
        expected = {row[id_key] for row in rows}
        if not isinstance(acknowledged, list) or not all(
            isinstance(uid, str) and uid in expected for uid in acknowledged
        ):
            raise ValueError('Invalid acknowledgement from ground')
        return acknowledged

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
    print(f'[FLIGHT] Ground={args.ground_url}; events={events_url} '
          f'(outage starts {args.blackout_after}s after first sample, '
          f'lasts {args.blackout_duration}s)', flush=True)

    try:
        while True:
            time.sleep(0.5)
            now = time.monotonic()
            with start_lock:
                started = mission_start[0]
            if started is None:
                continue
            elapsed = now - started
            telemetry_pending = db.counts()[1]
            event_pending = db.event_counts()[1]
            if window.offline(elapsed):
                next_state = 'BLACKOUT'
            elif state in ('BLACKOUT', 'RECOVERING') and (telemetry_pending or event_pending):
                next_state = 'RECOVERING'
            else:
                next_state = 'ONLINE'
            if next_state != state:
                state = next_state
                message = ('Simulated ground link unavailable' if state == 'BLACKOUT'
                           else 'Ground link restored; flushing buffered records and events' if state == 'RECOVERING'
                           else 'Ground link available')
                db.event(state, message)
                print(f'[FLIGHT] STATE={state} t={elapsed:.1f}s | {message}', flush=True)

            if state != 'BLACKOUT':
                try:
                    telemetry = db.pending(100)
                    if telemetry:
                        ack = submit_batch(args.ground_url, 'records', telemetry,
                                           'acknowledged_ids', 'sample_id')
                        db.acknowledge(ack)
                    events = db.pending_events(100)
                    if events:
                        ack = submit_batch(events_url, 'events', events,
                                           'acknowledged_event_uids', 'event_uid')
                        db.acknowledge_events(ack)
                except (OSError, ValueError, urllib.error.HTTPError) as exc:
                    if now - last_error > 5:
                        last_error = now
                        print(f'[FLIGHT] Ground unavailable; storing and retrying: {exc}', flush=True)

            if now - last_report >= 5:
                last_report = now
                total, pending = db.counts()
                event_total, pending_events = db.event_counts()
                print(f'[FLIGHT] t={elapsed:5.1f}s state={state:10s} '
                      f'saved={total} pending={pending} delivered={total-pending} '
                      f'events={event_total} events_pending={pending_events}', flush=True)
    except KeyboardInterrupt:
        print('\n[FLIGHT] Stopping collector', flush=True)
    finally:
        client.disconnect()
        client.loop_stop()
        db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

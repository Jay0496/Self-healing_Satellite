"""Pi-only MQTT store-and-forward flight demo. Existing HTTP demo is unchanged.

Simulator -> LOCAL Mosquitto -> SQLite -> MQTT downlink -> Mac ground.
Mac sends application ACKs and read-only commands via MQTT to the same broker.
Simulated blackout gates the ground-facing topics, not the local MQTT broker.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

import paho.mqtt.client as mqtt

from flight.storage.buffer import TelemetryBuffer
from shared.mqtt_protocol import topics, utc_now, validate_command
from shared.protocol import TelemetryRecord
from simulator.link.blackout import BlackoutWindow


def latest_events(db_path: Path, count: int = 10) -> list[dict]:
    # Separate read-only connection so existing SQLite buffer remains unmodified.
    with sqlite3.connect(f'file:{db_path.resolve()}?mode=ro', uri=True) as db:
        rows = db.execute(
            'SELECT event_time, state, message FROM events ORDER BY event_id DESC LIMIT ?',
            (count,),
        ).fetchall()
    return [dict(zip(('event_time', 'state', 'message'), item)) for item in reversed(rows)]


def main() -> int:
    p = argparse.ArgumentParser(description='MQTT satellite blackout/command demo on Raspberry Pi')
    p.add_argument('--mqtt-host', default='127.0.0.1')
    p.add_argument('--mqtt-port', type=int, default=1883)
    p.add_argument('--topic-prefix', default='capstone/g29/pi01')
    p.add_argument('--blackout-after', type=float, default=10)
    p.add_argument('--blackout-duration', type=float, default=15)
    p.add_argument('--db', type=Path, default=Path('flight/storage/pi_mqtt_flight.sqlite3'))
    args = p.parse_args()
    tm = topics(args.topic_prefix)
    window = BlackoutWindow(args.blackout_after, args.blackout_duration)
    db = TelemetryBuffer(args.db)
    lock = threading.RLock()
    shared = {'first': None, 'state': 'WAITING_FOR_TELEMETRY', 'connected': False}
    inflight = {'telemetry': None, 'events': None}
    seen_commands: dict[str, dict] = {}
    last_report = 0.0

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f'g29-flight-{uuid4().hex[:8]}')

    def on_connect(c, userdata, flags, rc, properties):
        if rc.is_failure:
            print(f'[FLIGHT MQTT] Connection rejected: {rc}', flush=True)
            return
        with lock:
            shared['connected'] = True
        c.subscribe('satellite/flight/telemetry/#', qos=1)
        c.subscribe(tm['ack'], qos=1)
        c.subscribe(tm['command'], qos=1)
        print('[FLIGHT MQTT] Connected; subscribed to local telemetry, ACKs and ground commands', flush=True)

    def on_disconnect(c, userdata, disconnect_flags, rc, properties):
        with lock:
            shared['connected'] = False
        print(f'[FLIGHT MQTT] Broker disconnected: {rc}; local SQLite data preserved', flush=True)

    def on_message(c, userdata, msg):
        if len(msg.payload) > 2_000_000:
            return
        if msg.topic.startswith('satellite/flight/telemetry/'):
            try:
                sample = TelemetryRecord.from_json(msg.payload)
                db.store(sample)
                with lock:
                    if shared['first'] is None:
                        shared['first'] = time.monotonic()
                        print('[FLIGHT MQTT] First sample; blackout timer started', flush=True)
            except (ValueError, KeyError, TypeError) as exc:
                print(f'[FLIGHT MQTT] Invalid telemetry: {exc}', flush=True)
            return
        try:
            body = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return
        with lock:
            # The blackout is a logical ground-radio blackout; nothing uplinked is processed.
            if shared['state'] not in ('ONLINE', 'RECOVERING'):
                return
            if msg.topic == tm['ack']:
                if not isinstance(body, dict):
                    return
                kind, ids = body.get('kind'), body.get('ids')
                if kind not in inflight or not isinstance(ids, list):
                    return
                pending = inflight[kind]
                if pending is None or not all(isinstance(x, str) for x in ids):
                    return
                accepted = list(set(ids) & set(pending['ids']))
                if kind == 'telemetry':
                    db.acknowledge(accepted)
                else:
                    db.acknowledge_events(accepted)
                inflight[kind] = None
                return
            if msg.topic == tm['command']:
                command_id = body.get('command_id') if isinstance(body, dict) else None
                try:
                    cmd = validate_command(body)
                    if cmd['command_id'] in seen_commands:
                        result = seen_commands[cmd['command_id']]
                    else:
                        total, waiting = db.counts()
                        events, event_waiting = db.event_counts()
                        if cmd['type'] == 'GET_STATUS':
                            details = {
                                'flight_state': shared['state'],
                                'telemetry_stored': total, 'telemetry_pending': waiting,
                                'events_stored': events, 'events_pending': event_waiting,
                            }
                        else:
                            details = {'events': latest_events(args.db, 10)}
                        result = {'command_id': cmd['command_id'], 'type': cmd['type'],
                                  'status': 'SUCCESS', 'result': details, 'handled_at': utc_now()}
                        seen_commands[cmd['command_id']] = result
                        # Bounded in-memory duplicate cache; read-only commands are safe to repeat.
                        if len(seen_commands) > 200:
                            seen_commands.pop(next(iter(seen_commands)))
                except (ValueError, TypeError) as exc:
                    result = {'command_id': command_id, 'status': 'REJECTED',
                              'reason': str(exc), 'handled_at': utc_now()}
                c.publish(tm['result'], json.dumps(result), qos=1, retain=False)
                print(f"[FLIGHT MQTT] Command {result.get('command_id')}: {result['status']}", flush=True)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    try:
        client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
    except OSError as exc:
        print(f'[FLIGHT MQTT] Cannot reach local broker: {exc}', flush=True)
        db.close()
        return 2
    client.loop_start()
    print(f'[FLIGHT MQTT] Ground downlink topics={args.topic_prefix}; '
          f'blackout after {args.blackout_after}s for {args.blackout_duration}s', flush=True)

    try:
        while True:
            time.sleep(0.5)
            now = time.monotonic()
            with lock:
                first = shared['first']
                if first is None:
                    continue
                elapsed = now - first
                state = shared['state']
                waiting = db.counts()[1] + db.event_counts()[1]
                if window.offline(elapsed):
                    next_state = 'BLACKOUT'
                elif state in ('BLACKOUT', 'RECOVERING') and waiting:
                    next_state = 'RECOVERING'
                else:
                    next_state = 'ONLINE'
                if next_state != state:
                    shared['state'] = next_state
                    note = ('Simulated ground link unavailable' if next_state == 'BLACKOUT'
                            else 'Ground link restored; flushing buffered records and events'
                            if next_state == 'RECOVERING' else 'Ground link available')
                    db.event(next_state, note)
                    print(f'[FLIGHT MQTT] STATE={next_state} t={elapsed:.1f}s | {note}', flush=True)
                    if next_state == 'BLACKOUT':
                        inflight['telemetry'] = None
                        inflight['events'] = None
                if shared['connected'] and shared['state'] != 'BLACKOUT':
                    # Publish event/state logs first; ground ACK means SQLite commit succeeded.
                    for kind, read in [('events', db.pending_events), ('telemetry', db.pending)]:
                        flight = inflight[kind]
                        if flight is None or now - flight['at'] >= 3.0:
                            rows = read(100)
                            if not rows:
                                inflight[kind] = None
                                continue
                            key = 'event_uid' if kind == 'events' else 'sample_id'
                            ids = [row[key] for row in rows]
                            payload = json.dumps({'items': rows}, separators=(',', ':'))
                            inflight[kind] = {'at': now, 'ids': ids}
                            client.publish(tm[kind], payload, qos=1, retain=False)
                if now - last_report >= 5:
                    last_report = now
                    t_total, t_pending = db.counts()
                    e_total, e_pending = db.event_counts()
                    print(f'[FLIGHT MQTT] t={elapsed:5.1f}s state={shared["state"]:10s} '
                          f'saved={t_total} pending={t_pending} '
                          f'events={e_total} events_pending={e_pending}', flush=True)
    except KeyboardInterrupt:
        print('\n[FLIGHT MQTT] Stopping', flush=True)
    finally:
        client.disconnect()
        client.loop_stop()
        db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Pi-only MQTT store-and-forward flight demo. Existing HTTP demo is unchanged.

Simulator -> LOCAL Mosquitto -> SQLite -> MQTT downlink -> Mac ground.
Mac sends application ACKs and read-only commands via MQTT to the same broker.
Simulated blackout gates the ground-facing topics, not the local MQTT broker.

Security (REQ-SEC-01/02): every ground-facing packet (telemetry, events, command results
downlinked; ACKs and commands uplinked) is an AES-256-GCM sealed envelope bound to its
topic, with replay rejection. Uplink commands must also carry a valid Ed25519 ground
signature. See docs/SECURITY.md.

Audit (REQ-AUD-01): link-state changes, commands, security rejections, key rotations,
resource samples and AI anomaly decisions are appended to a SHA-256 hash chain in the same
SQLite file and downlinked like telemetry. See docs/AUDIT_AND_MONITORING.md.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

import paho.mqtt.client as mqtt

from flight.monitoring.resource_monitor import ResourceMonitor
from flight.storage.buffer import TelemetryBuffer
from shared.audit import AuditLog, RateLimitedAudit
from shared.mqtt_protocol import topics, utc_now, validate_command
from shared.protocol import TelemetryRecord
from shared.security import KeyMaterialError, SecurityError, open_channel
from shared.security.keystore import load_verify_key
from shared.security.signing import verify_document
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
    p.add_argument('--secrets', default=None, help='Key directory (default $G29_SECRETS_DIR or ~/.g29_secrets)')
    p.add_argument('--resource-interval', type=float, default=30.0,
                   help='Seconds between audited resource samples (REQ-HW-01/02/03)')
    args = p.parse_args()
    # SIGTERM (e.g. from scripts/demo.py) shuts down cleanly so AGENT_STOP is audited.
    signal.signal(signal.SIGTERM, _interrupt)

    # Hash-chained audit log shares the flight SQLite file with the telemetry buffer and the
    # AI process, and is downlinked through the same store-and-forward path (REQ-AUD-01).
    audit = AuditLog(args.db, chain_prefix='pi01', actor='flight-agent')
    security_audit = RateLimitedAudit(audit, per_minute=20)

    def on_key_epoch(old: int, new: int) -> None:
        print(f'[FLIGHT MQTT] KEY_ROTATION link key epoch {old} -> {new}', flush=True)
        audit.append('KEY_ROTATION', {'old_epoch': old, 'new_epoch': new})

    try:
        channel = open_channel('pi01-flight', secrets=args.secrets, on_key_epoch=on_key_epoch)
        verify_key = load_verify_key(args.secrets)
    except KeyMaterialError as exc:
        print(f'[FLIGHT MQTT] SECURITY key material unavailable: {exc}', flush=True)
        audit.append('SECURITY_CONFIG_ERROR', {'error': str(exc)[:500]})
        audit.close()
        return 2
    print(f'[FLIGHT MQTT] SECURITY AES-256-GCM link enabled; key epoch {channel.current_epoch} '
          f'rotates every {channel.rotation_seconds}s; commands require Ed25519 signature', flush=True)
    tm = topics(args.topic_prefix)
    window = BlackoutWindow(args.blackout_after, args.blackout_duration)
    db = TelemetryBuffer(args.db)
    monitor = ResourceMonitor()
    lock = threading.RLock()
    shared = {'first': None, 'state': 'WAITING_FOR_TELEMETRY', 'connected': False,
              'resources': None, 'breaches': []}
    inflight = {'telemetry': None, 'events': None, 'audit': None}
    seen_commands: dict[str, dict] = {}
    last_report = 0.0
    last_resource = 0.0
    audit.append('AGENT_START', {'pid': os.getpid(), 'chain_id': audit.chain_id,
                                 'key_epoch': channel.current_epoch,
                                 'rotation_seconds': channel.rotation_seconds,
                                 'blackout_after_s': args.blackout_after,
                                 'blackout_duration_s': args.blackout_duration})
    print(f'[FLIGHT MQTT] AUDIT hash chain {audit.chain_id} in {args.db}', flush=True)

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
        with lock:
            # The blackout is a logical ground-radio blackout; nothing uplinked is processed.
            if shared['state'] not in ('ONLINE', 'RECOVERING'):
                return
        try:
            body = channel.open_json(msg.topic, msg.payload)
        except SecurityError as exc:
            print(f'[FLIGHT MQTT] SECURITY REJECT topic={msg.topic} reason={exc.reason} | {exc}', flush=True)
            security_audit.append('SECURITY_REJECT', {'topic': msg.topic, 'reason': exc.reason,
                                                      'detail': str(exc)[:200]})
            return
        with lock:
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
                elif kind == 'audit':
                    audit.acknowledge(accepted)
                else:
                    db.acknowledge_events(accepted)
                inflight[kind] = None
                return
            if msg.topic == tm['command']:
                command_id = body.get('command_id') if isinstance(body, dict) else None
                try:
                    # Signature first: unsigned or forged commands never reach command handling.
                    cmd = validate_command(verify_document(body, verify_key))
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
                                'security': channel.stats(),
                                'resources': shared['resources'],
                                'audit': audit_status(audit),
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
                audit.append('COMMAND', {
                    'command_id': result.get('command_id'), 'type': result.get('type'),
                    'status': result['status'], 'reason': result.get('reason'),
                    'issued_by': body.get('issued_by') if isinstance(body, dict) else None})
                c.publish(tm['result'], channel.seal_json(tm['result'], result), qos=1, retain=False)
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
            if now - last_resource >= args.resource_interval:
                last_resource = now
                sample_resources(monitor, audit, shared, lock)
            with lock:
                first = shared['first']
                if first is None:
                    continue
                elapsed = now - first
                state = shared['state']
                waiting = db.counts()[1] + db.event_counts()[1] + audit.counts()[1]
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
                    audit.append('LINK_STATE', {'from': state, 'to': next_state, 'note': note,
                                                'elapsed_s': round(elapsed, 1),
                                                'telemetry_pending': db.counts()[1]})
                    print(f'[FLIGHT MQTT] STATE={next_state} t={elapsed:.1f}s | {note}', flush=True)
                    if next_state == 'BLACKOUT':
                        for kind in inflight:
                            inflight[kind] = None
                if shared['connected'] and shared['state'] != 'BLACKOUT':
                    # Critical records first (REQ-COM-02): link events, then the audit trail,
                    # then routine telemetry. Ground ACK means its SQLite commit succeeded.
                    for kind, read in [('events', db.pending_events), ('audit', audit.pending),
                                       ('telemetry', db.pending)]:
                        flight = inflight[kind]
                        if flight is None or now - flight['at'] >= 3.0:
                            rows = read(100)
                            if not rows:
                                inflight[kind] = None
                                continue
                            key = {'events': 'event_uid', 'audit': 'entry_uid'}.get(kind, 'sample_id')
                            ids = [row[key] for row in rows]
                            payload = json.dumps({'items': rows}, separators=(',', ':'))
                            inflight[kind] = {'at': now, 'ids': ids}
                            client.publish(tm[kind], channel.seal(tm[kind], payload), qos=1, retain=False)
                if now - last_report >= 5:
                    last_report = now
                    t_total, t_pending = db.counts()
                    e_total, e_pending = db.event_counts()
                    a_total, a_pending = audit.counts()
                    rejected = sum(channel.rejected.values())
                    print(f'[FLIGHT MQTT] t={elapsed:5.1f}s state={shared["state"]:10s} '
                          f'saved={t_total} pending={t_pending} '
                          f'events={e_total} events_pending={e_pending} '
                          f'audit={a_total} audit_pending={a_pending} '
                          f'sec_rejected={rejected}', flush=True)
    except KeyboardInterrupt:
        print('\n[FLIGHT MQTT] Stopping', flush=True)
    finally:
        security_audit.flush()
        audit.append('AGENT_STOP', {'security': channel.stats(), 'telemetry_pending': db.counts()[1]})
        result = audit.verify()
        print(f"[FLIGHT MQTT] AUDIT chain {audit.chain_id}: {result['checked']} entries, "
              f"{'INTACT' if result['ok'] else 'BROKEN at seq ' + str(result['first_bad_seq'])}", flush=True)
        client.disconnect()
        client.loop_stop()
        db.close()
        audit.close()
    return 0


def _interrupt(*_args) -> None:
    raise KeyboardInterrupt


def audit_status(audit: AuditLog) -> dict:
    total, pending = audit.counts()
    result = audit.verify()
    return {'chain_id': audit.chain_id, 'entries': total, 'pending': pending,
            'intact': result['ok'], 'head': result['head'][:16]}


def sample_resources(monitor: ResourceMonitor, audit: AuditLog, shared: dict, lock) -> None:
    """Audit a resource sample and any limit crossing (REQ-HW-01/02/03)."""
    sample = monitor.sample()
    with lock:
        previous = set(shared['breaches'])
        shared['resources'] = sample
        shared['breaches'] = sample['breaches']
    audit.append('RESOURCE_SAMPLE', {k: sample[k] for k in (
        'cpu_percent', 'agent_ram_mb', 'agent_processes', 'cpu_temp_c', 'power_w')})
    current = set(sample['breaches'])
    if current - previous:
        audit.append('RESOURCE_LIMIT_EXCEEDED', {'breaches': sorted(current - previous), 'sample': {
            k: sample[k] for k in ('cpu_percent', 'agent_ram_mb', 'cpu_temp_c', 'power_w')}})
        print(f"[FLIGHT MQTT] RESOURCE LIMIT EXCEEDED {sorted(current - previous)}", flush=True)
    if previous - current:
        audit.append('RESOURCE_LIMIT_CLEARED', {'cleared': sorted(previous - current)})
    temp = 'n/a' if sample['cpu_temp_c'] is None else f"{sample['cpu_temp_c']}C"
    power = 'n/a' if sample['power_w'] is None else f"{sample['power_w']}W"
    print(f"[FLIGHT MQTT] RESOURCES cpu={sample['cpu_percent']}% agent_ram={sample['agent_ram_mb']}MB "
          f"temp={temp} power={power} "
          f"breaches={sample['breaches'] or 'none'}", flush=True)


if __name__ == '__main__':
    raise SystemExit(main())

"""Mac ground MQTT receiver. Stores telemetry/events before returning application ACKs.

Downlink batches must be valid AES-256-GCM envelopes from the Pi (tampered, replayed,
unencrypted or wrong-topic packets are rejected and never stored or ACKed). ACKs are
sealed the same way. See docs/SECURITY.md.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from uuid import uuid4

import paho.mqtt.client as mqtt

from ground.ingestion.receiver import GroundStore
from shared.mqtt_protocol import topics
from shared.security import KeyMaterialError, SecurityError, open_channel


def main() -> int:
    p = argparse.ArgumentParser(description='Ground-side MQTT telemetry and event receiver')
    p.add_argument('--broker-host', required=True, help='Pi Tailscale IPv4 address')
    p.add_argument('--broker-port', type=int, default=1883)
    p.add_argument('--topic-prefix', default='capstone/g29/pi01')
    p.add_argument('--db', type=Path, default=Path('ground/ingestion/mac_mqtt_ground.sqlite3'))
    p.add_argument('--secrets', default=None, help='Key directory (default $G29_SECRETS_DIR or ~/.g29_secrets)')
    args = p.parse_args()
    tm = topics(args.topic_prefix)
    try:
        channel = open_channel('ground-station', secrets=args.secrets)
    except KeyMaterialError as exc:
        print(f'[GROUND MQTT] SECURITY key material unavailable: {exc}', flush=True)
        return 2
    store = GroundStore(args.db)
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f'g29-ground-{uuid4().hex[:8]}')
    ready = threading.Event()

    def on_connect(c, userdata, flags, rc, properties):
        if rc.is_failure:
            print(f'[GROUND MQTT] Connection rejected: {rc}', flush=True)
            return
        c.subscribe(tm['telemetry'], qos=1)
        c.subscribe(tm['events'], qos=1)
        ready.set()
        print(f'[GROUND MQTT] Subscribed to {args.topic_prefix}/downlink/#', flush=True)

    def on_disconnect(c, userdata, flags, rc, properties):
        ready.clear()
        print(f'[GROUND MQTT] Disconnected: {rc}; reconnecting', flush=True)

    def on_message(c, userdata, msg):
        if len(msg.payload) > 2_000_000:
            return
        try:
            payload = channel.open_json(msg.topic, msg.payload)
        except SecurityError as exc:
            print(f'[GROUND MQTT] SECURITY REJECT topic={msg.topic} reason={exc.reason} | {exc}', flush=True)
            return
        try:
            rows = payload.get('items') if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
                raise ValueError('Invalid downlink batch')
            if msg.topic == tm['telemetry']:
                acknowledged = store.ingest(rows)
                kind = 'telemetry'
            elif msg.topic == tm['events']:
                acknowledged = store.ingest_events(rows)
                kind = 'events'
            else:
                return
            # ACK after SQLite transaction has committed. Duplicates remain idempotent.
            c.publish(tm['ack'], channel.seal_json(tm['ack'], {'kind': kind, 'ids': acknowledged}),
                      qos=1, retain=False)
            print(f'[GROUND MQTT] Saved {kind} batch={len(rows)} '
                  f'telemetry_unique={store.count()} events_unique={store.count_events()}', flush=True)
        except (ValueError, KeyError, TypeError) as exc:
            print(f'[GROUND MQTT] Rejected batch: {exc}', flush=True)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    try:
        client.connect(args.broker_host, args.broker_port, 30)
    except OSError as exc:
        print(f'[GROUND MQTT] Unable to connect to Pi: {exc}', flush=True)
        store.db.close()
        return 2
    client.loop_start()
    print(f'[GROUND MQTT] Connecting to {args.broker_host}:{args.broker_port}; database={args.db}', flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n[GROUND MQTT] Stopping', flush=True)
    finally:
        print(f'[GROUND MQTT] SECURITY summary {json.dumps(channel.stats())}', flush=True)
        client.disconnect()
        client.loop_stop()
        store.db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

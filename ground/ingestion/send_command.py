"""Ground-side read-only MQTT telecommand CLI. No arbitrary code execution."""
from __future__ import annotations

import argparse
import json
import threading
import time
from uuid import uuid4

import paho.mqtt.client as mqtt

from shared.mqtt_protocol import ALLOWED_COMMANDS, topics


def main() -> int:
    p = argparse.ArgumentParser(description='Send a safe command from Mac to satellite Pi')
    p.add_argument('--broker-host', required=True, help='Pi Tailscale IPv4 address')
    p.add_argument('--broker-port', type=int, default=1883)
    p.add_argument('--topic-prefix', default='capstone/g29/pi01')
    p.add_argument('--command', choices=sorted(ALLOWED_COMMANDS), default='GET_STATUS')
    p.add_argument('--timeout', type=float, default=15)
    args = p.parse_args()
    tm = topics(args.topic_prefix)
    cid = uuid4().hex
    done = threading.Event()
    result = {}
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f'g29-cli-{uuid4().hex[:8]}')

    def on_connect(c, userdata, flags, rc, properties):
        if not rc.is_failure:
            # Subscribe and wait for SUBACK before publishing to avoid a fast response being missed.
            c.subscribe(tm['result'], qos=1)
        else:
            print(f'[GROUND COMMAND] Broker connection rejected: {rc}', flush=True)

    def on_subscribe(c, userdata, mid, reason_codes, properties):
        command = {'command_id': cid, 'type': args.command,
                   'expires_at': time.time() + min(args.timeout, 30)}
        c.publish(tm['command'], json.dumps(command), qos=1, retain=False)
        print(f'[GROUND COMMAND] Sent {args.command}, id={cid}', flush=True)

    def on_message(c, userdata, msg):
        try:
            data = json.loads(msg.payload)
            if isinstance(data, dict) and data.get('command_id') == cid:
                result.update(data)
                done.set()
        except (ValueError, UnicodeDecodeError):
            pass

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    try:
        client.connect(args.broker_host, args.broker_port, 30)
    except OSError as exc:
        print(f'[GROUND COMMAND] Cannot reach Pi: {exc}', flush=True)
        return 2
    client.loop_start()
    try:
        if not done.wait(args.timeout):
            print('[GROUND COMMAND] No response (blackout/offline or connection issue).', flush=True)
            return 1
        print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
        return 0 if result.get('status') == 'SUCCESS' else 1
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == '__main__':
    raise SystemExit(main())

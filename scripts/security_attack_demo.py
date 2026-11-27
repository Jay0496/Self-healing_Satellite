"""Live security check: attack a running Pi collector + ground receiver over MQTT.

Run while `scripts/demo.py pi` and `scripts/demo.py mac` (or the collector and
mqtt_ground) are running and the link is ONLINE:

    python scripts/security_attack_demo.py --broker-host 127.0.0.1

It captures a real downlink packet and then tries, as an on-path attacker:
  1. replaying it to ground            -> ground: SECURITY REJECT reason=REPLAY
  2. flipping one ciphertext bit       -> ground: reason=AUTH_FAILED
  3. injecting a plaintext batch       -> ground: reason=UNENCRYPTED
  4. injecting a plaintext command     -> Pi:     reason=UNENCRYPTED
  5. forging a plaintext ACK           -> Pi:     reason=UNENCRYPTED (buffer is NOT cleared)
  6. sending an encrypted but UNSIGNED command (insider holding only the link key)
                                       -> Pi replies REJECTED: UNSIGNED
  7. moving the captured downlink packet onto the uplink command topic
                                       -> Pi:     reason=AUTH_FAILED
The rejections are printed by the Pi and ground processes; this script prints what it sent
and verifies attack 6 from the Pi's authenticated reply.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.mqtt_protocol import topics  # noqa: E402
from shared.security import open_channel  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Attack a running link and confirm rejections")
    p.add_argument("--broker-host", default="127.0.0.1")
    p.add_argument("--broker-port", type=int, default=1883)
    p.add_argument("--topic-prefix", default="capstone/g29/pi01")
    args = p.parse_args()
    tm = topics(args.topic_prefix)
    insider = open_channel("attacker-with-link-key-only")
    captured: list[bytes] = []
    result: dict = {}
    got_capture, got_result = threading.Event(), threading.Event()
    command_id = f"attack-{uuid4().hex[:8]}"

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"g29-attacker-{uuid4().hex[:6]}")

    def on_connect(c, userdata, flags, rc, properties):
        c.subscribe(tm["telemetry"], qos=1)
        c.subscribe(tm["result"], qos=1)

    def on_message(c, userdata, msg):
        if msg.topic == tm["telemetry"] and not captured:
            captured.append(bytes(msg.payload))
            got_capture.set()
        elif msg.topic == tm["result"]:
            try:
                data = insider.open_json(msg.topic, msg.payload)
            except ValueError:
                return
            if isinstance(data, dict) and data.get("command_id") == command_id:
                result.update(data)
                got_result.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker_host, args.broker_port, 30)
    client.loop_start()
    try:
        print("[ATTACK] Waiting to capture a live downlink telemetry packet...", flush=True)
        if not got_capture.wait(60):
            print("[ATTACK] No downlink seen in 60 s (is the link ONLINE?)")
            return 2
        packet = captured[0]
        env = json.loads(packet)
        readable = b"sample_id" in packet or b"Battery" in packet
        print(f"[ATTACK] Captured {len(packet)} B, alg={env['alg']} kid={env['kid']} seq={env['seq']}; "
              f"telemetry readable on the wire: {readable}", flush=True)

        def send(label, topic, payload):
            client.publish(topic, payload, qos=1).wait_for_publish(5)
            print(f"[ATTACK] sent {label}", flush=True)
            time.sleep(0.3)

        send("1 replay of captured downlink", tm["telemetry"], packet)
        ct = bytearray(base64.b64decode(env["ct"]))
        ct[len(ct) // 2] ^= 0x01
        send("2 bit-flipped downlink", tm["telemetry"],
             json.dumps({**env, "ct": base64.b64encode(bytes(ct)).decode()}))
        send("3 plaintext forged telemetry batch", tm["telemetry"],
             json.dumps({"items": [{"sample_id": "forged:1"}]}))
        send("4 plaintext GET_STATUS command", tm["command"],
             json.dumps({"command_id": "plain", "type": "GET_STATUS", "expires_at": time.time() + 20}))
        send("5 plaintext forged ACK", tm["ack"], json.dumps({"kind": "telemetry", "ids": ["x"]}))
        unsigned = {"command_id": command_id, "type": "GET_STATUS", "expires_at": time.time() + 20}
        send("6 encrypted but unsigned command", tm["command"], insider.seal_json(tm["command"], unsigned))
        send("7 downlink packet re-labelled as uplink command", tm["command"], packet)

        if got_result.wait(10):
            ok = result.get("status") == "REJECTED" and "UNSIGNED" in str(result.get("reason"))
            print(f"[ATTACK] Pi reply to unsigned command: status={result.get('status')} "
                  f"reason={result.get('reason')} -> {'PASS' if ok else 'FAIL'}", flush=True)
        else:
            ok = False
            print("[ATTACK] No reply to attack 6 (Pi in BLACKOUT?) -> FAIL", flush=True)
        print("[ATTACK] Done. Check Pi/ground output for SECURITY REJECT lines (expected 7).", flush=True)
        return 0 if ok and not readable else 1
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    raise SystemExit(main())

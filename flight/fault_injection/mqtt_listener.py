"""Receive supported physical fault requests on the Raspberry Pi."""

import argparse
import hashlib
import hmac
import json
import math
import os
import time
import uuid

from flight.fault_injection.injector import FAULTS, inject_fault

TOPIC = "satellite/flight/hardware-fault/request"


class HardwareFaultReceiver:

    def __init__(self, secret, injector=inject_fault):

        if not secret or len(secret) < 32:
            raise ValueError("Missing or invalid hardware fault key")

        self.secret = secret
        self.injector = injector
        self.processed = {}

    def handle(self, payload, retained=False):

        if retained:
            raise ValueError("Retained fault requests are not allowed")

        if len(payload) > 4096:
            raise ValueError("Request too large")

        message = json.loads(payload)

        if not isinstance(message, dict):
            raise ValueError("Invalid message")

        if set(message) != {"request", "signature"}:
            raise ValueError("Invalid message fields")

        request = message["request"]
        signature = message["signature"]

        if not isinstance(request, dict):
            raise ValueError("Invalid request")

        if not isinstance(signature, str):
            raise ValueError("Invalid signature")

        canonical = json.dumps(
            request,
            sort_keys=True,
            separators=(",", ":"),
        )

        expected = hmac.new(
            self.secret.encode(),
            canonical.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid request signature")

        if set(request) != {
            "command_id", "sensor", "fault", "expires_at"
        }:
            raise ValueError("Invalid request fields")

        command_id = request["command_id"]
        sensor = request["sensor"]
        fault = request["fault"]
        expires = request["expires_at"]

        if not isinstance(command_id, str):
            raise ValueError("Invalid command ID")

        if str(uuid.UUID(command_id)) != command_id:
            raise ValueError("Invalid command ID")

        if not isinstance(sensor, str) or sensor not in FAULTS:
            raise ValueError("Unsupported sensor")

        if not isinstance(fault, str) or fault not in FAULTS[sensor]:
            raise ValueError("Unsupported fault")

        now = time.time()

        if type(expires) not in (int, float):
            raise ValueError("Invalid expiry")

        if not math.isfinite(expires) or not now < expires <= now + 60:
            raise ValueError("Expired or invalid request")

        self.processed = {
            cid: expiry
            for cid, expiry in self.processed.items()
            if expiry > now
        }

        if command_id in self.processed:
            print("[IGNORED] Duplicate fault request")
            return None

        self.processed[command_id] = expires

        print(f"[HARDWARE REQUEST RECEIVED] {sensor}/{fault}")

        return self.injector(sensor, fault)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)

    parser.add_argument(
        "--enable-hardware-faults",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.enable_hardware_faults:
        parser.error("Explicit hardware fault enablement required")

    secret = os.environ.get("CAPSTONE_FAULT_KEY")

    receiver = HardwareFaultReceiver(secret)

    import paho.mqtt.client as mqtt

    def on_connect(client, userdata, flags, reason_code, properties):

        if reason_code == 0:
            client.subscribe(TOPIC, qos=1)
        else:
            print(f"[ERROR] MQTT connection: {reason_code}")

    def on_subscribe(client, userdata, mid, reason_codes, properties):

        if any(code.is_failure for code in reason_codes):
            print("[ERROR] Subscription failed")
        else:
            print("[READY] Hardware fault receiver subscribed")

    def on_message(client, userdata, message):

        try:
            result = receiver.handle(
                message.payload,
                retained=message.retain,
            )

            if result is not None:
                print("[SUCCESS] Physical fault injected")

        except Exception as error:
            print(f"[REJECTED] {error}")

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"pi-hardware-fault-{uuid.uuid4().hex[:8]}",
    )

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    client.connect(args.mqtt_host, args.mqtt_port, 30)

    try:
        client.loop_forever()

    except KeyboardInterrupt:
        print("\nHardware fault receiver stopped.")

    finally:
        client.disconnect()


if __name__ == "__main__":
    main()

"""MQTT publisher for simulator telemetry, restricted to the flight topic tree."""

from __future__ import annotations

import time
import json
import uuid
import os
import hmac
import hashlib
from dataclasses import dataclass

from shared.protocol import TelemetryRecord


class MqttPublishError(RuntimeError):
    """Raised when telemetry cannot be accepted by the MQTT client/broker."""


def validate_flight_topic_prefix(prefix: str) -> str:
    cleaned = prefix.strip("/")
    segments = cleaned.split("/")
    if not cleaned or any(not segment for segment in segments):
        raise ValueError("MQTT topic prefix must not be empty or contain empty segments")
    if "+" in cleaned or "#" in cleaned:
        raise ValueError("MQTT publish topic prefix cannot contain wildcards")
    if "flight" not in segments:
        raise ValueError("simulator MQTT topic prefix must be within the flight topic tree")
    if any(segment.lower() == "ground" for segment in segments):
        raise ValueError("simulator cannot publish to a ground topic")
    return cleaned


@dataclass(frozen=True, slots=True)
class MqttConfig:
    host: str = "localhost"
    port: int = 1883
    keepalive_seconds: int = 30
    topic_prefix: str = "satellite/flight/telemetry"
    qos: int = 1
    client_id: str = "satellite-simulator"
    username: str | None = None
    password: str | None = None
    connect_timeout_seconds: float = 10.0
    publish_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        validate_flight_topic_prefix(self.topic_prefix)
        if not self.host.strip():
            raise ValueError("MQTT host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("MQTT port must be in [1, 65535]")
        if self.qos not in (0, 1, 2):
            raise ValueError("MQTT QoS must be 0, 1, or 2")
        if not self.client_id.strip():
            raise ValueError("MQTT client_id must not be empty")
        if self.connect_timeout_seconds <= 0 or self.publish_timeout_seconds <= 0:
            raise ValueError("MQTT timeouts must be positive")


class MqttTelemetryPublisher:
    """Serialize and publish one shared telemetry record per MQTT message."""

    def __init__(self, client: object, config: MqttConfig | None = None) -> None:
        self.client = client
        self.config = config or MqttConfig()
        self.topic_prefix = validate_flight_topic_prefix(self.config.topic_prefix)

    def connect(self) -> None:
        result = self.client.connect(
            self.config.host,
            self.config.port,
            self.config.keepalive_seconds,
        )
        if isinstance(result, int) and result != 0:
            raise MqttPublishError(f"MQTT connect returned error code {result}")
        loop_start = getattr(self.client, "loop_start", None)
        if loop_start is not None:
            loop_start()
        is_connected = getattr(self.client, "is_connected", None)
        if is_connected is not None:
            deadline = time.monotonic() + self.config.connect_timeout_seconds
            while not is_connected() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not is_connected():
                self.close()
                raise MqttPublishError(
                    f"MQTT connection to {self.config.host}:{self.config.port} timed out"
                )

    def topic_for(self, record: TelemetryRecord) -> str:
        record.validate()
        return f"{self.topic_prefix}/{record.sensor_id}/{record.signal}"

    def publish(self, record: TelemetryRecord) -> None:
        topic = self.topic_for(record)
        info = self.client.publish(
            topic,
            payload=record.to_json(),
            qos=self.config.qos,
            retain=False,
        )
        result_code = getattr(info, "rc", 0)
        if result_code != 0:
            raise MqttPublishError(f"MQTT publish to {topic} returned error code {result_code}")
        wait_for_publish = getattr(info, "wait_for_publish", None)
        if wait_for_publish is not None:
            wait_for_publish(timeout=self.config.publish_timeout_seconds)
            is_published = getattr(info, "is_published", None)
            if is_published is not None and not is_published():
                raise MqttPublishError(f"MQTT publish to {topic} timed out")

    def publish_hardware_fault(self, sensor: str, fault: str) -> str:
        """Send an authenticated physical sensor fault request."""

        from .hardware_faults import validate_hardware_fault

        if not validate_hardware_fault(sensor, fault):
            raise ValueError("Unsupported hardware fault")

        secret = os.environ.get("CAPSTONE_FAULT_KEY")

        if not secret or len(secret) < 32:
            raise RuntimeError("Hardware fault key is missing or too short")

        request = {
            "command_id": str(uuid.uuid4()),
            "sensor": sensor,
            "fault": fault,
            "expires_at": time.time() + 30,
        }

        payload = json.dumps(
            request, sort_keys=True, separators=(",", ":")
        )

        signature = hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        message = json.dumps({
            "request": request,
            "signature": signature,
        })

        info = self.client.publish(
            "satellite/flight/hardware-fault/request",
            payload=message,
            qos=1,
            retain=False,
        )

        if info.rc != 0:
            raise MqttPublishError("Hardware fault publication failed")

        info.wait_for_publish(
            timeout=self.config.publish_timeout_seconds
        )

        if not info.is_published():
            raise MqttPublishError("Hardware fault publication timed out")

        print(f"[HARDWARE FAULT REQUEST] {sensor}/{fault}")

        return request["command_id"]


    def close(self) -> None:
        disconnect = getattr(self.client, "disconnect", None)
        if disconnect is not None:
            disconnect()
        loop_stop = getattr(self.client, "loop_stop", None)
        if loop_stop is not None:
            loop_stop()


def create_paho_publisher(config: MqttConfig | None = None) -> MqttTelemetryPublisher:
    """Create the production publisher, importing Paho only when requested."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise RuntimeError(
            "Paho MQTT is required for streaming. Install simulator/requirements.txt."
        ) from exc

    selected = config or MqttConfig()
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=selected.client_id,
    )
    if selected.username:
        client.username_pw_set(selected.username, selected.password)
    return MqttTelemetryPublisher(client, selected)

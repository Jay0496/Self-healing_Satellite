import json

import pytest

from simulator.generation import TelemetryGenerator
from simulator.mqtt import MqttConfig, MqttPublishError, MqttTelemetryPublisher


class FakePublishInfo:
    rc = 0

    def wait_for_publish(self, timeout=None):
        self.timeout = timeout

    def is_published(self):
        return True


class FakeMqttClient:
    def __init__(self):
        self.messages = []
        self.connected = None
        self.loop_started = False
        self.disconnected = False
        self.loop_stopped = False

    def connect(self, host, port, keepalive):
        self.connected = (host, port, keepalive)
        return 0

    def loop_start(self):
        self.loop_started = True

    def publish(self, topic, payload, qos, retain):
        self.messages.append((topic, payload, qos, retain))
        return FakePublishInfo()

    def disconnect(self):
        self.disconnected = True

    def loop_stop(self):
        self.loop_stopped = True


def test_publisher_emits_one_json_record_per_sensor_signal_to_flight(sample_uhf_csv):
    client = FakeMqttClient()
    config = MqttConfig(topic_prefix="satellite/flight/telemetry", qos=1)
    publisher = MqttTelemetryPublisher(client, config)
    frame = TelemetryGenerator(sample_uhf_csv).next_frame()

    publisher.connect()
    for record in frame.records:
        publisher.publish(record)
    publisher.close()

    assert client.connected == ("localhost", 1883, 30)
    assert client.loop_started and client.disconnected and client.loop_stopped
    assert len(client.messages) == len(frame.records)
    topic, payload, qos, retain = client.messages[0]
    assert topic == "satellite/flight/telemetry/bmp0/temperature_c"
    assert json.loads(payload)["signal"] == "temperature_c"
    assert qos == 1
    assert retain is False
    assert all("/ground/" not in message[0] for message in client.messages)


@pytest.mark.parametrize(
    "prefix",
    ["", "satellite/ground/telemetry", "satellite/telemetry", "satellite/flight/#"],
)
def test_publisher_rejects_non_flight_topics(prefix):
    with pytest.raises(ValueError):
        MqttConfig(topic_prefix=prefix)


def test_publish_failure_is_reported(sample_uhf_csv):
    class FailedClient(FakeMqttClient):
        def publish(self, topic, payload, qos, retain):
            info = FakePublishInfo()
            info.rc = 4
            return info

    publisher = MqttTelemetryPublisher(FailedClient())
    record = TelemetryGenerator(sample_uhf_csv).next_frame().records[0]
    with pytest.raises(MqttPublishError, match="error code 4"):
        publisher.publish(record)


@pytest.fixture
def sample_uhf_csv(tmp_path):
    path = tmp_path / "uhf.csv"
    path.write_text(
        "Timestamp,NanoMind_Temp,Battery_Voltage,X_Coarse_Spin,Y_Coarse_Spin,Z_Coarse_Spin\n"
        "2024-05-01T00:00:00Z,220,7000,-0.1,0.0,0.1\n"
        "2024-05-01T00:00:10Z,230,7100,-0.09,0.01,0.11\n",
        encoding="utf-8",
    )
    return path

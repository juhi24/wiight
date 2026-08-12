import json
from datetime import UTC, datetime
from typing import Any

from wiight.config import MqttConfig
from wiight.measurement import StableMeasurement
from wiight.mqtt import (
    MqttError,
    MqttPublisher,
    PairCommand,
    TareCommand,
    availability_message,
    discovery_message,
    pair_command_topic,
    pairing_status_message,
    status_message,
    tare_command_topic,
    tare_status_message,
    weight_message,
)


def mqtt_config() -> MqttConfig:
    return MqttConfig(host="mqtt.local", base_topic="wiight/bathroom")


def test_availability_and_status_are_retained() -> None:
    availability = availability_message(mqtt_config(), False)
    status = status_message(
        mqtt_config(),
        state="degraded",
        board_connected=False,
        calibrated=True,
        error="disconnected",
    )

    assert availability.retain and availability.qos == 1
    assert availability.payload == "offline"
    assert status.retain and json.loads(status.payload)["error"] == "disconnected"

    pairing = pairing_status_message(mqtt_config(), state="failed", error="timeout")
    assert pairing.topic == "wiight/bathroom/pair/status"
    assert pairing.retain and json.loads(pairing.payload)["state"] == "failed"

    tare = tare_status_message(mqtt_config(), state="failed", error="unstable")
    assert tare.topic == "wiight/bathroom/tare/status"
    assert tare.retain and json.loads(tare.payload)["error"] == "unstable"


def test_weight_is_non_retained_and_contains_no_corner_samples() -> None:
    message = weight_message(
        mqtt_config(),
        StableMeasurement(7234, 3, 10.0, 120),
        measured_at=datetime(2026, 8, 11, 12, 30, tzinfo=UTC),
    )
    payload = json.loads(message.payload)

    assert message.topic == "wiight/bathroom/weight"
    assert not message.retain
    assert payload["weight_kg"] == 72.34
    assert payload["measured_at"] == "2026-08-11T12:30:00Z"
    assert "corners" not in message.payload


def test_discovery_is_retained_device_discovery() -> None:
    message = discovery_message(mqtt_config(), "wiight_bathroom")
    payload = json.loads(message.payload)
    component = payload["components"]["weight"]
    connected = payload["components"]["connected"]

    assert message.topic == "homeassistant/device/wiight_bathroom/config"
    assert message.retain
    assert component["device_class"] == "weight"
    assert component["state_class"] == "measurement"
    assert component["unit_of_measurement"] == "kg"
    assert connected["platform"] == "binary_sensor"
    assert connected["name"] == "Connected"
    assert connected["unique_id"] == "wiight_bathroom_connected"
    assert connected["device_class"] == "connectivity"
    assert connected["state_topic"] == "wiight/bathroom/status"
    assert connected["value_template"] == (
        "{{ 'ON' if value_json.board_connected else 'OFF' }}"
    )
    assert connected["payload_on"] == "ON"
    assert connected["payload_off"] == "OFF"
    assert payload["components"]["pair"]["command_topic"] == (
        "wiight/bathroom/pair/set"
    )
    assert payload["components"]["pair"]["payload_press"] == "PAIR"
    assert payload["components"]["tare"]["command_topic"] == (
        "wiight/bathroom/tare/set"
    )
    assert payload["components"]["tare"]["payload_press"] == "TARE"


def test_weight_rejects_naive_timestamp() -> None:
    try:
        weight_message(
            mqtt_config(),
            StableMeasurement(7234, 3, 10.0, 120),
            measured_at=datetime(2026, 8, 11, 12, 30),
        )
    except ValueError as error:
        assert "timezone" in str(error)
    else:
        raise AssertionError("naive timestamp should be rejected")


class FakePublishInfo:
    rc = 0

    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def wait_for_publish(self, timeout: float) -> None:
        self.calls.append(("flush", timeout))


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.on_connect: Any = None
        self.on_message: Any = None

    def will_set(self, *args, **kwargs) -> None:
        self.calls.append(("will_set", args, kwargs))

    def username_pw_set(self, *args) -> None:
        self.calls.append(("credentials", args))

    def tls_set(self) -> None:
        self.calls.append(("tls",))

    def connect(self, host: str, port: int) -> None:
        self.calls.append(("connect", host, port))

    def subscribe(self, topic: str, qos: int) -> None:
        self.calls.append(("subscribe", topic, qos))

    def loop_start(self) -> None:
        self.calls.append(("loop_start",))

    def publish(self, *args, **kwargs):
        self.calls.append(("publish", args, kwargs))
        return FakePublishInfo(self.calls)

    def disconnect(self) -> None:
        self.calls.append(("disconnect",))

    def loop_stop(self) -> None:
        self.calls.append(("loop_stop",))


def test_mqtt_publisher_configures_lwt_credentials_tls_and_lifecycle() -> None:
    client = FakeClient()
    config = MqttConfig(host="mqtt.local", tls=True)
    publisher = MqttPublisher(
        config,
        username="user",
        password="secret",
        client_factory=lambda client_id: client,
    )

    publisher.start()
    publisher.publish(availability_message(config, True))
    publisher.flush()
    publisher.stop()

    assert client.calls[0][0] == "will_set"
    assert client.calls[0][2]["retain"] is True
    assert ("credentials", ("user", "secret")) in client.calls
    assert ("tls",) in client.calls
    assert ("connect", "mqtt.local", 1883) in client.calls
    assert any(call[0] == "publish" for call in client.calls)
    assert ("flush", 5.0) in client.calls
    assert client.calls[-2:] == [("disconnect",), ("loop_stop",)]


def test_mqtt_publisher_buffers_only_valid_non_retained_commands() -> None:
    client = FakeClient()
    publisher = MqttPublisher(
        mqtt_config(), client_factory=lambda client_id: client
    )
    client.on_connect(client, None, None, 0, None)

    class Message:
        topic = pair_command_topic(mqtt_config())
        payload = b"PAIR"
        retain = False

    client.on_message(client, None, Message())
    client.on_message(client, None, Message())

    assert ("subscribe", "wiight/bathroom/pair/set", 1) in client.calls
    assert isinstance(publisher.get_command(), PairCommand)
    assert isinstance(publisher.get_command(), PairCommand)

    Message.topic = tare_command_topic(mqtt_config())
    Message.payload = b"TARE"
    client.on_message(client, None, Message())
    assert ("subscribe", "wiight/bathroom/tare/set", 1) in client.calls
    assert isinstance(publisher.get_command(), TareCommand)

    Message.retain = True
    client.on_message(client, None, Message())
    assert publisher.get_command() is None


def test_mqtt_publisher_requires_username_with_password() -> None:
    try:
        MqttPublisher(
            mqtt_config(),
            password="secret",
            client_factory=lambda client_id: FakeClient(),
        )
    except MqttError as error:
        assert "requires a username" in str(error)
    else:
        raise AssertionError("password without username should fail")


def test_mqtt_publisher_cleans_up_failed_start() -> None:
    class BrokenClient(FakeClient):
        def loop_start(self) -> None:
            raise OSError("loop failed")

    client = BrokenClient()
    publisher = MqttPublisher(
        mqtt_config(), client_factory=lambda client_id: client
    )

    try:
        publisher.start()
    except MqttError as error:
        assert "loop failed" in str(error)
    else:
        raise AssertionError("failed MQTT start should raise")

    assert client.calls[-2:] == [("disconnect",), ("loop_stop",)]
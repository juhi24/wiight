import json
from datetime import UTC, datetime

from wiight.config import MqttConfig
from wiight.measurement import StableMeasurement
from wiight.mqtt import (
    availability_message,
    discovery_message,
    status_message,
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

    assert message.topic == "homeassistant/device/wiight_bathroom/config"
    assert message.retain
    assert component["device_class"] == "weight"
    assert component["state_class"] == "measurement"
    assert component["unit_of_measurement"] == "kg"


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
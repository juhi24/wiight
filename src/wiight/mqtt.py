from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from wiight.config import MqttConfig
from wiight.measurement import StableMeasurement, centikilograms_to_kilograms


@dataclass(frozen=True, slots=True)
class PublishMessage:
    topic: str
    payload: str
    qos: int = 1
    retain: bool = False


def availability_message(config: MqttConfig, online: bool) -> PublishMessage:
    return PublishMessage(
        topic=f"{config.base_topic}/availability",
        payload="online" if online else "offline",
        retain=True,
    )


def status_message(
    config: MqttConfig,
    *,
    state: str,
    board_connected: bool,
    calibrated: bool,
    error: str | None = None,
) -> PublishMessage:
    payload = {
        "state": state,
        "board_connected": board_connected,
        "calibrated": calibrated,
        "error": error,
    }
    return PublishMessage(
        topic=f"{config.base_topic}/status",
        payload=json.dumps(payload, separators=(",", ":")),
        retain=True,
    )


def weight_message(
    config: MqttConfig,
    measurement: StableMeasurement,
    *,
    measured_at: datetime,
) -> PublishMessage:
    if measured_at.tzinfo is None:
        raise ValueError("measured_at must include a timezone")
    payload = {
        "weight_kg": centikilograms_to_kilograms(measurement.raw_total),
        "dispersion_kg": centikilograms_to_kilograms(measurement.raw_stddev),
        "measured_at": measured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "monotonic_time": measurement.monotonic_time,
        "sample_count": measurement.sample_count,
    }
    return PublishMessage(
        topic=f"{config.base_topic}/weight",
        payload=json.dumps(payload, separators=(",", ":")),
        retain=False,
    )


def discovery_message(config: MqttConfig, device_id: str) -> PublishMessage:
    availability_topic = f"{config.base_topic}/availability"
    state_topic = f"{config.base_topic}/weight"
    payload: dict[str, Any] = {
        "device": {
            "identifiers": [device_id],
            "name": "Wii Balance Board",
            "manufacturer": "Nintendo",
            "model": "RVL-WBC-01",
        },
        "origin": {"name": "wiight"},
        "components": {
            "weight": {
                "platform": "sensor",
                "name": "Weight",
                "unique_id": f"{device_id}_weight",
                "device_class": "weight",
                "state_class": "measurement",
                "unit_of_measurement": "kg",
                "state_topic": state_topic,
                "value_template": "{{ value_json.weight_kg }}",
                "availability_topic": availability_topic,
                "payload_available": "online",
                "payload_not_available": "offline",
            }
        },
    }
    return PublishMessage(
        topic=f"{config.discovery_prefix}/device/{device_id}/config",
        payload=json.dumps(payload, separators=(",", ":")),
        retain=True,
    )
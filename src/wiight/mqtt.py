from __future__ import annotations

import json
import importlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from wiight.config import MqttConfig
from wiight.measurement import StableMeasurement, centikilograms_to_kilograms


@dataclass(frozen=True, slots=True)
class PublishMessage:
    topic: str
    payload: str
    qos: int = 1
    retain: bool = False


class MqttError(RuntimeError):
    pass


def _default_client_factory(client_id: str):
    try:
        mqtt = importlib.import_module("paho.mqtt.client")
    except ImportError as error:
        raise MqttError(
            "MQTT support requires the optional paho-mqtt dependency"
        ) from error
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)


class MqttPublisher:
    def __init__(
        self,
        config: MqttConfig,
        *,
        username: str | None = None,
        password: str | None = None,
        client_factory: Callable[[str], Any] = _default_client_factory,
    ) -> None:
        if password is not None and username is None:
            raise MqttError("MQTT password requires a username")
        self.config = config
        self._client = client_factory(config.client_id)
        self._started = False
        self._last_publish: Any | None = None
        offline = availability_message(config, False)
        self._client.will_set(
            offline.topic,
            offline.payload,
            qos=offline.qos,
            retain=offline.retain,
        )
        if username is not None:
            self._client.username_pw_set(username, password)
        if config.tls:
            self._client.tls_set()

    def start(self) -> None:
        if self._started:
            raise MqttError("MQTT publisher is already started")
        try:
            self._client.connect(self.config.host, self.config.port)
            self._client.loop_start()
        except Exception as error:
            try:
                self._client.disconnect()
                self._client.loop_stop()
            except Exception:
                pass
            raise MqttError(f"could not connect to MQTT broker: {error}") from error
        self._started = True

    def publish(self, message: PublishMessage) -> None:
        if not self._started:
            raise MqttError("MQTT publisher is not started")
        info = self._client.publish(
            message.topic,
            message.payload,
            qos=message.qos,
            retain=message.retain,
        )
        if getattr(info, "rc", 0) != 0:
            raise MqttError(f"MQTT publish failed with result {info.rc}")
        self._last_publish = info

    def flush(self, timeout: float = 5.0) -> None:
        if self._last_publish is None:
            return
        try:
            self._last_publish.wait_for_publish(timeout=timeout)
        except Exception as error:
            raise MqttError(f"MQTT publish did not flush: {error}") from error

    def stop(self, timeout: float = 5.0) -> None:
        if not self._started:
            return
        try:
            self._client.disconnect()
            self._client.loop_stop()
        finally:
            self._started = False
            self._last_publish = None


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
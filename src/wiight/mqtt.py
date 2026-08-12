"""Build MQTT messages and publish smart-scale state through paho-mqtt."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from queue import Empty, Full, Queue
from typing import Any, Callable

from wiight.config import MqttConfig
from wiight.measurement import StableMeasurement, centikilograms_to_kilograms


@dataclass(frozen=True, slots=True)
class PublishMessage:
    """Describe an MQTT publication independently of a client library."""

    topic: str
    payload: str
    qos: int = 1
    retain: bool = False


@dataclass(frozen=True, slots=True)
class PairCommand:
    """Request pairing of the configured balance board."""


@dataclass(frozen=True, slots=True)
class TareCommand:
    """Request tare of the configured balance board."""


Command = PairCommand | TareCommand


class MqttError(RuntimeError):
    """Raised when MQTT setup, publication, or shutdown fails."""


def _default_client_factory(client_id: str):
    try:
        mqtt = importlib.import_module("paho.mqtt.client")
    except ImportError as error:
        raise MqttError(
            "MQTT support requires the optional paho-mqtt dependency"
        ) from error
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)


class MqttPublisher:
    """Manage MQTT transport and a bounded queue of service commands.

    The publisher installs a retained offline last will and subscribes to pairing
    and tare command topics after connecting. Only non-retained messages whose
    payload exactly matches their topic's command are queued.
    """

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
        self._commands: Queue[Command] = Queue(maxsize=2)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
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
        """Connect to the broker and start paho's network loop.

        Raises:
            MqttError: If already started or the broker connection fails.
        """

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
        """Queue a message for publication and remember it for flushing.

        Raises:
            MqttError: If not started or paho rejects the publication.
        """

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

    def get_command(self, timeout: float = 0.0) -> Command | None:
        """Return the next service command, or ``None`` after the timeout."""

        try:
            return self._commands.get(timeout=timeout)
        except Empty:
            return None

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if getattr(reason_code, "is_failure", False):
            return
        client.subscribe(pair_command_topic(self.config), qos=1)
        client.subscribe(tare_command_topic(self.config), qos=1)

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        if message.retain:
            return
        try:
            payload = bytes(message.payload).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return
        if message.topic == pair_command_topic(self.config) and payload == "PAIR":
            command: Command = PairCommand()
        elif message.topic == tare_command_topic(self.config) and payload == "TARE":
            command = TareCommand()
        else:
            return
        try:
            self._commands.put_nowait(command)
        except Full:
            pass

    def flush(self, timeout: float = 5.0) -> None:
        """Wait for the most recently queued publication to complete.

        Raises:
            MqttError: If paho cannot confirm publication before the timeout.
        """

        if self._last_publish is None:
            return
        try:
            self._last_publish.wait_for_publish(timeout=timeout)
        except Exception as error:
            raise MqttError(f"MQTT publish did not flush: {error}") from error

    def stop(self, timeout: float = 5.0) -> None:
        """Disconnect and stop the network loop if currently started."""

        if not self._started:
            return
        try:
            self._client.disconnect()
            self._client.loop_stop()
        finally:
            self._started = False
            self._last_publish = None


def availability_message(config: MqttConfig, online: bool) -> PublishMessage:
    """Build a retained online or offline availability message."""

    return PublishMessage(
        topic=f"{config.base_topic}/availability",
        payload="online" if online else "offline",
        retain=True,
    )


def pair_command_topic(config: MqttConfig) -> str:
    """Return the command topic used to request board pairing."""

    return f"{config.base_topic}/pair/set"


def tare_command_topic(config: MqttConfig) -> str:
    """Return the command topic used to request tare."""

    return f"{config.base_topic}/tare/set"


def pairing_status_message(
    config: MqttConfig, *, state: str, error: str | None = None
) -> PublishMessage:
    """Build a retained pairing-state message."""

    return PublishMessage(
        topic=f"{config.base_topic}/pair/status",
        payload=json.dumps(
            {"state": state, "error": error}, separators=(",", ":")
        ),
        retain=True,
    )


def tare_status_message(
    config: MqttConfig, *, state: str, error: str | None = None
) -> PublishMessage:
    """Build a retained tare-state message."""

    return PublishMessage(
        topic=f"{config.base_topic}/tare/status",
        payload=json.dumps(
            {"state": state, "error": error}, separators=(",", ":")
        ),
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
    """Build a retained daemon and board status message."""

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
    """Build a non-retained stable-weight message in kilograms.

    Raises:
        ValueError: If ``measured_at`` does not include timezone information.
    """

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
    """Build retained Home Assistant discovery for weight and controls."""

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
            },
            "pair": {
                "platform": "button",
                "name": "Pair",
                "unique_id": f"{device_id}_pair",
                "command_topic": pair_command_topic(config),
                "payload_press": "PAIR",
                "availability_topic": availability_topic,
                "payload_available": "online",
                "payload_not_available": "offline",
            },
            "tare": {
                "platform": "button",
                "name": "Tare",
                "unique_id": f"{device_id}_tare",
                "command_topic": tare_command_topic(config),
                "payload_press": "TARE",
                "availability_topic": availability_topic,
                "payload_available": "online",
                "payload_not_available": "offline",
            },
        },
    }
    return PublishMessage(
        topic=f"{config.discovery_prefix}/device/{device_id}/config",
        payload=json.dumps(payload, separators=(",", ":")),
        retain=True,
    )
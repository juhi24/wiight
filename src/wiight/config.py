from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("/etc/wiight/wiight.toml")
DEFAULT_CALIBRATION_PATH = Path("/var/lib/wiight/calibration.json")


class ConfigError(ValueError):
    pass


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number")
    return float(value)


@dataclass(frozen=True, slots=True)
class BoardConfig:
    address: str
    adapter: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.address, str):
            raise ConfigError("board.address must be a string")
        if self.adapter is not None and not isinstance(self.adapter, str):
            raise ConfigError("board.adapter must be a string")
        normalized = self.address.upper()
        if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", normalized):
            raise ConfigError("board.address must be a Bluetooth MAC address")
        object.__setattr__(self, "address", normalized)


@dataclass(frozen=True, slots=True)
class MeasurementSettings:
    minimum_weight_centikilograms: float = 1000.0
    stable_duration_seconds: float = 2.0
    maximum_stddev_centikilograms: float = 50.0
    unload_threshold_centikilograms: float = 500.0

    def __post_init__(self) -> None:
        minimum_weight = _number(
            self.minimum_weight_centikilograms,
            "measurement.minimum_weight_centikilograms",
        )
        stable_duration = _number(
            self.stable_duration_seconds, "measurement.stable_duration_seconds"
        )
        maximum_stddev = _number(
            self.maximum_stddev_centikilograms,
            "measurement.maximum_stddev_centikilograms",
        )
        unload_threshold = _number(
            self.unload_threshold_centikilograms,
            "measurement.unload_threshold_centikilograms",
        )
        if minimum_weight <= 0:
            raise ConfigError("measurement.minimum_weight_centikilograms must be positive")
        if stable_duration <= 0:
            raise ConfigError("measurement.stable_duration_seconds must be positive")
        if maximum_stddev <= 0:
            raise ConfigError("measurement.maximum_stddev_centikilograms must be positive")
        if not 0 <= unload_threshold < minimum_weight:
            raise ConfigError(
                "measurement.unload_threshold_centikilograms must be non-negative "
                "and less than minimum_weight_centikilograms"
            )


@dataclass(frozen=True, slots=True)
class CalibrationSettings:
    path: Path = DEFAULT_CALIBRATION_PATH
    minimum_samples: int = 100
    maximum_corner_stddev_centikilograms: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise ConfigError("calibration.path must be a string path")
        if isinstance(self.minimum_samples, bool) or not isinstance(
            self.minimum_samples, int
        ):
            raise ConfigError("calibration.minimum_samples must be an integer")
        maximum_stddev = _number(
            self.maximum_corner_stddev_centikilograms,
            "calibration.maximum_corner_stddev_centikilograms",
        )
        if self.minimum_samples < 2:
            raise ConfigError("calibration.minimum_samples must be at least 2")
        if maximum_stddev <= 0:
            raise ConfigError(
                "calibration.maximum_corner_stddev_centikilograms must be positive"
            )


@dataclass(frozen=True, slots=True)
class MqttConfig:
    host: str
    port: int = 1883
    client_id: str = "wiight"
    base_topic: str = "wiight/scale"
    discovery_prefix: str = "homeassistant"
    tls: bool = False

    def __post_init__(self) -> None:
        for name in ("host", "client_id", "base_topic", "discovery_prefix"):
            if not isinstance(getattr(self, name), str):
                raise ConfigError(f"mqtt.{name} must be a string")
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise ConfigError("mqtt.port must be an integer")
        if not isinstance(self.tls, bool):
            raise ConfigError("mqtt.tls must be a boolean")
        if not self.host.strip():
            raise ConfigError("mqtt.host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ConfigError("mqtt.port must be between 1 and 65535")
        for name in ("client_id", "base_topic", "discovery_prefix"):
            if not getattr(self, name).strip():
                raise ConfigError(f"mqtt.{name} must not be empty")


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    board: BoardConfig
    mqtt: MqttConfig
    measurement: MeasurementSettings = field(default_factory=MeasurementSettings)
    calibration: CalibrationSettings = field(default_factory=CalibrationSettings)


def _table(data: dict[str, Any], name: str, allowed: set[str]) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a TOML table")
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"unknown {name} option(s): {', '.join(sorted(unknown))}")
    return value


def parse_config(data: dict[str, Any]) -> ServiceConfig:
    unknown_tables = set(data) - {"board", "measurement", "calibration", "mqtt"}
    if unknown_tables:
        raise ConfigError(f"unknown configuration table(s): {', '.join(sorted(unknown_tables))}")

    board = _table(data, "board", {"address", "adapter"})
    measurement = _table(
        data,
        "measurement",
        {
            "minimum_weight_centikilograms",
            "stable_duration_seconds",
            "maximum_stddev_centikilograms",
            "unload_threshold_centikilograms",
        },
    )
    calibration = _table(
        data,
        "calibration",
        {"path", "minimum_samples", "maximum_corner_stddev_centikilograms"},
    )
    mqtt = _table(
        data,
        "mqtt",
        {"host", "port", "client_id", "base_topic", "discovery_prefix", "tls"},
    )

    try:
        return ServiceConfig(
            board=BoardConfig(**board),
            mqtt=MqttConfig(**mqtt),
            measurement=MeasurementSettings(**measurement),
            calibration=CalibrationSettings(
                **(
                    {**calibration, "path": Path(calibration["path"])}
                    if "path" in calibration
                    else calibration
                )
            ),
        )
    except TypeError as error:
        raise ConfigError(f"missing or invalid configuration value: {error}") from error


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> ServiceConfig:
    try:
        with path.open("rb") as config_file:
            return parse_config(tomllib.load(config_file))
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file not found: {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {path}: {error}") from error
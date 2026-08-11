from pathlib import Path

import pytest

from wiight.config import ConfigError, load_config, parse_config


def valid_config() -> dict:
    return {
        "board": {"address": "00:22:4c:60:0c:db"},
        "mqtt": {"host": "mqtt.local"},
    }


def test_parse_config_applies_defaults_and_normalizes_address() -> None:
    config = parse_config(valid_config())

    assert config.board.address == "00:22:4C:60:0C:DB"
    assert config.measurement.stable_duration_seconds == 2
    assert config.calibration.path == Path("/var/lib/wiight/calibration.json")
    assert config.mqtt.port == 1883


def test_load_config_reads_toml(tmp_path: Path) -> None:
    path = tmp_path / "wiight.toml"
    path.write_text(
        """
[board]
address = "00:22:4C:60:0C:DB"
adapter = "hci0"

[measurement]
minimum_weight_centikilograms = 2000
unload_threshold_centikilograms = 300

[calibration]
path = "/tmp/calibration.json"

[mqtt]
host = "mqtt.local"
tls = true
""".strip(),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.board.adapter == "hci0"
    assert config.measurement.minimum_weight_centikilograms == 2000
    assert config.calibration.path == Path("/tmp/calibration.json")
    assert config.mqtt.tls is True


def test_parse_config_rejects_unknown_options() -> None:
    data = valid_config()
    data["mqtt"]["password"] = "not-allowed-here"

    with pytest.raises(ConfigError, match="unknown mqtt option.*password"):
        parse_config(data)


def test_parse_config_rejects_invalid_cross_field_thresholds() -> None:
    data = valid_config()
    data["measurement"] = {
        "minimum_weight_centikilograms": 100,
        "unload_threshold_centikilograms": 100,
    }

    with pytest.raises(ConfigError, match="less than minimum"):
        parse_config(data)


@pytest.mark.parametrize(
    "section,key,value,message",
    [
        ("board", "address", 123, "board.address must be a string"),
        ("mqtt", "port", "1883", "mqtt.port must be an integer"),
        ("mqtt", "tls", "false", "mqtt.tls must be a boolean"),
        (
            "measurement",
            "stable_duration_seconds",
            True,
            "stable_duration_seconds must be a number",
        ),
    ],
)
def test_parse_config_rejects_wrong_value_types(
    section: str, key: str, value, message: str
) -> None:
    data = valid_config()
    data.setdefault(section, {})[key] = value

    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_load_config_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="configuration file not found"):
        load_config(tmp_path / "missing.toml")
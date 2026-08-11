import builtins
import importlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path

from click.testing import CliRunner

import wiight.hardware as hardware
from wiight import CornerReading
from wiight.calibration import load_calibration, store_calibration
from wiight.measurement import TareCalibration


def test_cli_help_does_not_import_native_hardware(monkeypatch) -> None:
    blocked_modules = {"dbus", "gi", "gobject", "xwiimote"}
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.partition(".")[0] in blocked_modules:
            raise AssertionError(f"unexpected native import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules.pop("wiight.cli", None)
    module = importlib.import_module("wiight.cli")

    bare_result = CliRunner().invoke(module.main)
    help_result = CliRunner().invoke(module.main, ["--help"])

    assert bare_result.exit_code == 0
    assert help_result.exit_code == 0
    assert "Usage:" in bare_result.output
    assert "Usage:" in help_result.output


def test_capture_writes_json_lines(monkeypatch) -> None:
    module = importlib.import_module("wiight.cli")

    @contextmanager
    def fake_open(device):
        assert device == "/device"
        yield object()

    events = [
        hardware.CapturedEvent(
            wall_time=100.0,
            monotonic_time=5.0,
            event_type=3,
            corners=CornerReading(30, 10, 20, 40),
        )
    ]
    monkeypatch.setattr(hardware, "open_balance_board", fake_open)
    monkeypatch.setattr(hardware, "capture_events", lambda *args, **kwargs: events)

    result = CliRunner().invoke(
        module.main,
        ["capture", "--device", "/device", "--duration", "1"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["corners_centikilograms"] == [30, 10, 20, 40]


def write_config(path: Path, calibration_path: Path) -> None:
    path.write_text(
        f"""
[board]
address = "00:22:4C:60:0C:DB"

[calibration]
path = "{calibration_path}"
minimum_samples = 3
maximum_corner_stddev_centikilograms = 2

[measurement]
minimum_weight_centikilograms = 100
stable_duration_seconds = 2
maximum_stddev_centikilograms = 2
unload_threshold_centikilograms = 20

[mqtt]
host = "mqtt.local"
""".strip(),
        encoding="utf-8",
    )


def test_config_check_reports_valid_config_without_hardware(tmp_path: Path) -> None:
    config_path = tmp_path / "wiight.toml"
    write_config(config_path, tmp_path / "missing-calibration.json")

    result = CliRunner().invoke(
        importlib.import_module("wiight.cli").main,
        ["config-check", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert "configuration valid" in result.output
    assert "calibration=missing" in result.output


def test_config_check_rejects_calibration_for_other_board(tmp_path: Path) -> None:
    config_path = tmp_path / "wiight.toml"
    calibration_path = tmp_path / "calibration.json"
    write_config(config_path, calibration_path)
    store_calibration(
        calibration_path,
        "AA:BB:CC:DD:EE:FF",
        TareCalibration(CornerReading(1, 2, 3, 4), 100, 2),
    )

    result = CliRunner().invoke(
        importlib.import_module("wiight.cli").main,
        ["config-check", "--config", str(config_path)],
    )

    assert result.exit_code != 0
    assert "belongs to board" in result.output


def captured_event(timestamp: float, corner_value: float) -> hardware.CapturedEvent:
    return hardware.CapturedEvent(
        wall_time=100 + timestamp,
        monotonic_time=timestamp,
        event_type=3,
        corners=CornerReading(*(corner_value for _ in range(4))),
    )


def patch_hardware(monkeypatch, events) -> None:
    @contextmanager
    def fake_open(device):
        yield object()

    monkeypatch.setattr(hardware, "open_balance_board", fake_open)
    def find_configured(address, adapter):
        assert address == "00:22:4C:60:0C:DB"
        assert adapter is None
        return "/device"

    monkeypatch.setattr(hardware, "find_configured_balance_board_path", find_configured)
    monkeypatch.setattr(
        hardware,
        "capture_events",
        lambda *args, **kwargs: (event for event in events),
    )


def test_tare_persists_synthetic_calibration(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "wiight.toml"
    calibration_path = tmp_path / "calibration.json"
    write_config(config_path, calibration_path)
    patch_hardware(
        monkeypatch,
        [captured_event(1, 10), captured_event(2, 11), captured_event(3, 12)],
    )

    result = CliRunner().invoke(
        importlib.import_module("wiight.cli").main,
        ["tare", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert "tare saved" in result.output
    stored = load_calibration(calibration_path, "00:22:4C:60:0C:DB")
    assert stored.calibration.offsets == CornerReading(11, 11, 11, 11)


def test_measure_outputs_stable_synthetic_weight_as_json(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "wiight.toml"
    calibration_path = tmp_path / "calibration.json"
    write_config(config_path, calibration_path)
    store_calibration(
        calibration_path,
        "00:22:4C:60:0C:DB",
        TareCalibration(CornerReading(0, 0, 0, 0), 100, 0),
    )
    patch_hardware(
        monkeypatch,
        [captured_event(1, 25), captured_event(2, 25), captured_event(3, 25)],
    )

    result = CliRunner().invoke(
        importlib.import_module("wiight.cli").main,
        ["measure", "--config", str(config_path), "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "weight_kg": 1.0,
        "dispersion_kg": 0.0,
        "monotonic_time": 3,
        "sample_count": 3,
    }


def test_measure_uses_zero_tare_when_calibration_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "wiight.toml"
    calibration_path = tmp_path / "missing-calibration.json"
    write_config(config_path, calibration_path)
    patch_hardware(
        monkeypatch,
        [captured_event(1, 25), captured_event(2, 25), captured_event(3, 25)],
    )

    result = CliRunner().invoke(
        importlib.import_module("wiight.cli").main,
        ["measure", "--config", str(config_path), "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["weight_kg"] == 1.0


def test_measure_continuous_outputs_each_rearmed_measurement(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "wiight.toml"
    calibration_path = tmp_path / "calibration.json"
    write_config(config_path, calibration_path)
    store_calibration(
        calibration_path,
        "00:22:4C:60:0C:DB",
        TareCalibration(CornerReading(0, 0, 0, 0), 100, 0),
    )
    patch_hardware(
        monkeypatch,
        [
            captured_event(1, 25),
            captured_event(2, 25),
            captured_event(3, 25),
            captured_event(4, 0),
            captured_event(5, 30),
            captured_event(6, 30),
            captured_event(7, 30),
        ],
    )

    result = CliRunner().invoke(
        importlib.import_module("wiight.cli").main,
        ["measure", "--config", str(config_path), "--continuous", "--json"],
    )

    assert result.exit_code == 0
    assert [json.loads(line)["weight_kg"] for line in result.output.splitlines()] == [
        1.0,
        1.2,
    ]


def test_tare_explicit_device_bypasses_configured_discovery(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "wiight.toml"
    calibration_path = tmp_path / "calibration.json"
    write_config(config_path, calibration_path)

    @contextmanager
    def fake_open(device):
        assert device == "/explicit-device"
        yield object()

    monkeypatch.setattr(hardware, "open_balance_board", fake_open)
    monkeypatch.setattr(
        hardware,
        "find_configured_balance_board_path",
        lambda *args: (_ for _ in ()).throw(AssertionError("unexpected discovery")),
    )
    monkeypatch.setattr(
        hardware,
        "capture_events",
        lambda *args, **kwargs: (
            event
            for event in [
                captured_event(1, 10),
                captured_event(2, 11),
                captured_event(3, 12),
            ]
        ),
    )

    result = CliRunner().invoke(
        importlib.import_module("wiight.cli").main,
        [
            "tare",
            "--config",
            str(config_path),
            "--device",
            "/explicit-device",
        ],
    )

    assert result.exit_code == 0


def test_daemon_loads_config_calibration_and_environment_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "wiight.toml"
    calibration_path = tmp_path / "calibration.json"
    write_config(config_path, calibration_path)
    store_calibration(
        calibration_path,
        "00:22:4C:60:0C:DB",
        TareCalibration(CornerReading(0, 0, 0, 0), 100, 0),
    )
    calls = []

    class FakePublisher:
        def __init__(self, config, *, username=None, password=None) -> None:
            calls.append(("publisher", username, password))

    class FakeService:
        def __init__(self, config, calibration, publisher) -> None:
            calls.append(("service", config.board.address, calibration.sample_count))

        def run(self, stop_event) -> None:
            calls.append(("run", stop_event.is_set()))

    monkeypatch.setenv("WIIGHT_MQTT_USERNAME", "user")
    monkeypatch.setenv("WIIGHT_MQTT_PASSWORD", "secret")
    monkeypatch.setattr("wiight.mqtt.MqttPublisher", FakePublisher)
    monkeypatch.setattr("wiight.daemon.DaemonService", FakeService)

    result = CliRunner().invoke(
        importlib.import_module("wiight.cli").main,
        ["daemon", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert ("publisher", "user", "secret") in calls
    assert ("service", "00:22:4C:60:0C:DB", 100) in calls
    assert ("run", False) in calls


def test_daemon_starts_without_initial_calibration(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "wiight.toml"
    write_config(config_path, tmp_path / "missing-calibration.json")
    calibrations = []

    class FakePublisher:
        def __init__(self, config, *, username=None, password=None) -> None:
            pass

    class FakeService:
        def __init__(self, config, calibration, publisher) -> None:
            calibrations.append(calibration)

        def run(self, stop_event) -> None:
            pass

    monkeypatch.setattr("wiight.mqtt.MqttPublisher", FakePublisher)
    monkeypatch.setattr("wiight.daemon.DaemonService", FakeService)

    result = CliRunner().invoke(
        importlib.import_module("wiight.cli").main,
        ["daemon", "--config", str(config_path)],
    )

    assert result.exit_code == 0
    assert calibrations == [None]
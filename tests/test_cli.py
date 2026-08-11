import builtins
import importlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path

from click.testing import CliRunner

import wiight.hardware as hardware
from wiight import CornerReading
from wiight.calibration import store_calibration
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
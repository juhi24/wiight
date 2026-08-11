import builtins
import importlib
import json
import sys
from contextlib import contextmanager

from click.testing import CliRunner

import wiight.hardware as hardware
from wiight import CornerReading


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
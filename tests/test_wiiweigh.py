import builtins
import importlib
import sys

from click.testing import CliRunner


class FakeBalanceEvent:
    def __init__(self, channels: tuple[int, int, int, int]) -> None:
        self.channels = channels

    def get_abs(self, index: int) -> tuple[int, int, int]:
        return self.channels[index], 0, 0


def test_cli_help_does_not_import_native_hardware(monkeypatch) -> None:
    blocked_modules = {"dbus", "gi", "gobject", "xwiimote"}
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.partition(".")[0] in blocked_modules:
            raise AssertionError(f"unexpected native import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules.pop("wiight.wiiweigh", None)
    module = importlib.import_module("wiight.wiiweigh")

    result = CliRunner().invoke(module.main, ["--help"])

    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_calibrate_returns_finite_canonical_offsets(monkeypatch) -> None:
    module = importlib.import_module("wiight.wiiweigh")
    readings = iter([(10, 20, 30, 40)] * 10)
    monkeypatch.setattr(module, "measurements", lambda iface: readings)

    calibration = module.calibrate(object())

    assert calibration == (10, 20, 30, 40)


def test_corner_reading_uses_hardware_verified_channel_mapping() -> None:
    module = importlib.import_module("wiight.wiiweigh")

    reading = module.corner_reading_from_event(FakeBalanceEvent((10, 20, 30, 40)))

    assert tuple(reading) == (30, 10, 20, 40)
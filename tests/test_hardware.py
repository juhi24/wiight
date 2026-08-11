from __future__ import annotations

from types import SimpleNamespace

import pytest

import wiight.hardware as hardware
from wiight import CornerReading


class FakeEvent:
    type = 3

    def __init__(self, channels: tuple[int, int, int, int]) -> None:
        self.channels = channels

    def get_abs(self, index: int) -> tuple[int, int, int]:
        return self.channels[index], 0, 0


class FakeInterface:
    def __init__(self) -> None:
        self.closed = False
        self.dispatched = 0

    def get_devtype(self) -> str:
        return "balanceboard"

    def get_fd(self) -> int:
        return 42

    def open(self, mask: int) -> None:
        assert mask == 2048

    def close(self, mask: int) -> None:
        assert mask == 2048
        self.closed = True

    def dispatch(self, event: FakeEvent) -> None:
        self.dispatched += 1


class FakePoller:
    def __init__(self, ready: bool) -> None:
        self.ready = ready
        self.unregistered = False

    def register(self, file_descriptor: int, event_mask: int) -> None:
        assert file_descriptor == 42

    def poll(self, timeout: int):
        return [(42, 1)] if self.ready else []

    def unregister(self, file_descriptor: int) -> None:
        assert file_descriptor == 42
        self.unregistered = True


def test_captured_sample_serializes_canonical_centikilograms() -> None:
    event = hardware.CapturedEvent(
        wall_time=100.0,
        monotonic_time=5.0,
        event_type=3,
        corners=CornerReading(30, 10, 20, 40),
    )

    assert event.as_dict() == {
        "schema_version": 1,
        "type": "sample",
        "wall_time": 100.0,
        "monotonic_time": 5.0,
        "event_type": 3,
        "corners_centikilograms": [30, 10, 20, 40],
        "total_centikilograms": 100,
    }


def test_missing_xwiimote_binding_raises_hardware_error(monkeypatch) -> None:
    def missing_module(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(hardware.importlib, "import_module", missing_module)

    with pytest.raises(hardware.BalanceBoardError, match="binding is not available"):
        hardware.find_balance_board_path()


def test_open_balance_board_closes_interface_on_error(monkeypatch) -> None:
    interface = FakeInterface()
    xwiimote = SimpleNamespace(
        IFACE_BALANCE_BOARD=2048,
        iface=lambda path: interface,
    )
    monkeypatch.setattr(hardware, "_xwiimote_module", lambda: xwiimote)

    with pytest.raises(RuntimeError, match="stop"):
        with hardware.open_balance_board("/device"):
            raise RuntimeError("stop")

    assert interface.closed


def test_capture_events_stops_at_duration_and_maps_channels(monkeypatch) -> None:
    interface = FakeInterface()
    poller = FakePoller(ready=True)
    event = FakeEvent((10, 20, 30, 40))
    xwiimote = SimpleNamespace(
        EVENT_BALANCE_BOARD=3,
        event=lambda: event,
    )
    monotonic_values = iter((0.0, 0.0, 0.1, 1.1))
    monkeypatch.setattr(hardware, "_xwiimote_module", lambda: xwiimote)
    monkeypatch.setattr(hardware.select, "poll", lambda: poller)
    monkeypatch.setattr(hardware.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(hardware.time, "time", lambda: 100.0)

    events = list(hardware.capture_events(interface, duration=1, idle_timeout=1))

    assert len(events) == 1
    assert events[0].corners == CornerReading(30, 10, 20, 40)
    assert poller.unregistered


def test_capture_events_raises_on_idle_timeout(monkeypatch) -> None:
    interface = FakeInterface()
    poller = FakePoller(ready=False)
    xwiimote = SimpleNamespace(EVENT_BALANCE_BOARD=3)
    monotonic_values = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(hardware, "_xwiimote_module", lambda: xwiimote)
    monkeypatch.setattr(hardware.select, "poll", lambda: poller)
    monkeypatch.setattr(hardware.time, "monotonic", lambda: next(monotonic_values))

    with pytest.raises(hardware.CaptureIdleTimeoutError, match="1 seconds"):
        list(hardware.capture_events(interface, duration=10, idle_timeout=1))

    assert poller.unregistered
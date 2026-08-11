from __future__ import annotations

from types import SimpleNamespace
from threading import Event

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


def test_balance_board_reader_requires_open_context(monkeypatch) -> None:
    reader = hardware.BalanceBoardReader("/device")

    with pytest.raises(hardware.BalanceBoardError, match="not open"):
        next(reader.capture_events(duration=1, idle_timeout=1))


def test_balance_board_reader_opens_and_closes_interface(monkeypatch) -> None:
    interface = FakeInterface()
    xwiimote = SimpleNamespace(
        IFACE_BALANCE_BOARD=2048,
        iface=lambda path: interface,
    )
    monkeypatch.setattr(hardware, "_xwiimote_module", lambda: xwiimote)

    reader = hardware.BalanceBoardReader("/device")
    with reader:
        assert reader.is_open

    assert not reader.is_open
    assert interface.closed


def test_configured_discovery_requires_bluez_connection(monkeypatch) -> None:
    objects = {
        "/org/bluez/hci0": {
            hardware.bluezutils.ADAPTER_INTERFACE: {
                "Address": "AA:AA:AA:AA:AA:AA"
            }
        },
        "/org/bluez/hci0/dev_00_22_4C_60_0C_DB": {
            hardware.bluezutils.DEVICE_INTERFACE: {
                "Address": "00:22:4C:60:0C:DB",
                "Connected": True,
            }
        },
    }
    monkeypatch.setattr(
        hardware.bluezutils, "get_managed_objects", lambda bus=None: objects
    )
    monkeypatch.setattr(hardware, "_find_balance_board_paths", lambda: ["/device"])

    assert (
        hardware.find_configured_balance_board_path(
            "00:22:4c:60:0c:db", "hci0"
        )
        == "/device"
    )


def test_configured_discovery_rejects_disconnected_board(monkeypatch) -> None:
    objects = {
        "/org/bluez/hci0/dev_00_22_4C_60_0C_DB": {
            hardware.bluezutils.DEVICE_INTERFACE: {
                "Address": "00:22:4C:60:0C:DB",
                "Connected": False,
            }
        }
    }
    monkeypatch.setattr(
        hardware.bluezutils, "get_managed_objects", lambda bus=None: objects
    )

    with pytest.raises(hardware.BalanceBoardNotFoundError, match="not connected"):
        hardware.find_configured_balance_board_path("00:22:4C:60:0C:DB")


def test_configured_discovery_rejects_multiple_xwiimote_boards(monkeypatch) -> None:
    objects = {
        "/org/bluez/hci0/dev_00_22_4C_60_0C_DB": {
            hardware.bluezutils.DEVICE_INTERFACE: {
                "Address": "00:22:4C:60:0C:DB",
                "Connected": True,
            }
        }
    }
    monkeypatch.setattr(
        hardware.bluezutils, "get_managed_objects", lambda bus=None: objects
    )
    monkeypatch.setattr(
        hardware, "_find_balance_board_paths", lambda: ["/device/1", "/device/2"]
    )

    with pytest.raises(hardware.BalanceBoardNotFoundError, match="multiple"):
        hardware.find_configured_balance_board_path("00:22:4C:60:0C:DB")


def test_configured_discovery_wraps_bluez_unavailable(monkeypatch) -> None:
    def unavailable(bus=None):
        raise hardware.bluezutils.BlueZUnavailableError("system bus unavailable")

    monkeypatch.setattr(hardware.bluezutils, "get_managed_objects", unavailable)

    with pytest.raises(hardware.BalanceBoardNotFoundError, match="system bus"):
        hardware.find_configured_balance_board_path("00:22:4C:60:0C:DB")


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


def test_capture_events_stops_cooperatively(monkeypatch) -> None:
    interface = FakeInterface()
    poller = FakePoller(ready=False)
    stop_event = Event()
    stop_event.set()
    xwiimote = SimpleNamespace(EVENT_BALANCE_BOARD=3)
    monkeypatch.setattr(hardware, "_xwiimote_module", lambda: xwiimote)
    monkeypatch.setattr(hardware.select, "poll", lambda: poller)
    monkeypatch.setattr(hardware.time, "monotonic", lambda: 0.0)

    assert list(
        hardware.capture_events(
            interface,
            duration=10,
            idle_timeout=1,
            stop_event=stop_event,
        )
    ) == []
    assert poller.unregistered


def test_capture_events_can_cancel_after_empty_poll_slice(monkeypatch) -> None:
    interface = FakeInterface()
    stop_event = Event()

    class CancellingPoller(FakePoller):
        def poll(self, timeout: int):
            stop_event.set()
            return []

    poller = CancellingPoller(ready=False)
    xwiimote = SimpleNamespace(EVENT_BALANCE_BOARD=3)
    monotonic_values = iter((0.0, 0.0))
    monkeypatch.setattr(hardware, "_xwiimote_module", lambda: xwiimote)
    monkeypatch.setattr(hardware.select, "poll", lambda: poller)
    monkeypatch.setattr(hardware.time, "monotonic", lambda: next(monotonic_values))

    assert list(
        hardware.capture_events(
            interface,
            duration=10,
            idle_timeout=2,
            stop_event=stop_event,
        )
    ) == []
    assert poller.unregistered
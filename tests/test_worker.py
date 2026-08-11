from __future__ import annotations

from contextlib import AbstractContextManager
from threading import Event

from wiight import CornerReading
from wiight.config import BoardConfig
from wiight.hardware import BalanceBoardError, BalanceBoardNotFoundError, CapturedEvent
from wiight.worker import (
    HardwareWorker,
    WorkerConfig,
    WorkerDisconnected,
    WorkerError,
    WorkerMailbox,
    WorkerSample,
    WorkerStarted,
    WorkerStopped,
)


class FakeReader(AbstractContextManager):
    def __init__(self, path: str, events: list[CapturedEvent]) -> None:
        self.path = path
        self.events = events
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def capture_events(self, **kwargs):
        yield from self.events


def captured(timestamp: float) -> CapturedEvent:
    return CapturedEvent(
        wall_time=100 + timestamp,
        monotonic_time=timestamp,
        event_type=3,
        corners=CornerReading(1, 2, 3, 4),
    )


def test_worker_run_once_emits_lifecycle_and_samples() -> None:
    reader = FakeReader("/device", [captured(1), captured(2)])
    worker = HardwareWorker(
        BoardConfig("00:22:4C:60:0C:DB"),
        discover=lambda address, adapter: "/device",
        reader_factory=lambda path: reader,
    )

    worker.run_once()

    started = worker.events.get_nowait()
    assert isinstance(started, WorkerStarted)
    assert started.device_path == "/device"
    assert isinstance(worker.events.get_nowait(), WorkerSample)
    assert isinstance(worker.events.get_nowait(), WorkerSample)
    assert reader.closed


def test_worker_disconnect_closes_reader_then_disconnects_bluez() -> None:
    reader = FakeReader("/device", [captured(1), captured(2)])
    disconnect_calls: list[tuple[str, str | None, bool]] = []
    worker: HardwareWorker

    def capture_events(**kwargs):
        yield captured(1)
        worker.disconnect()
        if not kwargs["stop_event"].is_set():
            yield captured(2)

    reader.capture_events = capture_events

    def disconnect(address: str, adapter: str | None) -> None:
        disconnect_calls.append((address, adapter, reader.closed))

    worker = HardwareWorker(
        BoardConfig("00:22:4C:60:0C:DB", adapter="hci0"),
        discover=lambda address, adapter: "/device",
        disconnect=disconnect,
        reader_factory=lambda path: reader,
    )

    worker.run_once()

    assert isinstance(worker.events.get_nowait(), WorkerStarted)
    assert isinstance(worker.events.get_nowait(), WorkerSample)
    assert isinstance(worker.events.get_nowait(), WorkerDisconnected)
    assert disconnect_calls == [("00:22:4C:60:0C:DB", "hci0", True)]


def test_worker_quietly_waits_when_intentionally_disconnected() -> None:
    stop_event = Event()

    def connect(address: str, adapter: str | None) -> None:
        stop_event.set()
        raise BalanceBoardNotFoundError("board unavailable")

    worker = HardwareWorker(
        BoardConfig("00:22:4C:60:0C:DB"),
        stop_event=stop_event,
        connect=connect,
    )
    worker._waiting_for_reconnect = True

    worker._run()

    assert isinstance(worker.events.get_nowait(), WorkerStopped)
    assert worker.events.qsize() == 0


def test_worker_connects_profile_before_rediscovering_after_disconnect() -> None:
    stop_event = Event()
    calls: list[str] = []

    def connect(address: str, adapter: str | None) -> None:
        calls.append("connect")

    def discover(address: str, adapter: str | None) -> str:
        calls.append("discover")
        stop_event.set()
        raise BalanceBoardNotFoundError("xwiimote device pending")

    worker = HardwareWorker(
        BoardConfig("00:22:4C:60:0C:DB"),
        stop_event=stop_event,
        connect=connect,
        discover=discover,
    )
    worker._waiting_for_reconnect = True

    worker._run()

    assert calls == ["connect", "discover"]


def test_worker_drops_samples_when_bounded_queue_is_full() -> None:
    event_queue = WorkerMailbox(maxsize=1)
    event_queue.put_control(WorkerStarted(0, "/device"))
    worker = HardwareWorker(
        BoardConfig("00:22:4C:60:0C:DB"),
        event_queue=event_queue,
    )

    worker._put_sample(captured(1))

    assert worker.dropped_samples == 1
    assert isinstance(event_queue.get_nowait(), WorkerStarted)


def test_worker_control_event_evicts_sample_from_full_mailbox() -> None:
    mailbox = WorkerMailbox(maxsize=1)
    worker = HardwareWorker(
        BoardConfig("00:22:4C:60:0C:DB"),
        event_queue=mailbox,
    )
    worker._put_sample(captured(1))

    worker._put_control(WorkerStopped(2))

    assert isinstance(mailbox.get_nowait(), WorkerStopped)
    assert worker.dropped_samples == 1


def test_worker_retries_transient_hardware_error_and_stops() -> None:
    stop_event = Event()
    attempts = 0

    def discover(address: str, adapter: str | None) -> str:
        nonlocal attempts
        attempts += 1
        stop_event.set()
        raise BalanceBoardError("board unavailable")

    worker = HardwareWorker(
        BoardConfig("00:22:4C:60:0C:DB"),
        stop_event=stop_event,
        discover=discover,
    )

    worker._run()

    assert attempts == 1
    assert isinstance(worker.events.get_nowait(), WorkerError)
    assert isinstance(worker.events.get_nowait(), WorkerStopped)


def test_worker_stop_before_start_is_safe() -> None:
    worker = HardwareWorker(BoardConfig("00:22:4C:60:0C:DB"))

    worker.stop()

    assert not worker.is_alive


def test_worker_config_rejects_invalid_limits() -> None:
    try:
        WorkerConfig(queue_size=0)
    except ValueError as error:
        assert "queue_size" in str(error)
    else:
        raise AssertionError("invalid queue size should be rejected")
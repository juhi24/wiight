from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty
from threading import Condition, Event, Thread

from wiight.config import BoardConfig
from wiight.hardware import (
    BalanceBoardError,
    BalanceBoardNotFoundError,
    BalanceBoardReader,
    CapturedEvent,
    disconnect_configured_balance_board,
    find_configured_balance_board_path,
)
from wiight.measurement import SensorSample


@dataclass(frozen=True, slots=True)
class WorkerStarted:
    monotonic_time: float
    device_path: str


@dataclass(frozen=True, slots=True)
class WorkerStopped:
    monotonic_time: float


@dataclass(frozen=True, slots=True)
class WorkerDisconnected:
    monotonic_time: float


@dataclass(frozen=True, slots=True)
class WorkerError:
    monotonic_time: float
    message: str


@dataclass(frozen=True, slots=True)
class WorkerSample:
    wall_time: float
    sample: SensorSample


WorkerEvent = (
    WorkerStarted | WorkerDisconnected | WorkerStopped | WorkerError | WorkerSample
)


class WorkerMailbox:
    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("mailbox maxsize must be positive")
        self.maxsize = maxsize
        self._events: deque[WorkerEvent] = deque()
        self._condition = Condition()

    def put_sample(self, event: WorkerSample) -> bool:
        with self._condition:
            if len(self._events) >= self.maxsize:
                return False
            self._events.append(event)
            self._condition.notify()
            return True

    def put_control(
        self, event: WorkerStarted | WorkerDisconnected | WorkerStopped | WorkerError
    ) -> int:
        dropped = 0
        with self._condition:
            if len(self._events) >= self.maxsize:
                for index, queued in enumerate(self._events):
                    if isinstance(queued, WorkerSample):
                        del self._events[index]
                        dropped = 1
                        break
            self._events.append(event)
            self._condition.notify()
        return dropped

    def get(self, timeout: float | None = None) -> WorkerEvent:
        with self._condition:
            if not self._condition.wait_for(lambda: bool(self._events), timeout):
                raise Empty
            return self._events.popleft()

    def get_nowait(self) -> WorkerEvent:
        return self.get(timeout=0)

    def qsize(self) -> int:
        with self._condition:
            return len(self._events)


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    queue_size: int = 256
    capture_duration: float = 3600.0
    idle_timeout: float = 2.0
    retry_delay: float = 2.0

    def __post_init__(self) -> None:
        if self.queue_size < 1:
            raise ValueError("queue_size must be positive")
        for name in ("capture_duration", "idle_timeout", "retry_delay"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


class HardwareWorker:
    def __init__(
        self,
        board: BoardConfig,
        *,
        config: WorkerConfig = WorkerConfig(),
        event_queue: WorkerMailbox | None = None,
        stop_event: Event | None = None,
        discover: Callable[[str, str | None], str] = find_configured_balance_board_path,
        disconnect: Callable[
            [str, str | None], None
        ] = disconnect_configured_balance_board,
        reader_factory: Callable[[str], BalanceBoardReader] = BalanceBoardReader,
    ) -> None:
        self.board = board
        self.config = config
        self.events = event_queue or WorkerMailbox(config.queue_size)
        self.stop_event = stop_event or Event()
        self._discover = discover
        self._disconnect = disconnect
        self._reader_factory = reader_factory
        self._thread = Thread(target=self._run, name="wiight-hardware", daemon=True)
        self._disconnect_event = Event()
        self._waiting_for_reconnect = False
        self._dropped_samples = 0

    @property
    def dropped_samples(self) -> int:
        return self._dropped_samples

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def disconnect(self) -> None:
        self._disconnect_event.set()

    def stop(self, timeout: float = 5.0) -> None:
        self.stop_event.set()
        self._disconnect_event.set()
        if self._thread.ident is not None:
            self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError("hardware worker did not stop within the deadline")

    def run_once(self) -> None:
        self._disconnect_event.clear()
        if self.stop_event.is_set():
            return
        device_path = self._discover(self.board.address, self.board.adapter)
        self._waiting_for_reconnect = False
        self._put_control(WorkerStarted(time.monotonic(), device_path))
        with self._reader_factory(device_path) as reader:
            for event in reader.capture_events(
                duration=self.config.capture_duration,
                idle_timeout=self.config.idle_timeout,
                stop_event=self._disconnect_event,
            ):
                self._put_sample(event)
        if self._disconnect_event.is_set() and not self.stop_event.is_set():
            self._waiting_for_reconnect = True
            self._disconnect(self.board.address, self.board.adapter)
            self._put_control(WorkerDisconnected(time.monotonic()))

    def _run(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    self.run_once()
                    if self._waiting_for_reconnect and self.stop_event.wait(
                        self.config.retry_delay
                    ):
                        break
                except BalanceBoardNotFoundError as error:
                    if not self._waiting_for_reconnect:
                        self._put_control(WorkerError(time.monotonic(), str(error)))
                    if self.stop_event.wait(self.config.retry_delay):
                        break
                except BalanceBoardError as error:
                    self._put_control(WorkerError(time.monotonic(), str(error)))
                    if self.stop_event.wait(self.config.retry_delay):
                        break
                except Exception as error:
                    self._put_control(
                        WorkerError(time.monotonic(), f"fatal worker error: {error}")
                    )
                    break
        finally:
            self._put_control(WorkerStopped(time.monotonic()))

    def _put_sample(self, event: CapturedEvent) -> None:
        if event.corners is None:
            return
        message = WorkerSample(
            event.wall_time,
            SensorSample(event.monotonic_time, event.corners),
        )
        if not self.events.put_sample(message):
            self._dropped_samples += 1

    def _put_control(
        self,
        event: WorkerStarted | WorkerDisconnected | WorkerStopped | WorkerError,
    ) -> None:
        self._dropped_samples += self.events.put_control(event)
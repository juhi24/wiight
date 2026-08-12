"""Run blocking balance-board capture in a managed hardware thread."""

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
    connect_configured_balance_board,
    disconnect_configured_balance_board,
    find_configured_balance_board_path,
)
from wiight.measurement import SensorSample


@dataclass(frozen=True, slots=True)
class WorkerStarted:
    """Signal that capture opened a discovered board."""

    monotonic_time: float
    device_path: str


@dataclass(frozen=True, slots=True)
class WorkerStopped:
    """Signal that the hardware thread has exited."""

    monotonic_time: float


@dataclass(frozen=True, slots=True)
class WorkerDisconnected:
    """Signal an intentional disconnect while waiting for reconnection."""

    monotonic_time: float


@dataclass(frozen=True, slots=True)
class WorkerError:
    """Report a recoverable or fatal hardware worker failure."""

    monotonic_time: float
    message: str


@dataclass(frozen=True, slots=True)
class WorkerSample:
    """Carry a sensor sample with its corresponding wall-clock time."""

    wall_time: float
    sample: SensorSample


WorkerEvent = (
    WorkerStarted | WorkerDisconnected | WorkerStopped | WorkerError | WorkerSample
)


class WorkerMailbox:
    """Provide bounded, thread-safe delivery that prioritizes control events.

    Samples are rejected when full. A control event may evict the oldest queued
    sample so lifecycle information remains observable; control events themselves
    are never discarded.
    """

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("mailbox maxsize must be positive")
        self.maxsize = maxsize
        self._events: deque[WorkerEvent] = deque()
        self._condition = Condition()

    def put_sample(self, event: WorkerSample) -> bool:
        """Enqueue a sample if capacity is available."""

        with self._condition:
            if len(self._events) >= self.maxsize:
                return False
            self._events.append(event)
            self._condition.notify()
            return True

    def put_control(
        self, event: WorkerStarted | WorkerDisconnected | WorkerStopped | WorkerError
    ) -> int:
        """Enqueue a control event, evicting at most one sample if necessary.

        Returns:
            The number of samples evicted, either zero or one.
        """

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
        """Remove the oldest event, waiting up to ``timeout`` seconds.

        Raises:
            queue.Empty: If no event becomes available before the timeout.
        """

        with self._condition:
            if not self._condition.wait_for(lambda: bool(self._events), timeout):
                raise Empty
            return self._events.popleft()

    def get_nowait(self) -> WorkerEvent:
        """Remove the oldest event without waiting."""

        return self.get(timeout=0)

    def qsize(self) -> int:
        """Return the current number of queued events."""

        with self._condition:
            return len(self._events)


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """Configure mailbox capacity, capture bounds, and reconnect timing."""

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
    """Manage board discovery and event capture on a background thread."""

    def __init__(
        self,
        board: BoardConfig,
        *,
        config: WorkerConfig | None = None,
        event_queue: WorkerMailbox | None = None,
        stop_event: Event | None = None,
        discover: Callable[[str, str | None], str] = find_configured_balance_board_path,
        connect: Callable[
            [str, str | None], None
        ] = connect_configured_balance_board,
        disconnect: Callable[
            [str, str | None], None
        ] = disconnect_configured_balance_board,
        reader_factory: Callable[[str], BalanceBoardReader] = BalanceBoardReader,
    ) -> None:
        self.board = board
        self.config = config if config is not None else WorkerConfig()
        self.events = event_queue or WorkerMailbox(self.config.queue_size)
        self.stop_event = stop_event or Event()
        self._discover = discover
        self._connect = connect
        self._disconnect = disconnect
        self._reader_factory = reader_factory
        self._thread = Thread(target=self._run, name="wiight-hardware", daemon=True)
        self._disconnect_event = Event()
        self._waiting_for_reconnect = False
        self._dropped_samples = 0

    @property
    def dropped_samples(self) -> int:
        """Return the number of rejected or control-evicted samples."""

        return self._dropped_samples

    @property
    def is_alive(self) -> bool:
        """Return whether the hardware thread is running."""

        return self._thread.is_alive()

    def start(self) -> None:
        """Start the hardware thread."""

        self._thread.start()

    def disconnect(self) -> None:
        """Request capture cancellation and a BlueZ disconnect."""

        self._disconnect_event.set()

    def stop(self, timeout: float = 5.0) -> None:
        """Request shutdown and wait for the hardware thread.

        Raises:
            TimeoutError: If the thread remains alive after ``timeout`` seconds.
        """

        self.stop_event.set()
        self._disconnect_event.set()
        if self._thread.ident is not None:
            self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError("hardware worker did not stop within the deadline")

    def run_once(self) -> None:
        """Discover a board and run one bounded capture session.

        An explicit disconnect request closes capture, disconnects through BlueZ,
        and emits :class:`WorkerDisconnected`. A global stop only closes capture.
        """

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
                    if self._waiting_for_reconnect:
                        self._connect(self.board.address, self.board.adapter)
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
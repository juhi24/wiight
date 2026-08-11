from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from queue import Empty
from threading import Event
from typing import Protocol

from wiight.config import ServiceConfig
from wiight.measurement import MeasurementConfig, StableWeightDetector, TareCalibration
from wiight.mqtt import (
    PublishMessage,
    availability_message,
    discovery_message,
    status_message,
    weight_message,
)
from wiight.worker import (
    HardwareWorker,
    WorkerError,
    WorkerEvent,
    WorkerMailbox,
    WorkerSample,
    WorkerStarted,
    WorkerStopped,
)


class Publisher(Protocol):
    def publish(self, message: PublishMessage) -> None: ...


class Worker(Protocol):
    events: WorkerMailbox

    def start(self) -> None: ...

    def stop(self, timeout: float = 5.0) -> None: ...


class DaemonState(Enum):
    WAITING_FOR_BOARD = "waiting_for_board"
    MEASURING = "measuring"
    DEGRADED = "degraded"
    STOPPED = "stopped"


@dataclass(slots=True)
class DaemonEngine:
    config: ServiceConfig
    calibration: TareCalibration
    publisher: Publisher
    state: DaemonState = field(init=False)
    detector: StableWeightDetector = field(init=False)

    def __post_init__(self) -> None:
        settings = self.config.measurement
        self.state = DaemonState.WAITING_FOR_BOARD
        self.detector = StableWeightDetector(
            MeasurementConfig(
                minimum_weight_raw=settings.minimum_weight_centikilograms,
                stable_duration=settings.stable_duration_seconds,
                maximum_stddev_raw=settings.maximum_stddev_centikilograms,
                unload_threshold_raw=settings.unload_threshold_centikilograms,
            ),
            self.calibration,
        )

    def handle(self, event: WorkerEvent) -> None:
        if isinstance(event, WorkerStarted):
            self.state = DaemonState.MEASURING
            self._publish_status(board_connected=True)
        elif isinstance(event, WorkerSample):
            measurement = self.detector.add(event.sample)
            if measurement is not None:
                self.publisher.publish(
                    weight_message(
                        self.config.mqtt,
                        measurement,
                        measured_at=datetime.fromtimestamp(event.wall_time, UTC),
                    )
                )
        elif isinstance(event, WorkerError):
            self.state = DaemonState.DEGRADED
            self._publish_status(board_connected=False, error=event.message)
        elif isinstance(event, WorkerStopped):
            self.state = DaemonState.STOPPED
            self._publish_status(board_connected=False)

    def _publish_status(
        self, *, board_connected: bool, error: str | None = None
    ) -> None:
        self.publisher.publish(
            status_message(
                self.config.mqtt,
                state=self.state.value,
                board_connected=board_connected,
                calibrated=True,
                error=error,
            )
        )

    def publish_status(
        self, *, board_connected: bool, error: str | None = None
    ) -> None:
        self._publish_status(board_connected=board_connected, error=error)


def device_id(board_address: str) -> str:
    return f"wiight_{board_address.replace(':', '').lower()}"


@dataclass(slots=True)
class DaemonService:
    config: ServiceConfig
    calibration: TareCalibration
    publisher: Publisher
    worker: Worker | None = None
    engine: DaemonEngine = field(init=False)

    def __post_init__(self) -> None:
        self.engine = DaemonEngine(self.config, self.calibration, self.publisher)
        if self.worker is None:
            self.worker = HardwareWorker(self.config.board)

    def run(self, stop_event: Event) -> None:
        assert self.worker is not None
        identifier = device_id(self.config.board.address)
        self.publisher.publish(discovery_message(self.config.mqtt, identifier))
        self.publisher.publish(availability_message(self.config.mqtt, True))
        self.engine.publish_status(board_connected=False)
        self.worker.start()
        try:
            while not stop_event.is_set():
                try:
                    event = self.worker.events.get(timeout=0.25)
                except Empty:
                    continue
                self.engine.handle(event)
        finally:
            self.worker.stop()
            self._drain_events()
            self.publisher.publish(availability_message(self.config.mqtt, False))

    def _drain_events(self) -> None:
        assert self.worker is not None
        while True:
            try:
                event = self.worker.events.get_nowait()
            except Empty:
                return
            self.engine.handle(event)
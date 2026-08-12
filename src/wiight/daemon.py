"""Coordinate hardware events, stable measurements, and MQTT publication."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from queue import Empty
from threading import Event
from typing import Protocol

from wiight import bluezutils
from wiight.calibration import (
    CalibrationStoreError,
    store_calibration,
    zero_calibration,
)
from wiight.config import ServiceConfig
from wiight.measurement import (
    CalibrationConfig,
    CalibrationError,
    MeasurementConfig,
    SensorSample,
    StableWeightDetector,
    TareCalibration,
    compute_tare,
)
from wiight.mqtt import (
    Command,
    PairCommand,
    PublishMessage,
    TareCommand,
    availability_message,
    discovery_message,
    pairing_status_message,
    status_message,
    tare_status_message,
    weight_message,
)
from wiight.worker import (
    HardwareWorker,
    WorkerDisconnected,
    WorkerError,
    WorkerEvent,
    WorkerMailbox,
    WorkerSample,
    WorkerStarted,
    WorkerStopped,
)


class Publisher(Protocol):
    """Define the transport operations required by the daemon service."""

    def start(self) -> None:
        """Start the publisher."""
        ...

    def publish(self, message: PublishMessage) -> None:
        """Queue a transport-independent message for publication."""
        ...

    def get_command(self, timeout: float = 0.0) -> Command | None:
        """Return a pending service command, if available before the timeout."""
        ...

    def flush(self, timeout: float = 5.0) -> None:
        """Wait for pending publications to complete."""
        ...

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the publisher within the timeout."""
        ...


class Worker(Protocol):
    """Define the hardware worker operations required by the daemon."""

    events: WorkerMailbox

    def start(self) -> None:
        """Start hardware event capture."""
        ...

    def disconnect(self) -> None:
        """Request board disconnection after a measurement."""
        ...

    def stop(self, timeout: float = 5.0) -> None:
        """Stop hardware capture within the timeout."""
        ...


class Pairer(Protocol):
    """Define configured-board pairing required by the daemon."""

    def pair(
        self, address: str, adapter: str | None, *, timeout: float
    ) -> str:
        """Pair the board and return its BlueZ object path."""
        ...


class BlueZPairer:
    """Pair balance boards through the BlueZ implementation."""

    def pair(
        self, address: str, adapter: str | None, *, timeout: float
    ) -> str:
        """Pair a configured board through BlueZ."""

        return bluezutils.pair_balance_board(address, adapter, timeout=timeout)


class DaemonState(Enum):
    """Represent the service's current hardware lifecycle state."""

    WAITING_FOR_BOARD = "waiting_for_board"
    MEASURING = "measuring"
    DEGRADED = "degraded"
    STOPPED = "stopped"


@dataclass(slots=True)
class DaemonEngine:
    """Translate worker events into state and MQTT publications."""

    config: ServiceConfig
    calibration: TareCalibration | None
    publisher: Publisher
    state: DaemonState = field(init=False)
    detector: StableWeightDetector = field(init=False)
    calibrated: bool = field(init=False)

    def __post_init__(self) -> None:
        self.state = DaemonState.WAITING_FOR_BOARD
        self.calibrated = self.calibration is not None
        self.detector = self._create_detector(self.calibration or zero_calibration())

    def _create_detector(
        self, calibration: TareCalibration
    ) -> StableWeightDetector:
        settings = self.config.measurement
        self.detector = StableWeightDetector(
            MeasurementConfig(
                minimum_weight_raw=settings.minimum_weight_centikilograms,
                stable_duration=settings.stable_duration_seconds,
                maximum_stddev_raw=settings.maximum_stddev_centikilograms,
                unload_threshold_raw=settings.unload_threshold_centikilograms,
            ),
            calibration,
        )
        return self.detector

    def set_calibration(
        self, calibration: TareCalibration, *, board_connected: bool
    ) -> None:
        """Replace the active tare calibration and reset weight detection."""

        self.calibration = calibration
        self.calibrated = True
        self.detector = self._create_detector(calibration)
        self._publish_status(board_connected=board_connected)

    def handle(self, event: WorkerEvent) -> bool:
        """Process one worker event and publish resulting state.

        Returns:
            ``True`` only when a stable weight was published and the caller should
            request board disconnection.
        """

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
                return True
        elif isinstance(event, WorkerDisconnected):
            self.detector.reset()
            self.state = DaemonState.WAITING_FOR_BOARD
            self._publish_status(board_connected=False)
        elif isinstance(event, WorkerError):
            self.state = DaemonState.DEGRADED
            self._publish_status(board_connected=False, error=event.message)
        elif isinstance(event, WorkerStopped):
            self.state = DaemonState.STOPPED
            self._publish_status(board_connected=False)
        return False

    def _publish_status(
        self, *, board_connected: bool, error: str | None = None
    ) -> None:
        self.publisher.publish(
            status_message(
                self.config.mqtt,
                state=self.state.value,
                board_connected=board_connected,
                calibrated=self.calibrated,
                error=error,
            )
        )

    def publish_status(
        self, *, board_connected: bool, error: str | None = None
    ) -> None:
        """Publish the engine's current state and board status."""

        self._publish_status(board_connected=board_connected, error=error)


def device_id(board_address: str) -> str:
    """Return a stable Home Assistant identifier for a board address."""

    return f"wiight_{board_address.replace(':', '').lower()}"


@dataclass(slots=True)
class DaemonService:
    """Own publication, hardware, pairing, tare, and graceful shutdown."""

    config: ServiceConfig
    calibration: TareCalibration | None
    publisher: Publisher
    worker: Worker | None = None
    pairer: Pairer | None = None
    engine: DaemonEngine = field(init=False)
    tare_samples: list[SensorSample] | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.engine = DaemonEngine(self.config, self.calibration, self.publisher)
        if self.worker is None:
            self.worker = HardwareWorker(self.config.board)
        if self.pairer is None:
            self.pairer = BlueZPairer()

    def run(self, stop_event: Event) -> None:
        """Run the service until signaled and shut down owned resources.

        Startup publishes discovery, availability, status, pairing state, and tare
        state before hardware capture begins. Pair and tare commands are handled in
        the service thread. Each stable measurement or successful tare triggers a
        board disconnect.
        Shutdown stops the worker, drains lifecycle events, publishes retained offline
        availability, flushes it, and then stops the publisher.
        """

        assert self.worker is not None
        identifier = device_id(self.config.board.address)
        publisher_started = False
        worker_started = False
        try:
            self.publisher.start()
            publisher_started = True
            self.publisher.publish(discovery_message(self.config.mqtt, identifier))
            self.publisher.publish(availability_message(self.config.mqtt, True))
            self.engine.publish_status(board_connected=False)
            self.publisher.publish(
                pairing_status_message(self.config.mqtt, state="idle")
            )
            self.publisher.publish(tare_status_message(self.config.mqtt, state="idle"))
            self.worker.start()
            worker_started = True
            while not stop_event.is_set():
                command = self.publisher.get_command()
                if isinstance(command, PairCommand):
                    self._pair_board()
                elif isinstance(command, TareCommand):
                    self._start_tare()
                try:
                    event = self.worker.events.get(timeout=0.25)
                except Empty:
                    continue
                if self._handle_event(event):
                    self.worker.disconnect()
        finally:
            try:
                if worker_started:
                    self.worker.stop()
                    self._drain_events()
            finally:
                if publisher_started:
                    try:
                        self.publisher.publish(
                            availability_message(self.config.mqtt, False)
                        )
                        self.publisher.flush()
                    finally:
                        self.publisher.stop()

    def _pair_board(self) -> None:
        assert self.pairer is not None
        self.publisher.publish(
            pairing_status_message(self.config.mqtt, state="pairing")
        )
        try:
            self.pairer.pair(
                self.config.board.address,
                self.config.board.adapter,
                timeout=30.0,
            )
        except bluezutils.BlueZPairingError as error:
            self.publisher.publish(
                pairing_status_message(
                    self.config.mqtt, state="failed", error=str(error)
                )
            )
        else:
            self.publisher.publish(
                pairing_status_message(self.config.mqtt, state="paired")
            )

    def _start_tare(self) -> None:
        """Begin collecting worker samples and publish the taring state."""

        self.tare_samples = []
        self.publisher.publish(tare_status_message(self.config.mqtt, state="taring"))

    def _handle_event(self, event: WorkerEvent) -> bool:
        """Route an event through tare collection or normal measurement.

        Samples collected during tare are not passed to stable-weight detection.
        A worker disconnect, error, or stop aborts the active tare and reports it as
        failed before normal lifecycle handling continues.
        """

        if self.tare_samples is not None:
            if isinstance(event, WorkerSample):
                self.tare_samples.append(event.sample)
                if len(self.tare_samples) >= self.config.calibration.minimum_samples:
                    return self._finish_tare()
                return False
            if isinstance(event, (WorkerDisconnected, WorkerError, WorkerStopped)):
                self.tare_samples = None
                self.publisher.publish(
                    tare_status_message(
                        self.config.mqtt,
                        state="failed",
                        error="board disconnected during tare",
                    )
                )
        return self.engine.handle(event)

    def _finish_tare(self) -> bool:
        """Validate, atomically persist, and activate collected tare samples.

        Calibration or storage failures are published without replacing the active
        detector. A successful tare updates daemon calibration status immediately
        and requests board disconnection.
        """

        assert self.tare_samples is not None
        settings = self.config.calibration
        try:
            calibration = compute_tare(
                self.tare_samples,
                CalibrationConfig(
                    minimum_samples=settings.minimum_samples,
                    maximum_corner_stddev=(
                        settings.maximum_corner_stddev_centikilograms
                    ),
                ),
            )
            store_calibration(
                settings.path,
                self.config.board.address,
                calibration,
            )
        except (CalibrationError, CalibrationStoreError) as error:
            self.publisher.publish(
                tare_status_message(
                    self.config.mqtt, state="failed", error=str(error)
                )
            )
            return False
        else:
            self.engine.set_calibration(
                calibration,
                board_connected=self.engine.state is DaemonState.MEASURING,
            )
            self.publisher.publish(
                tare_status_message(self.config.mqtt, state="tared")
            )
            return True
        finally:
            self.tare_samples = None

    def _drain_events(self) -> None:
        assert self.worker is not None
        while True:
            try:
                event = self.worker.events.get_nowait()
            except Empty:
                return
            self.engine.handle(event)
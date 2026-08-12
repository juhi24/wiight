import json
from pathlib import Path
from threading import Event

from wiight import CornerReading, SensorSample, TareCalibration
from wiight import bluezutils
from wiight.config import (
    BoardConfig,
    CalibrationSettings,
    MqttConfig,
    ServiceConfig,
)
from wiight.daemon import DaemonEngine, DaemonService, DaemonState
from wiight.mqtt import Command, PairCommand, PublishMessage, TareCommand
from wiight.worker import (
    WorkerDisconnected,
    WorkerError,
    WorkerMailbox,
    WorkerSample,
    WorkerStarted,
    WorkerStopped,
)


class RecordingPublisher:
    def __init__(self) -> None:
        self.messages: list[PublishMessage] = []
        self.started = False
        self.stopped = False
        self.flushed = False
        self.commands: list[Command] = []

    def start(self) -> None:
        self.started = True

    def publish(self, message: PublishMessage) -> None:
        self.messages.append(message)

    def get_command(self, timeout: float = 0.0) -> Command | None:
        return self.commands.pop(0) if self.commands else None

    def flush(self, timeout: float = 5.0) -> None:
        self.flushed = True

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True


def engine() -> tuple[DaemonEngine, RecordingPublisher]:
    publisher = RecordingPublisher()
    config = ServiceConfig(
        board=BoardConfig("00:22:4C:60:0C:DB"),
        mqtt=MqttConfig(host="mqtt.local"),
    )
    return (
        DaemonEngine(
            config,
            TareCalibration(CornerReading(0, 0, 0, 0), 100, 0),
            publisher,
        ),
        publisher,
    )


def test_daemon_tracks_worker_lifecycle() -> None:
    daemon, publisher = engine()

    daemon.handle(WorkerStarted(0, "/device"))
    assert daemon.state is DaemonState.MEASURING
    assert json.loads(publisher.messages[-1].payload)["board_connected"] is True

    daemon.handle(WorkerDisconnected(0.5))
    assert daemon.state is DaemonState.WAITING_FOR_BOARD
    assert json.loads(publisher.messages[-1].payload)["board_connected"] is False

    daemon.handle(WorkerError(1, "lost board"))
    assert daemon.state is DaemonState.DEGRADED
    assert json.loads(publisher.messages[-1].payload)["error"] == "lost board"

    daemon.handle(WorkerStopped(2))
    assert daemon.state is DaemonState.STOPPED


def test_daemon_publishes_only_stable_weight() -> None:
    daemon, publisher = engine()
    daemon.handle(WorkerStarted(0, "/device"))

    for timestamp in (1.0, 2.0, 3.0):
        daemon.handle(
            WorkerSample(
                1_786_454_600 + timestamp,
                SensorSample(timestamp, CornerReading(250, 250, 250, 250)),
            )
        )

    weight_messages = [
        message for message in publisher.messages if message.topic.endswith("/weight")
    ]
    assert len(weight_messages) == 1
    assert json.loads(weight_messages[0].payload)["weight_kg"] == 10.0


def test_daemon_uses_zero_tare_and_reports_uncalibrated() -> None:
    publisher = RecordingPublisher()
    config = ServiceConfig(
        board=BoardConfig("00:22:4C:60:0C:DB"),
        mqtt=MqttConfig(host="mqtt.local"),
    )
    daemon = DaemonEngine(config, None, publisher)

    daemon.handle(WorkerStarted(0, "/device"))
    for timestamp in (1.0, 2.0, 3.0):
        daemon.handle(
            WorkerSample(
                1_786_454_600 + timestamp,
                SensorSample(timestamp, CornerReading(250, 250, 250, 250)),
            )
        )

    status = json.loads(publisher.messages[0].payload)
    weight = next(
        json.loads(message.payload)
        for message in publisher.messages
        if message.topic.endswith("/weight")
    )
    assert status["calibrated"] is False
    assert weight["weight_kg"] == 10.0


class FakeWorker:
    def __init__(self, stop_event: Event) -> None:
        self.events = WorkerMailbox(16)
        self.stop_event = stop_event
        self.started = False
        self.disconnected = False
        self.stopped = False

    def start(self) -> None:
        self.started = True
        self.events.put_control(WorkerStarted(0, "/device"))
        for timestamp in (1.0, 2.0, 3.0):
            self.events.put_sample(
                WorkerSample(
                    1_786_454_600 + timestamp,
                    SensorSample(
                        timestamp,
                        CornerReading(250, 250, 250, 250),
                    ),
                )
            )

    def disconnect(self) -> None:
        self.disconnected = True
        self.stop_event.set()

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True
        self.events.put_control(WorkerStopped(4))


class FakePairer:
    def __init__(self, stop_event: Event, error: str | None = None) -> None:
        self.stop_event = stop_event
        self.error = error
        self.calls: list[tuple[str, str | None, float]] = []

    def pair(
        self, address: str, adapter: str | None, *, timeout: float
    ) -> str:
        self.calls.append((address, adapter, timeout))
        self.stop_event.set()
        if self.error is not None:
            raise bluezutils.BlueZPairingError(self.error)
        return "/org/bluez/hci0/dev_00_22_4C_60_0C_DB"


def test_daemon_service_publishes_startup_measurement_and_offline_shutdown() -> None:
    config = ServiceConfig(
        board=BoardConfig("00:22:4C:60:0C:DB"),
        mqtt=MqttConfig(host="mqtt.local"),
    )
    calibration = TareCalibration(CornerReading(0, 0, 0, 0), 100, 0)
    publisher = RecordingPublisher()
    stop_event = Event()
    worker = FakeWorker(stop_event)
    service = DaemonService(config, calibration, publisher, worker)

    service.run(stop_event)

    assert worker.started and worker.disconnected and worker.stopped
    assert publisher.started and publisher.flushed and publisher.stopped
    assert publisher.messages[0].topic.endswith("/config")
    assert publisher.messages[1].payload == "online"
    assert any(message.topic.endswith("/weight") for message in publisher.messages)
    assert publisher.messages[-1].payload == "offline"
    assert publisher.messages[-1].retain


def test_daemon_service_handles_pair_command_and_publishes_result() -> None:
    config = ServiceConfig(
        board=BoardConfig("00:22:4C:60:0C:DB", adapter="hci0"),
        mqtt=MqttConfig(host="mqtt.local"),
    )
    publisher = RecordingPublisher()
    publisher.commands.append(PairCommand())
    stop_event = Event()
    worker = FakeWorker(Event())
    pairer = FakePairer(stop_event)
    service = DaemonService(config, None, publisher, worker, pairer)

    service.run(stop_event)

    assert pairer.calls == [("00:22:4C:60:0C:DB", "hci0", 30.0)]
    pairing_states = [
        json.loads(message.payload)["state"]
        for message in publisher.messages
        if message.topic.endswith("/pair/status")
    ]
    assert pairing_states == ["idle", "pairing", "paired"]


def test_daemon_service_tares_from_worker_samples(tmp_path: Path) -> None:
    calibration_path = tmp_path / "calibration.json"
    config = ServiceConfig(
        board=BoardConfig("00:22:4C:60:0C:DB"),
        mqtt=MqttConfig(host="mqtt.local"),
        calibration=CalibrationSettings(
            path=calibration_path,
            minimum_samples=3,
            maximum_corner_stddev_centikilograms=10,
        ),
    )
    publisher = RecordingPublisher()
    service = DaemonService(config, None, publisher, FakeWorker(Event()))
    service.engine.handle(WorkerStarted(0, "/device"))

    service._start_tare()
    for timestamp in (1.0, 2.0, 3.0):
        service._handle_event(
            WorkerSample(
                1_786_454_600 + timestamp,
                SensorSample(timestamp, CornerReading(25, 26, 27, 28)),
            )
        )

    stored = json.loads(calibration_path.read_text(encoding="utf-8"))
    tare_states = [
        json.loads(message.payload)["state"]
        for message in publisher.messages
        if message.topic.endswith("/tare/status")
    ]
    assert stored["offsets"] == [25.0, 26.0, 27.0, 28.0]
    assert service.engine.calibrated
    assert tare_states == ["taring", "tared"]
    assert not any(message.topic.endswith("/weight") for message in publisher.messages)


def test_daemon_service_reports_pair_failure_without_stopping_early() -> None:
    config = ServiceConfig(
        board=BoardConfig("00:22:4C:60:0C:DB"),
        mqtt=MqttConfig(host="mqtt.local"),
    )
    publisher = RecordingPublisher()
    publisher.commands.append(PairCommand())
    stop_event = Event()
    worker = FakeWorker(Event())
    service = DaemonService(
        config,
        None,
        publisher,
        worker,
        FakePairer(stop_event, "autopair plugin unavailable"),
    )

    service.run(stop_event)

    pairing = next(
        json.loads(message.payload)
        for message in publisher.messages
        if message.topic.endswith("/pair/status")
        and json.loads(message.payload)["state"] == "failed"
    )
    assert pairing["error"] == "autopair plugin unavailable"
    assert worker.stopped and publisher.stopped
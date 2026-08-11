import json
from threading import Event

from wiight import CornerReading, SensorSample, TareCalibration
from wiight.config import BoardConfig, MqttConfig, ServiceConfig
from wiight.daemon import DaemonEngine, DaemonService, DaemonState
from wiight.mqtt import PublishMessage
from wiight.worker import (
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

    def start(self) -> None:
        self.started = True

    def publish(self, message: PublishMessage) -> None:
        self.messages.append(message)

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
        self.stop_event.set()

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True
        self.events.put_control(WorkerStopped(4))


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

    assert worker.started and worker.stopped
    assert publisher.started and publisher.flushed and publisher.stopped
    assert publisher.messages[0].topic.endswith("/config")
    assert publisher.messages[1].payload == "online"
    assert any(message.topic.endswith("/weight") for message in publisher.messages)
    assert publisher.messages[-1].payload == "offline"
    assert publisher.messages[-1].retain
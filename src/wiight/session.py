from __future__ import annotations

from collections.abc import Iterable, Iterator

from wiight.config import CalibrationSettings, MeasurementSettings
from wiight.hardware import CapturedEvent
from wiight.measurement import (
    CalibrationConfig,
    MeasurementConfig,
    SensorSample,
    StableMeasurement,
    StableWeightDetector,
    TareCalibration,
    compute_tare,
)


class MeasurementTimeoutError(TimeoutError):
    pass


def sensor_samples(events: Iterable[CapturedEvent]) -> Iterator[SensorSample]:
    for event in events:
        if event.corners is not None:
            yield SensorSample(event.monotonic_time, event.corners)


def calculate_tare(
    events: Iterable[CapturedEvent], settings: CalibrationSettings
) -> TareCalibration:
    samples = []
    for sample in sensor_samples(events):
        samples.append(sample)
        if len(samples) == settings.minimum_samples:
            break

    return compute_tare(
        samples,
        CalibrationConfig(
            minimum_samples=settings.minimum_samples,
            maximum_corner_stddev=settings.maximum_corner_stddev_centikilograms,
        ),
    )


def stable_measurements(
    events: Iterable[CapturedEvent],
    calibration: TareCalibration,
    settings: MeasurementSettings,
) -> Iterator[StableMeasurement]:
    detector = StableWeightDetector(
        MeasurementConfig(
            minimum_weight_raw=settings.minimum_weight_centikilograms,
            stable_duration=settings.stable_duration_seconds,
            maximum_stddev_raw=settings.maximum_stddev_centikilograms,
            unload_threshold_raw=settings.unload_threshold_centikilograms,
        ),
        calibration,
    )
    for sample in sensor_samples(events):
        measurement = detector.add(sample)
        if measurement is not None:
            yield measurement


def measure_once(
    events: Iterable[CapturedEvent],
    calibration: TareCalibration,
    settings: MeasurementSettings,
) -> StableMeasurement:
    measurement = next(stable_measurements(events, calibration, settings), None)
    if measurement is None:
        raise MeasurementTimeoutError(
            "capture ended before a stable weight measurement was available"
        )
    return measurement
import pytest

import wiight
from wiight import CornerReading, TareCalibration
from wiight.config import CalibrationSettings, MeasurementSettings
from wiight.hardware import CapturedEvent
from wiight.session import (
    MeasurementTimeoutError,
    calculate_tare,
    measure_once,
    sensor_samples,
    stable_measurements,
)


def event(timestamp: float, total_per_corner: float) -> CapturedEvent:
    return CapturedEvent(
        wall_time=100 + timestamp,
        monotonic_time=timestamp,
        event_type=3,
        corners=CornerReading(*(total_per_corner for _ in range(4))),
    )


def lifecycle_event(timestamp: float) -> CapturedEvent:
    return CapturedEvent(
        wall_time=100 + timestamp,
        monotonic_time=timestamp,
        event_type=99,
    )


def test_package_exports_session_workflow() -> None:
    assert wiight.calculate_tare is calculate_tare
    assert wiight.measure_once is measure_once
    assert wiight.stable_measurements is stable_measurements


def test_sensor_samples_skip_non_sample_events() -> None:
    samples = list(sensor_samples([lifecycle_event(0), event(1, 10)]))

    assert len(samples) == 1
    assert samples[0].corners == CornerReading(10, 10, 10, 10)


def test_calculate_tare_uses_configured_sample_count() -> None:
    events = [event(1, 10), lifecycle_event(1.5), event(2, 12), event(3, 100)]

    calibration = calculate_tare(
        events,
        CalibrationSettings(
            minimum_samples=2,
            maximum_corner_stddev_centikilograms=2,
        ),
    )

    assert calibration.offsets == CornerReading(11, 11, 11, 11)
    assert calibration.sample_count == 2


def measurement_settings() -> MeasurementSettings:
    return MeasurementSettings(
        minimum_weight_centikilograms=100,
        stable_duration_seconds=2,
        maximum_stddev_centikilograms=2,
        unload_threshold_centikilograms=20,
    )


def zero_tare() -> TareCalibration:
    return TareCalibration(CornerReading(0, 0, 0, 0), 100, 0)


def test_measure_once_returns_first_stable_weight() -> None:
    measurement = measure_once(
        [event(1, 25), event(2, 25), event(3, 25)],
        zero_tare(),
        measurement_settings(),
    )

    assert measurement.raw_total == 100
    assert measurement.sample_count == 3


def test_stable_measurements_rearms_after_unload() -> None:
    measurements = list(
        stable_measurements(
            [
                event(1, 25),
                event(2, 25),
                event(3, 25),
                event(4, 0),
                event(5, 30),
                event(6, 30),
                event(7, 30),
            ],
            zero_tare(),
            measurement_settings(),
        )
    )

    assert [measurement.raw_total for measurement in measurements] == [100, 120]


def test_measure_once_reports_bounded_stream_timeout() -> None:
    with pytest.raises(MeasurementTimeoutError, match="capture ended"):
        measure_once(
            [event(1, 25), event(2, 25)],
            zero_tare(),
            measurement_settings(),
        )
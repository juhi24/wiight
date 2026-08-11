import pytest

import wiight
from wiight.measurement import (
    CalibrationConfig,
    CornerReading,
    InsufficientSamplesError,
    MeasurementConfig,
    SensorSample,
    StableWeightDetector,
    TareCalibration,
    UnstableCalibrationError,
    centikilograms_to_kilograms,
    compute_tare,
)


def sample(timestamp: float, corners: tuple[float, float, float, float]) -> SensorSample:
    return SensorSample(timestamp, CornerReading(*corners))


def test_package_exports_hardware_independent_api() -> None:
    assert wiight.CornerReading is CornerReading
    assert wiight.compute_tare is compute_tare


def test_centikilograms_convert_to_kilograms() -> None:
    assert centikilograms_to_kilograms(500) == 5


def test_corner_reading_preserves_canonical_order() -> None:
    reading = CornerReading(1, 2, 3, 4)

    assert tuple(reading) == (1, 2, 3, 4)
    assert reading.total == 10


def test_compute_tare_averages_each_corner_and_applies_offsets() -> None:
    calibration = compute_tare(
        [
            sample(1.0, (10, 20, 30, 40)),
            sample(2.0, (12, 22, 32, 42)),
            sample(3.0, (11, 21, 31, 41)),
        ],
        CalibrationConfig(minimum_samples=3, maximum_corner_stddev=2),
    )

    assert calibration.offsets == CornerReading(11, 21, 31, 41)
    assert calibration.sample_count == 3
    assert calibration.apply(CornerReading(16, 27, 38, 49)) == CornerReading(
        5, 6, 7, 8
    )


def test_compute_tare_rejects_too_few_samples() -> None:
    with pytest.raises(InsufficientSamplesError, match="received 1"):
        compute_tare(
            [sample(1.0, (10, 20, 30, 40))],
            CalibrationConfig(minimum_samples=2),
        )


def test_compute_tare_rejects_unstable_corner() -> None:
    with pytest.raises(UnstableCalibrationError, match="exceeds limit"):
        compute_tare(
            [
                sample(1.0, (0, 20, 30, 40)),
                sample(2.0, (100, 20, 30, 40)),
            ],
            CalibrationConfig(minimum_samples=2, maximum_corner_stddev=10),
        )


def test_calibration_config_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="minimum_samples"):
        CalibrationConfig(minimum_samples=1)

    with pytest.raises(ValueError, match="maximum_corner_stddev"):
        CalibrationConfig(maximum_corner_stddev=0)


def detector() -> StableWeightDetector:
    return StableWeightDetector(
        MeasurementConfig(
            minimum_weight_raw=100,
            stable_duration=2,
            maximum_stddev_raw=2,
            unload_threshold_raw=20,
        ),
        TareCalibration(CornerReading(10, 10, 10, 10), 10, 0),
    )


def test_detector_emits_stable_tared_weight_after_required_duration() -> None:
    weight_detector = detector()

    assert weight_detector.add(sample(0, (10, 10, 10, 10))) is None
    assert weight_detector.add(sample(1, (35, 35, 35, 35))) is None
    assert weight_detector.add(sample(2, (36, 35, 35, 35))) is None
    measurement = weight_detector.add(sample(3, (35, 35, 35, 35)))

    assert measurement is not None
    assert measurement.raw_total == pytest.approx(100 + 1 / 3)
    assert measurement.sample_count == 3
    assert measurement.monotonic_time == 3


def test_detector_rejects_motion_until_full_stable_duration() -> None:
    weight_detector = detector()

    assert weight_detector.add(sample(1, (35, 35, 35, 35))) is None
    assert weight_detector.add(sample(2, (60, 35, 35, 35))) is None
    assert weight_detector.add(sample(3, (35, 35, 35, 35))) is None
    assert weight_detector.add(sample(4, (35, 35, 35, 35))) is None
    assert weight_detector.add(sample(5, (35, 35, 35, 35))) is not None


def test_detector_handles_irregular_sample_cadence() -> None:
    weight_detector = detector()

    assert weight_detector.add(sample(1.0, (35, 35, 35, 35))) is None
    assert weight_detector.add(sample(2.1, (35, 35, 35, 35))) is None
    assert weight_detector.add(sample(3.2, (35, 35, 35, 35))) is not None


def test_detector_emits_only_once_until_board_is_unloaded() -> None:
    weight_detector = detector()

    for timestamp in (1, 2):
        assert weight_detector.add(sample(timestamp, (35, 35, 35, 35))) is None
    assert weight_detector.add(sample(3, (35, 35, 35, 35))) is not None
    assert weight_detector.add(sample(4, (35, 35, 35, 35))) is None
    assert weight_detector.add(sample(5, (12, 12, 12, 12))) is None
    assert weight_detector.add(sample(6, (35, 35, 35, 35))) is None
    assert weight_detector.add(sample(7, (35, 35, 35, 35))) is None
    assert weight_detector.add(sample(8, (35, 35, 35, 35))) is not None


def test_detector_rejects_non_increasing_timestamps() -> None:
    weight_detector = detector()
    weight_detector.add(sample(1, (35, 35, 35, 35)))

    with pytest.raises(ValueError, match="strictly increasing"):
        weight_detector.add(sample(1, (35, 35, 35, 35)))
"""Calibrate balance-board readings and detect stable weights."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Iterable

CENTIKILOGRAMS_PER_KILOGRAM = 100.0


def centikilograms_to_kilograms(value: float) -> float:
    """Convert a weight from centikilograms to kilograms."""

    return value / CENTIKILOGRAMS_PER_KILOGRAM


@dataclass(frozen=True, slots=True)
class CornerReading:
    """Four sensor values in canonical balance-board corner order."""

    top_left: float
    top_right: float
    bottom_right: float
    bottom_left: float

    def __iter__(self) -> Iterator[float]:
        """Iterate over values in canonical corner order."""

        return iter(
            (self.top_left, self.top_right, self.bottom_right, self.bottom_left)
        )

    @property
    def total(self) -> float:
        """Return the sum of all four corner values."""

        return sum(self)

    def subtract(self, offsets: CornerReading) -> CornerReading:
        """Return a reading with the corresponding offsets subtracted."""

        return CornerReading(
            top_left=self.top_left - offsets.top_left,
            top_right=self.top_right - offsets.top_right,
            bottom_right=self.bottom_right - offsets.bottom_right,
            bottom_left=self.bottom_left - offsets.bottom_left,
        )


@dataclass(frozen=True, slots=True)
class SensorSample:
    """Associate a monotonic timestamp with a four-corner reading."""

    monotonic_time: float
    corners: CornerReading


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    """Configure the sample count and noise limit used for taring."""

    minimum_samples: int = 10
    maximum_corner_stddev: float = 10.0

    def __post_init__(self) -> None:
        if self.minimum_samples < 2:
            raise ValueError("minimum_samples must be at least 2")
        if self.maximum_corner_stddev <= 0:
            raise ValueError("maximum_corner_stddev must be positive")


@dataclass(frozen=True, slots=True)
class TareCalibration:
    """Describe corner offsets derived from an unloaded board."""

    offsets: CornerReading
    sample_count: int
    maximum_corner_stddev: float

    def apply(self, reading: CornerReading) -> CornerReading:
        """Subtract the tare offsets from a corner reading."""

        return reading.subtract(self.offsets)


@dataclass(frozen=True, slots=True)
class MeasurementConfig:
    """Configure stable-weight detection in raw centikilogram units."""

    minimum_weight_raw: float
    stable_duration: float
    maximum_stddev_raw: float
    unload_threshold_raw: float

    def __post_init__(self) -> None:
        if self.minimum_weight_raw <= 0:
            raise ValueError("minimum_weight_raw must be positive")
        if self.stable_duration <= 0:
            raise ValueError("stable_duration must be positive")
        if self.maximum_stddev_raw <= 0:
            raise ValueError("maximum_stddev_raw must be positive")
        if not 0 <= self.unload_threshold_raw < self.minimum_weight_raw:
            raise ValueError(
                "unload_threshold_raw must be non-negative and less than "
                "minimum_weight_raw"
            )


@dataclass(frozen=True, slots=True)
class StableMeasurement:
    """Describe a stable weight window in raw centikilogram units."""

    raw_total: float
    raw_stddev: float
    monotonic_time: float
    sample_count: int


class CalibrationError(ValueError):
    """Base class for calibration failures."""


class InsufficientSamplesError(CalibrationError):
    """Raised when too few samples are available for calibration."""


class UnstableCalibrationError(CalibrationError):
    """Raised when calibration samples exceed the permitted noise."""


class StableWeightDetector:
    """Detect stable weight windows and require unloading between results."""

    def __init__(
        self, config: MeasurementConfig, calibration: TareCalibration
    ) -> None:
        self._config = config
        self._calibration = calibration
        self._window: deque[tuple[float, float]] = deque()
        self._waiting_for_unload = False
        self._last_sample_time: float | None = None

    def add(self, sample: SensorSample) -> StableMeasurement | None:
        """Add a sample and return a newly completed stable measurement.

        Args:
            sample: Reading whose timestamp must be later than the previous sample.

        Returns:
            A stable measurement when the configured window completes, otherwise
            ``None``. After producing a result, the detector returns ``None`` until
            the board is unloaded below the configured threshold.

        Raises:
            ValueError: If sample timestamps are not strictly increasing.
        """

        if (
            self._last_sample_time is not None
            and sample.monotonic_time <= self._last_sample_time
        ):
            raise ValueError("sample timestamps must be strictly increasing")
        self._last_sample_time = sample.monotonic_time

        raw_total = self._calibration.apply(sample.corners).total
        if self._waiting_for_unload:
            if raw_total <= self._config.unload_threshold_raw:
                self._waiting_for_unload = False
                self._window.clear()
            return None

        if raw_total < self._config.minimum_weight_raw:
            self._window.clear()
            return None

        self._window.append((sample.monotonic_time, raw_total))
        window_start = sample.monotonic_time - self._config.stable_duration
        while len(self._window) > 1 and self._window[1][0] <= window_start:
            self._window.popleft()

        if self._window[-1][0] - self._window[0][0] < self._config.stable_duration:
            return None

        totals = tuple(total for _, total in self._window)
        raw_stddev = pstdev(totals)
        if raw_stddev > self._config.maximum_stddev_raw:
            return None

        self._waiting_for_unload = True
        return StableMeasurement(
            raw_total=fmean(totals),
            raw_stddev=raw_stddev,
            monotonic_time=sample.monotonic_time,
            sample_count=len(totals),
        )

    def reset(self) -> None:
        """Discard all samples and return to the initial unloaded state."""

        self._window.clear()
        self._waiting_for_unload = False
        self._last_sample_time = None


def compute_tare(
    samples: Iterable[SensorSample],
    config: CalibrationConfig = CalibrationConfig(),
) -> TareCalibration:
    """Compute corner offsets from unloaded-board samples.

    Args:
        samples: Unloaded-board readings to aggregate.
        config: Required sample count and maximum per-corner noise.

    Returns:
        Calibration containing mean offsets and observed maximum standard deviation.

    Raises:
        InsufficientSamplesError: If fewer than the configured number of samples are
            supplied.
        UnstableCalibrationError: If any corner exceeds the configured population
            standard deviation.
    """

    collected = tuple(samples)
    if len(collected) < config.minimum_samples:
        raise InsufficientSamplesError(
            f"calibration requires at least {config.minimum_samples} samples; "
            f"received {len(collected)}"
        )

    corner_columns = tuple(zip(*(sample.corners for sample in collected), strict=True))
    offsets = CornerReading(*(fmean(column) for column in corner_columns))
    corner_stddevs = tuple(pstdev(column) for column in corner_columns)
    maximum_stddev = max(corner_stddevs)

    if maximum_stddev > config.maximum_corner_stddev:
        raise UnstableCalibrationError(
            f"maximum corner standard deviation {maximum_stddev:.3f} exceeds "
            f"limit {config.maximum_corner_stddev:.3f}"
        )

    return TareCalibration(
        offsets=offsets,
        sample_count=len(collected),
        maximum_corner_stddev=maximum_stddev,
    )
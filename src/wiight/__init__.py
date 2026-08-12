# SPDX-FileCopyrightText: 2024-present Jussi Tiira <jussi@j24.fi>
#
# SPDX-License-Identifier: MIT

"""Hardware-independent public API for Wii Balance Board measurements."""

from wiight.measurement import (
	CENTIKILOGRAMS_PER_KILOGRAM,
	CalibrationConfig,
	CalibrationError,
	CornerReading,
	InsufficientSamplesError,
	MeasurementConfig,
	SensorSample,
	StableMeasurement,
	StableWeightDetector,
	TareCalibration,
	UnstableCalibrationError,
	centikilograms_to_kilograms,
	compute_tare,
)
from wiight.session import (
	MeasurementTimeoutError,
	calculate_tare,
	measure_once,
	sensor_samples,
	stable_measurements,
)

__all__ = [
	"CENTIKILOGRAMS_PER_KILOGRAM",
	"CalibrationConfig",
	"CalibrationError",
	"CornerReading",
	"InsufficientSamplesError",
	"MeasurementConfig",
	"MeasurementTimeoutError",
	"SensorSample",
	"StableMeasurement",
	"StableWeightDetector",
	"TareCalibration",
	"UnstableCalibrationError",
	"calculate_tare",
	"centikilograms_to_kilograms",
	"compute_tare",
	"measure_once",
	"sensor_samples",
	"stable_measurements",
]

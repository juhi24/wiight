"""Persist versioned, board-bound tare calibrations."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wiight.measurement import CornerReading, TareCalibration

SCHEMA_VERSION = 1
CORNER_ORDER = ("top_left", "top_right", "bottom_right", "bottom_left")
UNIT = "centikilogram"


class CalibrationStoreError(ValueError):
    """Raised when persisted calibration cannot be validated or accessed."""


@dataclass(frozen=True, slots=True)
class StoredCalibration:
    """Combine tare data with its board identity and creation time."""

    board_address: str
    created_at: datetime
    calibration: TareCalibration

    def as_dict(self) -> dict[str, Any]:
        """Return the versioned JSON-compatible storage representation."""

        return {
            "schema_version": SCHEMA_VERSION,
            "board_address": self.board_address.upper(),
            "created_at": self.created_at.astimezone(UTC).isoformat().replace(
                "+00:00", "Z"
            ),
            "unit": UNIT,
            "corner_order": list(CORNER_ORDER),
            "offsets": list(self.calibration.offsets),
            "sample_count": self.calibration.sample_count,
            "maximum_corner_stddev": self.calibration.maximum_corner_stddev,
        }


def zero_calibration() -> TareCalibration:
    """Return a calibration that leaves all corner readings unchanged."""

    return TareCalibration(
        offsets=CornerReading(0, 0, 0, 0),
        sample_count=0,
        maximum_corner_stddev=0,
    )


def _validate_for_storage(
    board_address: str, calibration: TareCalibration, created_at: datetime
) -> str:
    normalized_address = board_address.upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", normalized_address):
        raise CalibrationStoreError("board address must be a Bluetooth MAC address")
    if created_at.tzinfo is None:
        raise CalibrationStoreError("calibration timestamp must include a timezone")
    if calibration.sample_count < 2:
        raise CalibrationStoreError("calibration sample_count must be at least 2")
    if calibration.maximum_corner_stddev < 0:
        raise CalibrationStoreError(
            "calibration maximum_corner_stddev must be non-negative"
        )
    return normalized_address


def store_calibration(
    path: Path,
    board_address: str,
    calibration: TareCalibration,
    *,
    created_at: datetime | None = None,
) -> StoredCalibration:
    """Validate and atomically persist a tare calibration.

    The file and its containing directory are synchronized before this function
    returns, and an existing calibration is replaced only after the new document
    has been written successfully.

    Args:
        path: Destination JSON file.
        board_address: Bluetooth MAC address owning the calibration.
        calibration: Tare values to persist.
        created_at: Timezone-aware creation time, or the current UTC time by default.

    Returns:
        The normalized calibration record written to disk.

    Raises:
        CalibrationStoreError: If values are invalid or the file cannot be written.
    """

    timestamp = created_at or datetime.now(UTC)
    normalized_address = _validate_for_storage(
        board_address, calibration, timestamp
    )
    stored = StoredCalibration(
        board_address=normalized_address,
        created_at=timestamp,
        calibration=calibration,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(stored.as_dict(), temporary_file, separators=(",", ":"))
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        raise CalibrationStoreError(f"could not write calibration file {path}: {error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return stored


def load_calibration(path: Path, board_address: str) -> StoredCalibration:
    """Load and validate calibration for a specific board.

    Args:
        path: Calibration JSON file.
        board_address: Bluetooth MAC address expected to own the calibration.

    Returns:
        A validated record normalized to uppercase address and UTC time.

    Raises:
        CalibrationStoreError: If the file is absent, inaccessible, malformed,
            incompatible, or belongs to another board.
    """

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CalibrationStoreError(f"calibration file not found: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise CalibrationStoreError(f"invalid calibration file {path}: {error}") from error

    if not isinstance(data, dict):
        raise CalibrationStoreError("calibration document must be a JSON object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CalibrationStoreError("unsupported calibration schema version")
    if data.get("unit") != UNIT:
        raise CalibrationStoreError(f"calibration unit must be {UNIT}")
    if data.get("corner_order") != list(CORNER_ORDER):
        raise CalibrationStoreError("calibration corner order is invalid")

    stored_address = data.get("board_address")
    if not isinstance(stored_address, str):
        raise CalibrationStoreError("calibration board address is invalid")
    if stored_address.casefold() != board_address.casefold():
        raise CalibrationStoreError(
            f"calibration belongs to board {stored_address}, not {board_address}"
        )

    try:
        offsets = data["offsets"]
        if not isinstance(offsets, list) or len(offsets) != 4:
            raise TypeError("offsets must contain four values")
        created_at = datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            raise TypeError("created_at must include a timezone")
        calibration = TareCalibration(
            offsets=CornerReading(*(float(value) for value in offsets)),
            sample_count=int(data["sample_count"]),
            maximum_corner_stddev=float(data["maximum_corner_stddev"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CalibrationStoreError(f"invalid calibration values: {error}") from error

    if calibration.sample_count < 2:
        raise CalibrationStoreError("calibration sample_count must be at least 2")
    if calibration.maximum_corner_stddev < 0:
        raise CalibrationStoreError(
            "calibration maximum_corner_stddev must be non-negative"
        )

    return StoredCalibration(
        board_address=stored_address.upper(),
        created_at=created_at.astimezone(UTC),
        calibration=calibration,
    )


def load_optional_calibration(
    path: Path, board_address: str
) -> StoredCalibration | None:
    """Load calibration when present while rejecting invalid existing files.

    Returns:
        ``None`` only when the path does not exist; otherwise a validated record.

    Raises:
        CalibrationStoreError: If an existing file cannot be loaded or validated.
    """

    try:
        return load_calibration(path, board_address)
    except CalibrationStoreError as error:
        if isinstance(error.__cause__, FileNotFoundError):
            return None
        raise
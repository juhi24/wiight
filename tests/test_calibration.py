import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wiight import CornerReading, TareCalibration
from wiight.calibration import (
    CalibrationStoreError,
    load_calibration,
    store_calibration,
)


BOARD_ADDRESS = "00:22:4C:60:0C:DB"


def calibration() -> TareCalibration:
    return TareCalibration(
        offsets=CornerReading(1, 2, 3, 4),
        sample_count=100,
        maximum_corner_stddev=2.5,
    )


def test_store_and_load_calibration_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state" / "calibration.json"
    created_at = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)

    store_calibration(path, BOARD_ADDRESS, calibration(), created_at=created_at)
    loaded = load_calibration(path, BOARD_ADDRESS.lower())

    assert loaded.board_address == BOARD_ADDRESS
    assert loaded.created_at == created_at
    assert loaded.calibration == calibration()
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["corner_order"] == [
        "top_left",
        "top_right",
        "bottom_right",
        "bottom_left",
    ]
    assert not list(path.parent.glob("*.tmp"))


def test_load_calibration_rejects_other_board(tmp_path: Path) -> None:
    path = tmp_path / "calibration.json"
    store_calibration(path, BOARD_ADDRESS, calibration())

    with pytest.raises(CalibrationStoreError, match="belongs to board"):
        load_calibration(path, "AA:BB:CC:DD:EE:FF")


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("schema_version", 2, "schema version"),
        ("unit", "kilogram", "unit"),
        ("corner_order", ["wrong"], "corner order"),
    ],
)
def test_load_calibration_rejects_incompatible_metadata(
    tmp_path: Path, field: str, value, message: str
) -> None:
    path = tmp_path / "calibration.json"
    store_calibration(path, BOARD_ADDRESS, calibration())
    document = json.loads(path.read_text(encoding="utf-8"))
    document[field] = value
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CalibrationStoreError, match=message):
        load_calibration(path, BOARD_ADDRESS)


def test_load_calibration_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "calibration.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(CalibrationStoreError, match="invalid calibration file"):
        load_calibration(path, BOARD_ADDRESS)


def test_store_calibration_rejects_naive_timestamp(tmp_path: Path) -> None:
    with pytest.raises(CalibrationStoreError, match="timezone"):
        store_calibration(
            tmp_path / "calibration.json",
            BOARD_ADDRESS,
            calibration(),
            created_at=datetime(2026, 8, 11),
        )


def test_store_calibration_cleans_temporary_file_on_replace_failure(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "calibration.json"

    def fail_replace(source, destination) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("wiight.calibration.os.replace", fail_replace)

    with pytest.raises(CalibrationStoreError, match="replace failed"):
        store_calibration(path, BOARD_ADDRESS, calibration())

    assert not path.exists()
    assert not list(tmp_path.glob("*.tmp"))
from __future__ import annotations

import importlib
import select
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from math import ceil
from typing import Any

from wiight.measurement import CornerReading


class BalanceBoardError(RuntimeError):
    pass


class BalanceBoardNotFoundError(BalanceBoardError):
    pass


class CaptureIdleTimeoutError(BalanceBoardError):
    pass


@dataclass(frozen=True, slots=True)
class CapturedEvent:
    wall_time: float
    monotonic_time: float
    event_type: int
    corners: CornerReading | None = None

    def as_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": 1,
            "type": "sample" if self.corners is not None else "xwiimote_event",
            "wall_time": self.wall_time,
            "monotonic_time": self.monotonic_time,
            "event_type": self.event_type,
        }
        if self.corners is not None:
            record["corners_centikilograms"] = list(self.corners)
            record["total_centikilograms"] = self.corners.total
        return record


def _xwiimote_module():
    try:
        return importlib.import_module("xwiimote")
    except ImportError as error:
        raise BalanceBoardError(
            "the xwiimote Python binding is not available"
        ) from error


def find_balance_board_path() -> str:
    xwiimote = _xwiimote_module()
    monitor = xwiimote.monitor(True, True)
    matches = []
    path = monitor.poll()
    while path is not None:
        if xwiimote.iface(path).get_devtype() == "balanceboard":
            matches.append(path)
        path = monitor.poll()

    if not matches:
        raise BalanceBoardNotFoundError("no connected balance board found")
    if len(matches) > 1:
        raise BalanceBoardNotFoundError(
            "multiple connected balance boards found; specify --device"
        )
    return matches[0]


@contextmanager
def open_balance_board(device_path: str | None = None) -> Iterator[Any]:
    xwiimote = _xwiimote_module()
    interface = xwiimote.iface(device_path or find_balance_board_path())
    if interface.get_devtype() != "balanceboard":
        raise BalanceBoardNotFoundError("selected device is not a balance board")

    interface.open(xwiimote.IFACE_BALANCE_BOARD)
    try:
        yield interface
    finally:
        interface.close(xwiimote.IFACE_BALANCE_BOARD)


def _corner_reading(event: Any) -> CornerReading:
    return CornerReading(
        top_left=event.get_abs(2)[0],
        top_right=event.get_abs(0)[0],
        bottom_right=event.get_abs(1)[0],
        bottom_left=event.get_abs(3)[0],
    )


def capture_events(
    interface: Any,
    *,
    duration: float,
    idle_timeout: float,
) -> Iterator[CapturedEvent]:
    xwiimote = _xwiimote_module()
    poller = select.poll()
    file_descriptor = interface.get_fd()
    poller.register(file_descriptor, select.POLLIN)
    deadline = time.monotonic() + duration

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            wait_seconds = min(remaining, idle_timeout)
            if not poller.poll(max(1, ceil(wait_seconds * 1000))):
                if time.monotonic() >= deadline:
                    return
                raise CaptureIdleTimeoutError(
                    f"no xwiimote events received for {idle_timeout:g} seconds"
                )

            event = xwiimote.event()
            interface.dispatch(event)
            monotonic_time = time.monotonic()
            corners = (
                _corner_reading(event)
                if event.type == xwiimote.EVENT_BALANCE_BOARD
                else None
            )
            yield CapturedEvent(
                wall_time=time.time(),
                monotonic_time=monotonic_time,
                event_type=event.type,
                corners=corners,
            )
    finally:
        poller.unregister(file_descriptor)
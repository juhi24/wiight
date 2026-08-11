from __future__ import annotations

import importlib
import select
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from math import ceil
from threading import Event
from typing import Any

from wiight import bluezutils
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
    matches = _find_balance_board_paths()
    if not matches:
        raise BalanceBoardNotFoundError("no connected balance board found")
    if len(matches) > 1:
        raise BalanceBoardNotFoundError(
            "multiple connected balance boards found; specify --device"
        )
    return matches[0]


def _find_balance_board_paths() -> list[str]:
    xwiimote = _xwiimote_module()
    monitor = xwiimote.monitor(True, True)
    matches: list[str] = []
    path = monitor.poll()
    while path is not None:
        if xwiimote.iface(path).get_devtype() == "balanceboard":
            matches.append(path)
        path = monitor.poll()
    return matches


def find_configured_balance_board_path(
    board_address: str,
    adapter_pattern: str | None = None,
    bus: Any | None = None,
) -> str:
    try:
        objects = bluezutils.get_managed_objects(bus)
        bluezutils.find_connected_device_path(
            objects, board_address, adapter_pattern
        )
    except bluezutils.BlueZLookupError as error:
        raise BalanceBoardNotFoundError(str(error)) from error
    except (ImportError, OSError) as error:
        raise BalanceBoardError(f"could not query BlueZ: {error}") from error

    matches = _find_balance_board_paths()
    if not matches:
        raise BalanceBoardNotFoundError(
            f"configured board {board_address} is connected in BlueZ but not available "
            "through xwiimote"
        )
    if len(matches) > 1:
        raise BalanceBoardNotFoundError(
            "multiple xwiimote balance boards are connected; specify --device"
        )
    return matches[0]


def disconnect_configured_balance_board(
    board_address: str,
    adapter_pattern: str | None = None,
) -> None:
    try:
        bluezutils.disconnect_device(board_address, adapter_pattern)
    except bluezutils.BlueZLookupError as error:
        raise BalanceBoardError(str(error)) from error
    except (ImportError, OSError) as error:
        raise BalanceBoardError(f"could not query BlueZ: {error}") from error


class BalanceBoardReader:
    def __init__(self, device_path: str) -> None:
        self.device_path = device_path
        self._interface: Any | None = None
        self._interface_mask: int | None = None

    @property
    def is_open(self) -> bool:
        return self._interface is not None

    @property
    def interface(self) -> Any:
        if self._interface is None:
            raise BalanceBoardError("balance board reader is not open")
        return self._interface

    def __enter__(self) -> BalanceBoardReader:
        if self._interface is not None:
            raise BalanceBoardError("balance board reader is already open")
        xwiimote = _xwiimote_module()
        interface = xwiimote.iface(self.device_path)
        if interface.get_devtype() != "balanceboard":
            raise BalanceBoardNotFoundError("selected device is not a balance board")
        interface_mask = xwiimote.IFACE_BALANCE_BOARD
        interface.open(interface_mask)
        self._interface = interface
        self._interface_mask = interface_mask
        return self

    def __exit__(self, *args: object) -> None:
        interface = self._interface
        interface_mask = self._interface_mask
        self._interface = None
        self._interface_mask = None
        if interface is not None and interface_mask is not None:
            interface.close(interface_mask)

    def capture_events(
        self,
        *,
        duration: float,
        idle_timeout: float,
        stop_event: Event | None = None,
    ) -> Generator[CapturedEvent, None, None]:
        yield from capture_events(
            self.interface,
            duration=duration,
            idle_timeout=idle_timeout,
            stop_event=stop_event,
        )


@contextmanager
def open_balance_board(device_path: str | None = None) -> Iterator[Any]:
    with BalanceBoardReader(device_path or find_balance_board_path()) as reader:
        yield reader.interface


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
    stop_event: Event | None = None,
) -> Generator[CapturedEvent, None, None]:
    xwiimote = _xwiimote_module()
    poller = select.poll()
    file_descriptor = interface.get_fd()
    poller.register(file_descriptor, select.POLLIN)
    started_at = time.monotonic()
    deadline = started_at + duration
    idle_deadline = started_at + idle_timeout

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                return
            idle_remaining = idle_deadline - now
            if idle_remaining <= 0:
                raise CaptureIdleTimeoutError(
                    f"no xwiimote events received for {idle_timeout:g} seconds"
                )
            wait_seconds = min(
                remaining,
                idle_remaining,
                0.25 if stop_event is not None else idle_timeout,
            )
            if not poller.poll(max(1, ceil(wait_seconds * 1000))):
                if stop_event is not None and stop_event.is_set():
                    return
                now = time.monotonic()
                if now >= deadline:
                    return
                if now >= idle_deadline:
                    raise CaptureIdleTimeoutError(
                        f"no xwiimote events received for {idle_timeout:g} seconds"
                    )
                continue

            event = xwiimote.event()
            interface.dispatch(event)
            monotonic_time = time.monotonic()
            idle_deadline = monotonic_time + idle_timeout
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
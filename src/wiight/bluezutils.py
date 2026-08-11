from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

SERVICE_NAME = "org.bluez"
ADAPTER_INTERFACE = SERVICE_NAME + ".Adapter1"
DEVICE_INTERFACE = SERVICE_NAME + ".Device1"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
HID_SERVICE_UUID = "00001124-0000-1000-8000-00805f9b34fb"

ManagedObjects = Mapping[str, Mapping[str, Mapping[str, Any]]]


class BlueZLookupError(LookupError):
    pass


class AdapterNotFoundError(BlueZLookupError):
    pass


class DeviceNotFoundError(BlueZLookupError):
    pass


class DeviceNotConnectedError(BlueZLookupError):
    pass


class BlueZUnavailableError(BlueZLookupError):
    pass


class BlueZPairingError(BlueZLookupError):
    pass


def _dbus_module():
    import dbus  # type: ignore[import-untyped]

    return dbus


def _system_bus():
    return _dbus_module().SystemBus()


def get_managed_objects(bus=None) -> ManagedObjects:
    try:
        if bus is None:
            bus = _system_bus()
        manager = _dbus_module().Interface(
            bus.get_object(SERVICE_NAME, "/"), OBJECT_MANAGER_INTERFACE
        )
        return manager.GetManagedObjects()
    except Exception as error:
        raise BlueZUnavailableError(f"could not query BlueZ: {error}") from error


def find_adapter_path(objects: ManagedObjects, pattern: str | None = None) -> str:
    for path, interfaces in objects.items():
        adapter = interfaces.get(ADAPTER_INTERFACE)
        if adapter is None:
            continue
        address = str(adapter.get("Address", ""))
        if pattern is None or pattern.casefold() == address.casefold() or path.endswith(
            pattern
        ):
            return path
    raise AdapterNotFoundError("Bluetooth adapter not found")


def find_device_path(
    objects: ManagedObjects,
    device_address: str,
    adapter_pattern: str | None = None,
) -> str:
    adapter_path = (
        find_adapter_path(objects, adapter_pattern) if adapter_pattern else None
    )
    for path, interfaces in objects.items():
        device = interfaces.get(DEVICE_INTERFACE)
        if device is None:
            continue
        address = str(device.get("Address", ""))
        if address.casefold() != device_address.casefold():
            continue
        if adapter_path is None or path.startswith(adapter_path + "/"):
            return path
    raise DeviceNotFoundError(f"Bluetooth device {device_address} not found")


def find_connected_device_path(
    objects: ManagedObjects,
    device_address: str,
    adapter_pattern: str | None = None,
) -> str:
    path = find_device_path(objects, device_address, adapter_pattern)
    device = objects[path][DEVICE_INTERFACE]
    if not bool(device.get("Connected", False)):
        raise DeviceNotConnectedError(
            f"Bluetooth device {device_address} is not connected"
        )
    return path


def find_adapter(pattern: str | None = None, bus=None):
    if bus is None:
        bus = _system_bus()
    return find_adapter_in_objects(get_managed_objects(bus), pattern, bus)


def find_adapter_in_objects(
    objects: ManagedObjects, pattern: str | None = None, bus=None
):
    if bus is None:
        bus = _system_bus()
    path = find_adapter_path(objects, pattern)
    obj = bus.get_object(SERVICE_NAME, path)
    return _dbus_module().Interface(obj, ADAPTER_INTERFACE)


def find_device(
    device_address: str, adapter_pattern: str | None = None, bus=None
):
    if bus is None:
        bus = _system_bus()
    return find_device_in_objects(
        get_managed_objects(bus), device_address, adapter_pattern, bus
    )


def disconnect_device(
    device_address: str, adapter_pattern: str | None = None, bus=None
) -> None:
    try:
        bus = bus or _system_bus()
        device = find_device_in_objects(
            get_managed_objects(bus), device_address, adapter_pattern, bus
        )
        device.Disconnect()
    except BlueZLookupError:
        raise
    except Exception as error:
        raise BlueZUnavailableError(
            f"could not disconnect Bluetooth device {device_address}: {error}"
        ) from error


def find_device_in_objects(
    objects: ManagedObjects,
    device_address: str,
    adapter_pattern: str | None = None,
    bus=None,
):
    if bus is None:
        bus = _system_bus()
    path = find_device_path(objects, device_address, adapter_pattern)
    obj = bus.get_object(SERVICE_NAME, path)
    return _dbus_module().Interface(obj, DEVICE_INTERFACE)


def pair_balance_board(
    device_address: str,
    adapter_pattern: str | None = None,
    *,
    timeout: float = 30.0,
    bus=None,
) -> str:
    if timeout <= 0:
        raise ValueError("pairing timeout must be positive")
    deadline = time.monotonic() + timeout
    try:
        bus = bus or _system_bus()
        objects = get_managed_objects(bus)
        adapter_path = find_adapter_path(objects, adapter_pattern)
        adapter = find_adapter_in_objects(objects, adapter_pattern, bus)
        device_path = _discover_device_path(
            bus, adapter, device_address, adapter_path, deadline
        )
        # Nintendo's binary BDADDR PIN must be supplied by BlueZ's autopair plugin.
        _pair_device(bus, device_path, deadline)
        return device_path
    except BlueZPairingError:
        raise
    except BlueZLookupError as error:
        raise BlueZPairingError(str(error)) from error
    except Exception as error:
        raise BlueZPairingError(f"could not pair balance board: {error}") from error


def _discover_device_path(
    bus: Any,
    adapter: Any,
    device_address: str,
    adapter_path: str,
    deadline: float,
) -> str:
    discovery_started = False
    try:
        try:
            adapter.StartDiscovery()
            discovery_started = True
        except Exception as error:
            if _dbus_error_name(error) != "org.bluez.Error.InProgress":
                raise

        while time.monotonic() < deadline:
            objects = get_managed_objects(bus)
            try:
                path = find_device_path(objects, device_address)
            except DeviceNotFoundError:
                time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
                continue
            if path.startswith(adapter_path + "/"):
                return path
            raise BlueZPairingError(
                f"Bluetooth device {device_address} was found on a different adapter"
            )
        raise BlueZPairingError(
            f"timed out discovering Bluetooth device {device_address}; "
            "press the balance board sync button and try again"
        )
    finally:
        if discovery_started:
            try:
                adapter.StopDiscovery()
            except _dbus_module().DBusException:
                pass


def _pair_device(bus: Any, device_path: str, deadline: float) -> None:
    dbus = _dbus_module()
    device_object = bus.get_object(SERVICE_NAME, device_path)
    device = dbus.Interface(device_object, DEVICE_INTERFACE)
    properties = dbus.Interface(device_object, PROPERTIES_INTERFACE)
    paired = bool(properties.Get(DEVICE_INTERFACE, "Paired"))
    if not paired:
        device.Pair(timeout=_remaining(deadline))
    properties.Set(DEVICE_INTERFACE, "Trusted", dbus.Boolean(True))
    connected = bool(properties.Get(DEVICE_INTERFACE, "Connected"))
    # Pair() exposes its ACL as connected before the Wii HID channels are open.
    if not paired or not connected:
        device.ConnectProfile(HID_SERVICE_UUID, timeout=_remaining(deadline))


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BlueZPairingError("balance board pairing timed out")
    return remaining


def _dbus_error_name(error: Exception) -> str | None:
    getter = getattr(error, "get_dbus_name", None)
    return getter() if getter is not None else None

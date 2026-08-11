from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SERVICE_NAME = "org.bluez"
ADAPTER_INTERFACE = SERVICE_NAME + ".Adapter1"
DEVICE_INTERFACE = SERVICE_NAME + ".Device1"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"

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

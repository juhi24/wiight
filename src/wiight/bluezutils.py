from __future__ import annotations

import importlib
import time
from collections.abc import Mapping
from contextlib import contextmanager
from threading import Thread
from typing import Any

SERVICE_NAME = "org.bluez"
ADAPTER_INTERFACE = SERVICE_NAME + ".Adapter1"
DEVICE_INTERFACE = SERVICE_NAME + ".Device1"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
AGENT_INTERFACE = SERVICE_NAME + ".Agent1"
AGENT_MANAGER_INTERFACE = SERVICE_NAME + ".AgentManager1"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
PAIRING_AGENT_PATH = "/org/wiight/pairing_agent"

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


def _pairing_bus():
    mainloop = importlib.import_module("dbus.mainloop.glib")
    return _dbus_module().SystemBus(mainloop=mainloop.DBusGMainLoop())


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
        bus = bus or _pairing_bus()
        objects = get_managed_objects(bus)
        adapter_path = find_adapter_path(objects, adapter_pattern)
        adapter = find_adapter_in_objects(objects, adapter_pattern, bus)
        device_path = _discover_device_path(
            bus, adapter, device_address, adapter_path, deadline
        )
        with _pairing_agent(bus, device_path):
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


@contextmanager
def _pairing_agent(bus: Any, device_path: str):
    dbus: Any = _dbus_module()
    service: Any = importlib.import_module("dbus.service")
    glib = importlib.import_module("gi.repository.GLib")

    class Rejected(dbus.DBusException):
        _dbus_error_name = "org.bluez.Error.Rejected"

    class PairingAgent(service.Object):
        def _require_device(self, requested_path: str) -> None:
            if requested_path != device_path:
                raise Rejected("pairing is restricted to the configured balance board")

        @service.method(AGENT_INTERFACE, in_signature="", out_signature="")
        def Release(self) -> None:
            pass

        @service.method(AGENT_INTERFACE, in_signature="o", out_signature="s")
        def RequestPinCode(self, requested_path: str) -> str:
            self._require_device(requested_path)
            raise Rejected(
                "BlueZ requested an interactive PIN; ensure its autopair plugin "
                "is enabled for Nintendo RVL-WBC-01 devices"
            )

        @service.method(AGENT_INTERFACE, in_signature="o", out_signature="u")
        def RequestPasskey(self, requested_path: str) -> int:
            self._require_device(requested_path)
            raise Rejected("interactive passkeys are not supported")

        @service.method(AGENT_INTERFACE, in_signature="ou", out_signature="")
        def RequestConfirmation(self, requested_path: str, passkey: int) -> None:
            self._require_device(requested_path)

        @service.method(AGENT_INTERFACE, in_signature="o", out_signature="")
        def RequestAuthorization(self, requested_path: str) -> None:
            self._require_device(requested_path)

        @service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
        def AuthorizeService(self, requested_path: str, uuid: str) -> None:
            self._require_device(requested_path)

        @service.method(AGENT_INTERFACE, in_signature="", out_signature="")
        def Cancel(self) -> None:
            pass

    agent = PairingAgent(bus, PAIRING_AGENT_PATH)
    manager_object = bus.get_object(SERVICE_NAME, "/org/bluez")
    manager = dbus.Interface(manager_object, AGENT_MANAGER_INTERFACE)
    loop = glib.MainLoop()
    loop_thread = Thread(target=loop.run, name="wiight-pairing-agent", daemon=True)
    loop_thread.start()
    registered = False
    try:
        manager.RegisterAgent(PAIRING_AGENT_PATH, "NoInputNoOutput")
        registered = True
        yield
    finally:
        if registered:
            try:
                manager.UnregisterAgent(PAIRING_AGENT_PATH)
            except dbus.DBusException:
                pass
        agent.remove_from_connection()
        loop.quit()
        loop_thread.join(1.0)


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
    if not connected:
        device.Connect(timeout=_remaining(deadline))


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BlueZPairingError("balance board pairing timed out")
    return remaining


def _dbus_error_name(error: Exception) -> str | None:
    getter = getattr(error, "get_dbus_name", None)
    return getter() if getter is not None else None

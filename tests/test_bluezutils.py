from wiight import bluezutils


OBJECTS = {
    "/org/bluez/hci0": {
        bluezutils.ADAPTER_INTERFACE: {"Address": "AA:AA:AA:AA:AA:AA"}
    },
    "/org/bluez/hci1": {
        bluezutils.ADAPTER_INTERFACE: {"Address": "BB:BB:BB:BB:BB:BB"}
    },
    "/org/bluez/hci0/dev_11_22_33_44_55_66": {
        bluezutils.DEVICE_INTERFACE: {
            "Address": "11:22:33:44:55:66",
            "Connected": False,
        }
    },
    "/org/bluez/hci1/dev_11_22_33_44_55_66": {
        bluezutils.DEVICE_INTERFACE: {
            "Address": "11:22:33:44:55:66",
            "Connected": 1,
        }
    },
    "/org/bluez/hci0/unrelated": {"org.example.Other": {}},
}


def test_find_adapter_path_matches_address_or_path_suffix() -> None:
    assert (
        bluezutils.find_adapter_path(OBJECTS, "bb:bb:bb:bb:bb:bb")
        == "/org/bluez/hci1"
    )
    assert bluezutils.find_adapter_path(OBJECTS, "hci0") == "/org/bluez/hci0"


def test_find_device_path_can_be_scoped_to_adapter() -> None:
    assert (
        bluezutils.find_device_path(OBJECTS, "11:22:33:44:55:66", "hci1")
        == "/org/bluez/hci1/dev_11_22_33_44_55_66"
    )


def test_find_connected_device_path_requires_connected_configured_device() -> None:
    assert (
        bluezutils.find_connected_device_path(
            OBJECTS, "11:22:33:44:55:66", "hci1"
        )
        == "/org/bluez/hci1/dev_11_22_33_44_55_66"
    )

    try:
        bluezutils.find_connected_device_path(
            OBJECTS, "11:22:33:44:55:66", "hci0"
        )
    except bluezutils.DeviceNotConnectedError as error:
        assert "not connected" in str(error)
    else:
        raise AssertionError("disconnected device should be rejected")


def test_find_paths_raise_specific_errors() -> None:
    try:
        bluezutils.find_adapter_path(OBJECTS, "hci9")
    except bluezutils.AdapterNotFoundError:
        pass
    else:
        raise AssertionError("missing adapter should raise AdapterNotFoundError")

    try:
        bluezutils.find_device_path(OBJECTS, "00:00:00:00:00:00")
    except bluezutils.DeviceNotFoundError as error:
        assert "00:00:00:00:00:00" in str(error)
    else:
        raise AssertionError("missing device should raise DeviceNotFoundError")


def test_get_managed_objects_wraps_bluez_failure(monkeypatch) -> None:
    class FakeDbusModule:
        @staticmethod
        def Interface(obj, interface):
            return obj

    class BrokenBus:
        def get_object(self, service: str, path: str):
            raise OSError("system bus unavailable")

    monkeypatch.setattr(
        bluezutils,
        "_dbus_module",
        FakeDbusModule,
    )

    try:
        bluezutils.get_managed_objects(BrokenBus())
    except bluezutils.BlueZUnavailableError as error:
        assert "system bus unavailable" in str(error)
    else:
        raise AssertionError("BlueZ failure should be wrapped")


def test_discover_device_is_bounded_and_stops_discovery(monkeypatch) -> None:
    class Adapter:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        def StartDiscovery(self) -> None:
            self.started = True

        def StopDiscovery(self) -> None:
            self.stopped = True

    adapter = Adapter()
    times = iter((0.0, 0.0, 0.25, 0.25))
    monkeypatch.setattr(bluezutils.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(bluezutils.time, "sleep", lambda duration: None)
    monkeypatch.setattr(bluezutils, "get_managed_objects", lambda bus: {})

    try:
        bluezutils._discover_device_path(
            object(), adapter, "11:22:33:44:55:66", "/org/bluez/hci0", 0.25
        )
    except bluezutils.BlueZPairingError as error:
        assert "sync button" in str(error)
    else:
        raise AssertionError("undiscovered board should time out")

    assert adapter.started and adapter.stopped
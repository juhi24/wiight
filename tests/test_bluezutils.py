from wiight import bluezutils


OBJECTS: bluezutils.ManagedObjects = {
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


class FakeDevice:
    def __init__(self, *, paired: bool, connected: bool) -> None:
        self.properties = {"Paired": paired, "Connected": connected}
        self.calls: list[tuple] = []
        self.interface_requests: list[str] = []

    def Pair(self, *, timeout: float) -> None:
        self.calls.append(("Pair", timeout))

    def ConnectProfile(self, uuid: str, *, timeout: float) -> None:
        self.calls.append(("ConnectProfile", uuid, timeout))

    def Disconnect(self) -> None:
        self.calls.append(("Disconnect",))

    def Get(self, interface: str, name: str) -> object:
        self.calls.append(("Get", interface, name))
        return self.properties[name]

    def Set(self, interface: str, name: str, value: object) -> None:
        self.calls.append(("Set", interface, name, value))


class FakeDbusModule:
    class DBusException(Exception):
        pass

    @staticmethod
    def Interface(obj, interface: str):
        obj.interface_requests.append(interface)
        return obj

    @staticmethod
    def Boolean(value: bool) -> bool:
        return value


class FakeBus:
    def __init__(self, device: FakeDevice) -> None:
        self.device = device
        self.paths: list[str] = []

    def get_object(self, service: str, path: str):
        assert service == bluezutils.SERVICE_NAME
        self.paths.append(path)
        if path == "/org/bluez/hci0/dev_11_22_33_44_55_66":
            self.device.interface_requests = []
            return self.device
        raise AssertionError(f"unexpected D-Bus object path: {path}")


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


def test_disconnect_device_calls_configured_bluez_device(monkeypatch) -> None:
    device = FakeDevice(paired=True, connected=True)
    bus = FakeBus(device)
    monkeypatch.setattr(bluezutils, "get_managed_objects", lambda current_bus: OBJECTS)
    monkeypatch.setattr(bluezutils, "_dbus_module", lambda: FakeDbusModule)

    bluezutils.disconnect_device("11:22:33:44:55:66", "hci0", bus)

    assert device.calls == [("Disconnect",)]


def test_connect_device_profile_calls_configured_bluez_device(monkeypatch) -> None:
    device = FakeDevice(paired=True, connected=False)
    bus = FakeBus(device)
    monkeypatch.setattr(bluezutils, "get_managed_objects", lambda current_bus: OBJECTS)
    monkeypatch.setattr(bluezutils, "_dbus_module", lambda: FakeDbusModule)

    bluezutils.connect_device_profile(
        "11:22:33:44:55:66",
        bluezutils.HID_SERVICE_UUID,
        "hci0",
        timeout=4.0,
        bus=bus,
    )

    assert device.calls == [
        ("ConnectProfile", bluezutils.HID_SERVICE_UUID, 4.0)
    ]


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


def test_pair_balance_board_discovers_and_pairs_without_agent(monkeypatch) -> None:
    device = FakeDevice(paired=False, connected=False)
    bus = FakeBus(device)
    calls: list[tuple[object, str, str, float]] = []

    def discover(
        current_bus: object,
        adapter: object,
        address: str,
        adapter_path: str,
        deadline: float,
    ) -> str:
        calls.append((current_bus, address, adapter_path, deadline))
        return "/org/bluez/hci0/dev_11_22_33_44_55_66"

    monkeypatch.setattr(bluezutils.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(bluezutils, "get_managed_objects", lambda current_bus: OBJECTS)
    monkeypatch.setattr(
        bluezutils,
        "find_adapter_in_objects",
        lambda objects, pattern, current_bus: object(),
    )
    monkeypatch.setattr(
        bluezutils,
        "_discover_device_path",
        discover,
    )
    monkeypatch.setattr(bluezutils, "_dbus_module", lambda: FakeDbusModule)

    path = bluezutils.pair_balance_board(
        "11:22:33:44:55:66", "hci0", timeout=30, bus=bus
    )

    assert path == "/org/bluez/hci0/dev_11_22_33_44_55_66"
    assert calls == [(bus, "11:22:33:44:55:66", "/org/bluez/hci0", 40.0)]
    assert all("agent" not in path.casefold() for path in bus.paths)
    assert device.calls == [
        ("Get", bluezutils.DEVICE_INTERFACE, "Paired"),
        ("Pair", 30.0),
        ("Set", bluezutils.DEVICE_INTERFACE, "Trusted", True),
        ("Get", bluezutils.DEVICE_INTERFACE, "Connected"),
        ("ConnectProfile", bluezutils.HID_SERVICE_UUID, 30.0),
    ]


def test_newly_paired_device_connects_hid_even_while_acl_is_connected(
    monkeypatch,
) -> None:
    device = FakeDevice(paired=False, connected=True)
    bus = FakeBus(device)
    monkeypatch.setattr(bluezutils.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(bluezutils, "_dbus_module", lambda: FakeDbusModule)

    bluezutils._pair_device(
        bus, "/org/bluez/hci0/dev_11_22_33_44_55_66", 40.0
    )

    assert ("Pair", 30.0) in device.calls
    assert (
        "ConnectProfile",
        bluezutils.HID_SERVICE_UUID,
        30.0,
    ) in device.calls


def test_paired_disconnected_device_connects_hid_profile(monkeypatch) -> None:
    device = FakeDevice(paired=True, connected=False)
    bus = FakeBus(device)
    monkeypatch.setattr(bluezutils.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(bluezutils, "_dbus_module", lambda: FakeDbusModule)

    bluezutils._pair_device(
        bus, "/org/bluez/hci0/dev_11_22_33_44_55_66", 40.0
    )

    assert ("Pair", 30.0) not in device.calls
    assert device.calls[-1] == (
        "ConnectProfile",
        bluezutils.HID_SERVICE_UUID,
        30.0,
    )


def test_pair_device_skips_pair_and_connect_when_already_active(monkeypatch) -> None:
    device = FakeDevice(paired=True, connected=True)
    bus = FakeBus(device)
    monkeypatch.setattr(bluezutils, "_dbus_module", lambda: FakeDbusModule)

    bluezutils._pair_device(
        bus, "/org/bluez/hci0/dev_11_22_33_44_55_66", 30.0
    )

    assert device.calls == [
        ("Get", bluezutils.DEVICE_INTERFACE, "Paired"),
        ("Set", bluezutils.DEVICE_INTERFACE, "Trusted", True),
        ("Get", bluezutils.DEVICE_INTERFACE, "Connected"),
    ]


def test_pair_balance_board_wraps_bluez_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        bluezutils,
        "get_managed_objects",
        lambda bus: (_ for _ in ()).throw(OSError("bluetoothd unavailable")),
    )

    try:
        bluezutils.pair_balance_board(
            "11:22:33:44:55:66", timeout=30, bus=object()
        )
    except bluezutils.BlueZPairingError as error:
        assert "bluetoothd unavailable" in str(error)
    else:
        raise AssertionError("BlueZ pairing failure should be wrapped")

import wiight.bluezutils


OBJECTS = {
    "/org/bluez/hci0": {
        wiight.bluezutils.ADAPTER_INTERFACE: {"Address": "AA:AA:AA:AA:AA:AA"}
    },
    "/org/bluez/hci1": {
        wiight.bluezutils.ADAPTER_INTERFACE: {"Address": "BB:BB:BB:BB:BB:BB"}
    },
    "/org/bluez/hci0/dev_11_22_33_44_55_66": {
        wiight.bluezutils.DEVICE_INTERFACE: {"Address": "11:22:33:44:55:66"}
    },
    "/org/bluez/hci1/dev_11_22_33_44_55_66": {
        wiight.bluezutils.DEVICE_INTERFACE: {"Address": "11:22:33:44:55:66"}
    },
    "/org/bluez/hci0/unrelated": {"org.example.Other": {}},
}


def test_find_adapter_path_matches_address_or_path_suffix() -> None:
    assert (
        wiight.bluezutils.find_adapter_path(OBJECTS, "bb:bb:bb:bb:bb:bb")
        == "/org/bluez/hci1"
    )
    assert wiight.bluezutils.find_adapter_path(OBJECTS, "hci0") == "/org/bluez/hci0"


def test_find_device_path_can_be_scoped_to_adapter() -> None:
    assert (
        wiight.bluezutils.find_device_path(OBJECTS, "11:22:33:44:55:66", "hci1")
        == "/org/bluez/hci1/dev_11_22_33_44_55_66"
    )


def test_find_paths_raise_specific_errors() -> None:
    try:
        wiight.bluezutils.find_adapter_path(OBJECTS, "hci9")
    except wiight.bluezutils.AdapterNotFoundError:
        pass
    else:
        raise AssertionError("missing adapter should raise AdapterNotFoundError")

    try:
        wiight.bluezutils.find_device_path(OBJECTS, "00:00:00:00:00:00")
    except wiight.bluezutils.DeviceNotFoundError as error:
        assert "00:00:00:00:00:00" in str(error)
    else:
        raise AssertionError("missing device should raise DeviceNotFoundError")
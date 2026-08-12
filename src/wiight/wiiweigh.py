"""Provide the legacy D-Bus and xwiimote measurement workflow."""

import importlib
import logging
import select
import time
from functools import partial

import click
import numpy as np

from wiight.bluezutils import find_adapter, find_device
from wiight.measurement import (
    CornerReading,
    SensorSample,
    centikilograms_to_kilograms,
    compute_tare,
)

bbaddress = None

logger = logging.getLogger(__name__)


def _xwiimote_module():
    """Import and return the optional xwiimote binding."""

    return importlib.import_module("xwiimote")


# from https://github.com/irq0/wiiscale/blob/master/scale.py
class RingBuffer:
    """Store a fixed-size rolling window for the legacy averaging flow."""

    def __init__(self, length):
        """Initialize an empty buffer with ``length`` entries."""

        self.length = length
        self.reset()
        self.filled = False

    def extend(self, x):
        """Append all values from a NumPy array, wrapping as needed."""

        x_index = (self.index + np.arange(x.size)) % self.data.size
        self.data[x_index] = x
        self.index = x_index[-1] + 1
        if self.filled == False and self.index == (self.length-1):
            self.filled = True

    def append(self, x):
        """Append one value, overwriting the oldest entry when full."""

        x_index = (self.index + 1) % self.data.size
        self.data[x_index] = x
        self.index = x_index
        if self.filled == False and self.index == (self.length-1):
            self.filled = True

    def get(self):
        """Return buffered values in rolling order."""

        idx = (self.index + np.arange(self.data.size)) %self.data.size
        return self.data[idx]

    def reset(self):
        """Clear values and reset the write position."""

        self.data = np.zeros(self.length, dtype=int)
        self.index = 0


def dev_is_balanceboard(dev):
    """Return whether a newly connected xwiimote device is a balance board."""

    time.sleep(2) # if we check the devtype to early it is reported as 'unknown' :(
    xwiimote = _xwiimote_module()
    iface = xwiimote.iface(dev)
    return iface.get_devtype() == 'balanceboard'


def wait_for_balanceboard():
    """Block until xwiimote reports a newly connected balance board."""

    xwiimote = _xwiimote_module()
    print("Waiting for balanceboard to connect..")
    mon = xwiimote.monitor(True, False)
    dev = None
    while True:
        mon.get_fd(True) # blocks
        connected = mon.poll()
        if connected == None:
            continue
        elif dev_is_balanceboard(connected):
            print("Found balanceboard:", connected)
            dev = connected
            break
        else:
            print("Found non-balanceboard device:", connected)
            print("Waiting..")
    return dev


def corner_reading_from_event(event) -> CornerReading:
    """Map a legacy xwiimote event into canonical corner order."""

    return CornerReading(
        top_left=event.get_abs(2)[0],
        top_right=event.get_abs(0)[0],
        bottom_right=event.get_abs(1)[0],
        bottom_left=event.get_abs(3)[0],
    )


def measurements(iface, calibration=(0,0,0,0)):
    """Yield calibrated corner tuples from balance-board events indefinitely."""

    xwiimote = _xwiimote_module()
    offsets = CornerReading(*calibration)
    poller = select.poll()
    poller.register(iface.get_fd(), select.POLLIN)
    while True:
        poller.poll() # blocks
        event = xwiimote.event()
        iface.dispatch(event)
        if event.type != xwiimote.EVENT_BALANCE_BOARD:
            continue
        reading = corner_reading_from_event(event).subtract(offsets)
        logger.debug(reading.total)
        yield tuple(reading)
            

def calibrate(iface):
    """Calculate tare offsets from ten unloaded-board readings."""

    print("Calibrating balanceboard..")
    readings = measurements(iface)
    samples = [
        SensorSample(time.monotonic(), CornerReading(*next(readings)))
        for _ in range(10)
    ]
    calibration = compute_tare(samples)
    print("Calibration done.")
    return tuple(calibration.offsets)


def average_measurements(ms, window_size=800, max_stddev=10, min_weight=10, 
                        max_measurements=5000):
    """Return median weight and dispersion once a legacy window is stable.

    Returns zero values when stability is not reached within ``max_measurements``.
    """

    last_measurements = RingBuffer(window_size)
    counter = 0
    while True:
        weight = sum(next(ms))
        last_measurements.append(weight)
        median = np.median(last_measurements.data)
        stddev = np.std(last_measurements.data)
        if stddev < max_stddev and last_measurements.filled and median > min_weight:
            return np.array((median, stddev))
        if counter > max_measurements:
            return np.array((0, 0))
        counter = counter + 1

    
def find_device_address(bus):
    """Return the first registered Nintendo balance-board address, if any."""

    dbus = importlib.import_module("dbus")

    adapter = find_adapter(bus=bus)
    adapter_path = adapter.object_path
    om = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
    objects = om.GetManagedObjects()
    # find FIRST registered or connected Wii Balance Board ("RVL-WBC-01") and return address
    for interfaces in objects.values():
        if "org.bluez.Device1" not in interfaces:
            continue
        properties = interfaces["org.bluez.Device1"]
        if properties["Adapter"] != adapter_path:
            continue
        if properties["Alias"] != "Nintendo RVL-WBC-01":
            continue
        logger.info("found Wii Balanceboard with address %s", properties["Address"])
        return properties["Address"]


def connect_balanceboard(bus):
    """Connect, tare, measure once, and disconnect through the legacy flow."""

    global bbaddress
    xwiimote = _xwiimote_module()
    #device is something like "/sys/devices/platform/soc/3f201000.uart/tty/ttyAMA0/hci0/hci0:11/0005:057E:0306.000C"
    device = wait_for_balanceboard()
    iface = xwiimote.iface(device)
    iface.open(xwiimote.IFACE_BALANCE_BOARD)
    calibration = calibrate(iface)
    (kg, std) = average_measurements(measurements(iface, calibration))
    # do something with this data
    # like log to file or send to server
    print(
        f"{centikilograms_to_kilograms(kg):.2f} +/- "
        f"{centikilograms_to_kilograms(std):.2f}"
    )
    # find address of the balance board (once) and disconnect (if found).
    if bbaddress is None:
        bbaddress = find_device_address(bus)
    if bbaddress is not None:
        device = find_device(bbaddress, bus=bus)
        device.Disconnect()


def property_changed(interface, changed, invalidated, path, bus=None):
    """Handle legacy BlueZ property changes and measure new connections."""

    iface = interface[interface.rfind(".") + 1:]
    for name, value in changed.items():
        val = str(value)
        logger.info("{%s.PropertyChanged} [%s] %s = %s", iface, path, name, val)
        # check if property "Connected" changed to "1". Does NOT check which device has connected, we only assume it was the balance board
        if name == "Connected" and val == "1":
            connect_balanceboard(bus)


@click.command()
def main():
    """Run the legacy GLib connection-monitoring service."""

    dbus = importlib.import_module("dbus")
    dbus_glib = importlib.import_module("dbus.mainloop.glib")

    try:
        GObject = importlib.import_module("gi.repository").GObject
    except ImportError:
        GObject = importlib.import_module("gobject")

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger.debug("Starting")
    dbus_glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    # bluetooth (dis)connection triggers PropertiesChanged signal
    logger.debug("Adding signal receiver")
    bus.add_signal_receiver(partial(property_changed, bus=bus), bus_name="org.bluez",
            dbus_interface="org.freedesktop.DBus.Properties",
            signal_name="PropertiesChanged",
            path_keyword="path")
    try:
        logger.debug("Running mainloop")
        mainloop = GObject.MainLoop()
        mainloop.run()
    except KeyboardInterrupt:
        mainloop.quit()
        print("Bye!")


if __name__ == '__main__':
    main()

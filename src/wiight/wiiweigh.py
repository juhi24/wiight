#!/usr/bin/python
# -*- coding: utf-8 -*-

import sys
import time
import select
import numpy as np
import xwiimote
import fnmatch
import os
import bisect
from functools import partial

from wiight.bluezutils import find_adapter, find_device

import dbus
import dbus.mainloop.glib
import click
try:
  from gi.repository import GObject
except ImportError:
  import gobject as GObject
import logging

relevant_ifaces = [ "org.bluez.Adapter1", "org.bluez.Device1" ]
bbaddress = None

# Configure logger
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Create logger
logger = logging.getLogger(__name__)

# Log a message
logger.info('Logging initialized')


# from https://github.com/irq0/wiiscale/blob/master/scale.py
class RingBuffer():
    def __init__(self, length):
        self.length = length
        self.reset()
        self.filled = False

    def extend(self, x):
        x_index = (self.index + np.arange(x.size)) % self.data.size
        self.data[x_index] = x
        self.index = x_index[-1] + 1
        if self.filled == False and self.index == (self.length-1):
            self.filled = True

    def append(self, x):
        x_index = (self.index + 1) % self.data.size
        self.data[x_index] = x
        self.index = x_index
        if self.filled == False and self.index == (self.length-1):
            self.filled = True

    def get(self):
        idx = (self.index + np.arange(self.data.size)) %self.data.size
        return self.data[idx]

    def reset(self):
        self.data = np.zeros(self.length, dtype=int)
        self.index = 0


def dev_is_balanceboard(dev):
    time.sleep(2) # if we check the devtype to early it is reported as 'unknown' :(
    iface = xwiimote.iface(dev)
    return iface.get_devtype() == 'balanceboard'


def wait_for_balanceboard():
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


def measurements(iface, calibration=(0,0,0,0)):
    p = select.epoll.fromfd(iface.get_fd())
    while True:
        p.poll() # blocks
        event = xwiimote.event()
        iface.dispatch(event)
        tl = event.get_abs(2)[0] - calibration[0]
        tr = event.get_abs(0)[0] - calibration[2]
        br = event.get_abs(3)[0] - calibration[1]
        bl = event.get_abs(1)[0] - calibration[3]
        logging.debug(sum((tl,tr,br,bl)))
        yield (tl,tr,br,bl)
            

def calibrate(iface):
    """Calibrate empty balance board"""
    print("Calibrating balanceboard..")
    calibration = [0,0,0,0]
    for i in range(10):
        p = select.epoll.fromfd(iface.get_fd())
        while True:
            p.poll() # blocks
            event = xwiimote.event()
            iface.dispatch(event)
            calibration[0] += event.get_abs(2)[0]
            calibration[1] += event.get_abs(3)[0]
            calibration[2] += event.get_abs(0)[0]
            calibration[3] += event.get_abs(1)[0]
        calibration = [x/10 for x in calibration]
    print("Calibration done.")
    return calibration


def average_measurements(ms, window_size=800, max_stddev=10, min_weight=10, 
                        max_measurements=5000):
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
    adapter = find_adapter()
    adapter_path = adapter.object_path
    om = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
    objects = om.GetManagedObjects()
    # find FIRST registered or connected Wii Balance Board ("RVL-WBC-01") and return address
    for path, interfaces in objects.items():
        if "org.bluez.Device1" not in interfaces:
            continue
        properties = interfaces["org.bluez.Device1"]
        if properties["Adapter"] != adapter_path:
            continue
        if properties["Alias"] != "Nintendo RVL-WBC-01":
            continue
        logger.info("found Wii Balanceboard with address %s" % (properties["Address"]))
        return properties["Address"]


def connect_balanceboard(bus):
    global bbaddress
    #device is something like "/sys/devices/platform/soc/3f201000.uart/tty/ttyAMA0/hci0/hci0:11/0005:057E:0306.000C"
    device = wait_for_balanceboard()
    iface = xwiimote.iface(device)
    iface.open(xwiimote.IFACE_BALANCE_BOARD)
    calibration = calibrate(iface)
    (kg, std) = average_measurements(measurements(iface), calibration=calibration)
    # do something with this data
    # like log to file or send to server
    print("{:.2f} +/- {:.2f}".format(kg/100.0, std/100.0))
    # find address of the balance board (once) and disconnect (if found).
    if bbaddress is None:
        bbaddress = find_device_address(bus)
    if bbaddress is not None:
        device = find_device(bbaddress)
        device.Disconnect()


def property_changed(interface, changed, invalidated, path, bus=None):
    iface = interface[interface.rfind(".") + 1:]
    for name, value in changed.items():
        val = str(value)
        logger.info("{%s.PropertyChanged} [%s] %s = %s" % (iface, path, name, val))
        # check if property "Connected" changed to "1". Does NOT check which device has connected, we only assume it was the balance board
        if name == "Connected" and val == "1":
            connect_balanceboard(bus)


@click.command()
def main():
    logger.debug("Starting")
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
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

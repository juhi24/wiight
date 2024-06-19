#!/usr/bin/python
# -*- coding: utf-8 -*-

import sys
import time
import select
import numpy
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

relevant_ifaces = [ "org.bluez.Adapter1", "org.bluez.Device1" ]
bbaddress = None


#from https://github.com/irq0/wiiscale/blob/master/scale.py
class RingBuffer():
    def __init__(self, length):
        self.length = length
        self.reset()
        self.filled = False

    def extend(self, x):
        x_index = (self.index + numpy.arange(x.size)) % self.data.size
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
        idx = (self.index + numpy.arange(self.data.size)) %self.data.size
        return self.data[idx]

    def reset(self):
        self.data = numpy.zeros(self.length, dtype=int)
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


def measurements(iface):
    p = select.epoll.fromfd(iface.get_fd())
    while True:
        p.poll() # blocks
        event = xwiimote.event()
        iface.dispatch(event)
        tl = event.get_abs(2)[0]
        tr = event.get_abs(0)[0]
        br = event.get_abs(3)[0]
        bl = event.get_abs(1)[0]
        yield (tl,tr,br,bl)
            

def average_measurements(ms, window_size=600, weight_threshold=50):
    last_measurements = []
    counter = 0
    while True:
        weight = sum(next(ms))
        if weight < weight_threshold:
            continue
        if len(last_measurements) >= window_size:
            last_measurements.pop(bisect.bisect_left(last_measurements, -counter))
        bisect.insort(last_measurements, weight)
        counter = (counter + 1) % window_size
        if len(last_measurements) == window_size:
            median = last_measurements[window_size // 2]
            if window_size % 2 == 0:
                median = (last_measurements[window_size // 2 - 1] + median) / 2
            stddev = numpy.std(last_measurements)
            return numpy.array((median, stddev))


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
        print ("found Wii Balanceboard with address %s" % (properties["Address"]))
        return properties["Address"]


def connect_balanceboard(bus):
    global bbaddress
    #device is something like "/sys/devices/platform/soc/3f201000.uart/tty/ttyAMA0/hci0/hci0:11/0005:057E:0306.000C"
    device = wait_for_balanceboard()
    iface = xwiimote.iface(device)
    iface.open(xwiimote.IFACE_BALANCE_BOARD)
    (kg, err) = average_measurements(measurements(iface))
    # do something with this data
    # like log to file or send to server
    print("{:.2f} +/- {:.2f}".format(kg/100.0, err/100.0))
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
        print("{%s.PropertyChanged} [%s] %s = %s" % (iface, path, name, val))
        # check if property "Connected" changed to "1". Does NOT check which device has connected, we only assume it was the balance board
        if name == "Connected" and val == "1":
            connect_balanceboard(bus)


@click.command()
def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    # bluetooth (dis)connection triggers PropertiesChanged signal
    bus.add_signal_receiver(partial(property_changed, bus=bus), bus_name="org.bluez",
            dbus_interface="org.freedesktop.DBus.Properties",
            signal_name="PropertiesChanged",
            path_keyword="path")
    try:
        mainloop = GObject.MainLoop()
        mainloop.run()
    except KeyboardInterrupt:
        mainloop.quit()
        print("Bye!")


if __name__ == '__main__':
    main()

# wiight

[![PyPI - Version](https://img.shields.io/pypi/v/wiight.svg)](https://pypi.org/project/wiight)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/wiight.svg)](https://pypi.org/project/wiight)

-----

**Table of Contents**

- [Installation](#installation)
- [Configuration](#configuration)
- [Calibration](#calibration)
- [Measurement](#measurement)
- [Capture](#capture)
- [MQTT Service](#mqtt-service)
- [Raspberry Pi Deployment](#raspberry-pi-deployment)
- [License](#license)

## Installation

```console
pip install wiight
```

Install the optional MQTT transport for service operation:

```console
pip install 'wiight[mqtt]'
```

Linux hardware access additionally requires BlueZ, the kernel `hid-wiimote`
driver, libxwiimote, and its Python binding. The balance board must already be
paired and connected.

## Configuration

The service configuration defaults to `/etc/wiight/wiight.toml`:

```toml
[board]
address = "00:22:4C:60:0C:DB"
# adapter = "hci0"

[measurement]
minimum_weight_centikilograms = 1000
stable_duration_seconds = 2.0
maximum_stddev_centikilograms = 50
unload_threshold_centikilograms = 500

[calibration]
path = "/var/lib/wiight/calibration.json"
minimum_samples = 100
maximum_corner_stddev_centikilograms = 10

[mqtt]
host = "mqtt.local"
port = 1883
client_id = "wiight"
base_topic = "wiight/scale"
discovery_prefix = "homeassistant"
tls = false
```

Validate configuration without accessing Bluetooth hardware:

```console
wiight config-check --config /etc/wiight/wiight.toml
```

MQTT credentials are intentionally not accepted in this file. Supply them
through the service environment or systemd credentials when MQTT support is
configured. Tare calibration is stored as versioned JSON bound to the board's
Bluetooth address; calibration from another board is rejected.

## Calibration

Place the connected board on a firm surface with nothing touching it, then run:

```console
wiight tare --config /etc/wiight/wiight.toml
```

The command collects the configured number of stable empty-board samples and
atomically writes the resulting per-corner offsets to the configured calibration
path. It fails without replacing the existing calibration if the board is too
unstable or too few samples arrive before the bounded capture ends.

## Measurement

Measure one stable weight using the persisted tare calibration:

```console
wiight measure --config /etc/wiight/wiight.toml
```

Use `--json` for machine-readable JSON Lines. Use `--continuous` to emit another
measurement after the board has been unloaded and occupied again. `--timeout`
and `--idle-timeout` bound the session and event wait respectively.

By default, tare and measurement require the configured Bluetooth address to be
connected in BlueZ, optionally scoped to the configured adapter, and require
exactly one balance board to be available through xwiimote. If multiple boards
are connected, provide the intended xwiimote sysfs path with `--device`.
Supplying `--device` is an explicit diagnostic override and bypasses configured
Bluetooth-address matching.

## Capture

Record a bounded hardware trace as JSON Lines:

```console
wiight capture --duration 10 --output board.jsonl
```

Use `--idle-timeout` to control how long capture waits without an xwiimote
event. Use `--device` when more than one balance board is connected. Each sample
contains wall-clock and monotonic timestamps, the xwiimote event type, four
corner values ordered as top-left, top-right, bottom-right, bottom-left, and the
total. Sensor values are centikilograms.

Capture files can contain personal weight data. Do not commit them unless they
have been intentionally anonymized for use as test fixtures.

## MQTT Service

Run the foreground service, suitable for supervision by systemd:

```console
WIIGHT_MQTT_USERNAME=wiight \
WIIGHT_MQTT_PASSWORD=secret \
wiight daemon --config /etc/wiight/wiight.toml
```

The daemon publishes retained Home Assistant device discovery, retained
availability and status, and non-retained stable weight measurements at QoS 1.
The MQTT client configures a retained offline last will and flushes a graceful
offline update before disconnecting. Corner samples are not published. Hardware
access runs in a dedicated worker with bounded sample buffering, while stable
measurement detection and publishing remain in the service thread.

## Raspberry Pi Deployment

The supported deployment target is Raspberry Pi OS Trixie with CPython 3.13.
Install BlueZ, libxwiimote, the `hid-wiimote` kernel driver, and a Python 3.13
xwiimote binding from system packages or their upstream sources before creating
the application environment. The native binding is not installed from PyPI.

From a source checkout, install the service into a system-site-packages-enabled
virtual environment so it can see the native binding:

```console
sudo python3.13 -m venv --system-site-packages /opt/wiight/venv
sudo /opt/wiight/venv/bin/pip install '.[mqtt]'
```

Install the service account, state directory, configuration, credentials, and
unit supplied under `deploy/`:

```console
sudo install -m 0644 deploy/wiight.sysusers /usr/lib/sysusers.d/wiight.conf
sudo install -m 0644 deploy/wiight.tmpfiles /usr/lib/tmpfiles.d/wiight.conf
sudo systemd-sysusers /usr/lib/sysusers.d/wiight.conf
sudo systemd-tmpfiles --create /usr/lib/tmpfiles.d/wiight.conf

sudo install -d -m 0750 -o root -g wiight /etc/wiight
sudo install -m 0640 -o root -g wiight \
	deploy/wiight.toml.example /etc/wiight/wiight.toml
sudo install -m 0600 -o root -g root \
	deploy/wiight.env.example /etc/wiight/wiight.env
sudo install -m 0644 deploy/wiight.service /etc/systemd/system/wiight.service
```

Edit the board address, broker settings, and credentials. Pair and connect the
board through BlueZ, then initialize tare as the service user and start the
daemon:

```console
sudo -u wiight /opt/wiight/venv/bin/wiight tare \
	--config /etc/wiight/wiight.toml
sudo systemctl daemon-reload
sudo systemctl enable --now wiight.service
sudo journalctl -u wiight.service -f
```

The unit runs without privilege escalation, keeps `/etc/wiight` read-only, and
permits writes only to `/var/lib/wiight`. Membership in the `input` group is
included for systems where the xwiimote devices require it.

## License

`wiight` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.

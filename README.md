# wiight

[![PyPI - Version](https://img.shields.io/pypi/v/wiight.svg)](https://pypi.org/project/wiight)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/wiight.svg)](https://pypi.org/project/wiight)

-----

**Table of Contents**

- [Installation](#installation)
- [Configuration](#configuration)
- [Pairing](#pairing)
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
driver, libxwiimote, and its Python binding.

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

[logging]
level = "INFO"
```

Validate configuration without accessing Bluetooth hardware:

```console
wiight config-check --config /etc/wiight/wiight.toml
```

MQTT credentials are intentionally not accepted in this file. Supply them
through the service environment or systemd credentials when MQTT support is
configured. Tare calibration is stored as versioned JSON bound to the board's
Bluetooth address; calibration from another board is rejected.

## Pairing

Press the balance board's red sync button, then pair and connect the configured
board through BlueZ:

```console
wiight pair --config /etc/wiight/wiight.toml
```

Discovery and pairing share a 30-second deadline by default; use `--timeout` to
change it. The command is restricted to the configured board address and
adapter. Wiight deliberately does not register a pairing agent because the
Nintendo PIN contains binary adapter-address bytes that cannot be represented
reliably by the Agent1 string API. BlueZ's built-in Wii autopair plugin must be
enabled to supply that PIN.

## Calibration

Initial tare calibration is optional. Without a calibration file, `measure` and
the MQTT daemon use zero offsets and report the board's kernel-provided,
factory-calibrated centikilogram values. MQTT status reports
`"calibrated": false` in this mode.

To remove the board's current empty-load offset, place it on a firm surface with
nothing touching it, then run:

```console
wiight tare --config /etc/wiight/wiight.toml
```

The command collects the configured number of stable empty-board samples and
atomically writes the resulting per-corner offsets to the configured calibration
path. It fails without replacing the existing calibration if the board is too
unstable or too few samples arrive before the bounded capture ends.

If a calibration file exists but is corrupt, incompatible, or belongs to a
different board, startup fails instead of silently falling back to zero offsets.

## Measurement

Measure one stable weight, using persisted tare when available:

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

Daemon logs are written to standard error as `LEVEL logger: message`, which keeps
foreground output readable and avoids duplicating timestamps already supplied by
the systemd journal. Set `logging.level` to `DEBUG`, `INFO`, `WARNING`, `ERROR`,
or `CRITICAL`; the default is `INFO`. Stable weights and raw corner readings are
not included in INFO logs. DEBUG logging includes MQTT topic metadata but not
payloads.

After publishing a stable weight, the service closes the xwiimote interface and
asks BlueZ to disconnect the board to conserve its batteries. The service stays
online and periodically asks BlueZ to restore the paired board's HID profile.
Pressing the board's front button makes it available for that reconnect.

Home Assistant discovery includes a Pair button. Press the balance board's red
sync button, then press Pair within 30 seconds. The same operation can be
requested by publishing the exact payload `PAIR` at QoS 1, without retain, to
`wiight/scale/pair/set` (relative to the configured base topic). Retained JSON
status is published to `wiight/scale/pair/status` with the states `idle`,
`pairing`, `paired`, or `failed`.

Home Assistant discovery also includes a Tare button. Unload the board before
pressing it, or publish the exact payload `TARE` at QoS 1, without retain, to
`wiight/scale/tare/set`. The daemon collects the configured number of samples,
applies the configured corner noise limit, atomically replaces the persisted
calibration, and uses it immediately. Retained JSON status is published to
`wiight/scale/tare/status` with the states `idle`, `taring`, `tared`, or
`failed`.

Pairing is restricted to the configured board address and adapter. BlueZ's
built-in `autopair` plugin recognizes `Nintendo RVL-WBC-01` and supplies the
adapter address as the Wii protocol's binary PIN. Wiight does not register an
Agent1 because its string return value cannot safely carry that PIN. Restrict
publish access to the pair and tare command topics with broker ACLs.

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

Edit the board address, broker settings, and credentials. The board can be
paired through BlueZ before startup or from the MQTT Pair button after startup.
Optionally initialize tare as the service user, then start the daemon:

```console
# Optional:
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

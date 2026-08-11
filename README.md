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
- [License](#license)

## Installation

```console
pip install wiight
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

## Service Status

The service core now runs hardware access behind a dedicated worker contract
with bounded sample buffering, cooperative shutdown, reconnect errors, and
stable-measurement processing in the service thread. MQTT topic, payload,
retention, availability, and Home Assistant discovery contracts are implemented
without retaining personal weight measurements. The network client and
`wiight daemon` command are the next deployment milestone and are not yet
available.

## License

`wiight` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.

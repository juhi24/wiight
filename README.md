# wiight

[![PyPI - Version](https://img.shields.io/pypi/v/wiight.svg)](https://pypi.org/project/wiight)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/wiight.svg)](https://pypi.org/project/wiight)

-----

**Table of Contents**

- [Installation](#installation)
- [Configuration](#configuration)
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

## License

`wiight` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.

# wiight

[![PyPI - Version](https://img.shields.io/pypi/v/wiight.svg)](https://pypi.org/project/wiight)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/wiight.svg)](https://pypi.org/project/wiight)

-----

**Table of Contents**

- [Installation](#installation)
- [Capture](#capture)
- [License](#license)

## Installation

```console
pip install wiight
```

Linux hardware access additionally requires BlueZ, the kernel `hid-wiimote`
driver, libxwiimote, and its Python binding. The balance board must already be
paired and connected.

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

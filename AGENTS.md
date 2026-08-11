# Agent Guide

## Project direction

`wiight` is a Python 3.12+ library and CLI for reading weight data from a Wii Balance Board on Linux. Keep reusable measurement and calibration logic separate from BlueZ, D-Bus, GLib, and xwiimote integration so it can be tested without hardware.

Read [README.md](README.md) for user-facing installation information and [pyproject.toml](pyproject.toml) for authoritative package metadata and tool configuration.

## Working practices

- Keep this file up to date when commands, architecture, dependencies, or project conventions change. Include instruction updates in the same change that makes existing guidance inaccurate.
- Commit often with small, coherent changes after their relevant checks pass. Keep unrelated user changes out of those commits and use commit messages that describe the behavior or convention changed.

## Code boundaries

- [src/wiight/wiiweigh.py](src/wiight/wiiweigh.py) owns balance-board discovery, xwiimote event reading, calibration, aggregation, and the `wiight` CLI entry point.
- [src/wiight/bluezutils.py](src/wiight/bluezutils.py) owns BlueZ object discovery and D-Bus adapter/device lookup.
- Preserve the corner order `(top_left, top_right, bottom_right, bottom_left)` across raw readings, calibration values, tests, and public APIs.
- Sensor readings are aggregated in centikilograms; conversion to kilograms happens at the presentation boundary by dividing by 100.
- Avoid adding import-time hardware access, event-loop startup, or logging configuration. Keep side effects behind explicit calls and the CLI entry point.
- Prefer typed data and explicit public exports when evolving the library API. Keep hardware-independent calculations deterministic and independently testable.

## Hardware and tests

The Python packages `dbus`, `gi`/GLib, and `xwiimote` depend on Linux system packages and services; they are intentionally available through Hatch's `system-packages = true` environment rather than normal PyPI dependencies. BlueZ access uses the system bus, and xwiimote reads are blocking.

Do not require a physical board, a running BlueZ daemon, or a system D-Bus in unit tests. Mock those boundaries and use synthetic four-corner readings for calibration and aggregation tests. Mark genuine hardware integration tests explicitly and document their prerequisites.

When changing device discovery, do not assume every `org.bluez.Device1` connection event belongs to the balance board. Match the intended device and preserve useful behavior when adapters or devices are absent.
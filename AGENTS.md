# Agent Guide

## Project direction

`wiight` is a Python 3.13+ library and CLI for reading weight data from a Wii Balance Board on Linux. Keep reusable measurement and calibration logic separate from BlueZ, D-Bus, GLib, and xwiimote integration so it can be tested without hardware.

Read [README.md](README.md) for user-facing installation information and [pyproject.toml](pyproject.toml) for authoritative package metadata and tool configuration.

## Working practices

- Keep this file up to date when commands, architecture, dependencies, or project conventions change. Include instruction updates in the same change that makes existing guidance inaccurate.
- Commit often with small, coherent changes after their relevant checks pass. Keep unrelated user changes out of those commits and use commit messages that describe the behavior or convention changed.

## Code boundaries

- [src/wiight/measurement.py](src/wiight/measurement.py) owns hardware-independent calibration and stable-weight calculations.
- [src/wiight/session.py](src/wiight/session.py) owns hardware-independent orchestration from captured events to tare and stable measurements.
- [src/wiight/hardware.py](src/wiight/hardware.py) owns configured board discovery, xwiimote interface lifecycle, cancellable event capture, and hardware errors.
- [src/wiight/worker.py](src/wiight/worker.py) owns the hardware thread, bounded sample mailbox, retry, and immutable worker lifecycle events.
- [src/wiight/daemon.py](src/wiight/daemon.py) owns service state, stable-measurement detection from worker samples, and publication orchestration.
- [src/wiight/mqtt.py](src/wiight/mqtt.py) owns transport-independent MQTT topics, payloads, retain policy, and Home Assistant discovery.
- [src/wiight/cli.py](src/wiight/cli.py) owns the `wiight` Click command group and presentation formats.
- [src/wiight/config.py](src/wiight/config.py) owns strict TOML loading and service configuration validation.
- [src/wiight/calibration.py](src/wiight/calibration.py) owns versioned, board-bound, atomic tare persistence.
- [src/wiight/wiiweigh.py](src/wiight/wiiweigh.py) contains the legacy discovery and measurement flow while it is incrementally replaced by the new modules. This module may be removed when it's no longer needed.
- [src/wiight/bluezutils.py](src/wiight/bluezutils.py) owns BlueZ object discovery and D-Bus adapter/device lookup.
- MQTT-triggered pairing is restricted to the configured board and adapter. Do not register Agent1 for Nintendo pairing: BlueZ's Wii autopair plugin must supply the binary adapter-address PIN.
- Preserve the corner order `(top_left, top_right, bottom_right, bottom_left)` across raw readings, calibration values, tests, and public APIs.
- Sensor readings are aggregated in centikilograms; conversion to kilograms happens at the presentation boundary by dividing by 100.
- Persisted tare calibration is optional. When absent, measurement and daemon workflows use zero corner offsets and report `calibrated = false`; invalid existing calibration remains a hard error.
- Avoid adding import-time hardware access, event-loop startup, or logging configuration. Keep side effects behind explicit calls and the CLI entry point.
- Prefer typed data and explicit public exports when evolving the library API. Keep hardware-independent calculations deterministic and independently testable.

## Hardware and tests

The Python packages `dbus`, `gi`/GLib, and `xwiimote` depend on Linux system packages and services; they are intentionally available through Hatch's `system-packages = true` environment rather than normal PyPI dependencies. BlueZ access uses the system bus, and xwiimote reads are blocking.

Do not require a physical board, a running BlueZ daemon, or a system D-Bus in unit tests. Mock those boundaries and use synthetic four-corner readings for calibration and aggregation tests. Mark genuine hardware integration tests explicitly and document their prerequisites.

Use `wiight capture --duration SECONDS --output FILE.jsonl` for bounded hardware characterization. Capture output can contain personal weight data; keep it out of source control unless it has been intentionally anonymized as a test fixture.

When changing device discovery, do not assume every `org.bluez.Device1` connection event belongs to the balance board. Match the intended device and preserve useful behavior when adapters or devices are absent.
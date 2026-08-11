from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from threading import Event

import click


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, invoke_without_command=True)
@click.pass_context
def main(context: click.Context) -> None:
    """Read and measure weight from a Wii Balance Board."""
    if context.invoked_subcommand is None:
        click.echo(context.get_help())


@main.command()
@click.option(
    "--duration",
    type=click.FloatRange(min=0, min_open=True),
    default=10.0,
    show_default=True,
    help="Total capture duration in seconds.",
)
@click.option(
    "--idle-timeout",
    type=click.FloatRange(min=0, min_open=True),
    default=2.0,
    show_default=True,
    help="Fail if no xwiimote event arrives within this many seconds.",
)
@click.option("--device", help="Explicit xwiimote sysfs device path.")
@click.option(
    "--output",
    type=click.File("w", encoding="utf-8", lazy=True),
    default="-",
    show_default=True,
    help="JSONL output file, or - for stdout.",
)
def capture(duration: float, idle_timeout: float, device: str | None, output) -> None:
    """Capture timestamped balance-board events as JSON Lines."""
    from wiight.hardware import (
        BalanceBoardError,
        capture_events,
        open_balance_board,
    )

    try:
        with open_balance_board(device) as interface:
            for event in capture_events(
                interface, duration=duration, idle_timeout=idle_timeout
            ):
                json.dump(event.as_dict(), output, separators=(",", ":"))
                output.write("\n")
                output.flush()
    except BalanceBoardError as error:
        raise click.ClickException(str(error)) from error


@main.command("config-check")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("/etc/wiight/wiight.toml"),
    show_default=True,
    help="TOML configuration file.",
)
def config_check(config_path: Path) -> None:
    """Validate service configuration without accessing hardware."""
    from wiight.calibration import CalibrationStoreError, load_calibration
    from wiight.config import ConfigError, load_config

    try:
        config = load_config(config_path)
        calibration_status = "missing"
        if config.calibration.path.exists():
            load_calibration(config.calibration.path, config.board.address)
            calibration_status = "valid"
    except (ConfigError, CalibrationStoreError) as error:
        raise click.ClickException(str(error)) from error

    click.echo(
        f"configuration valid: board={config.board.address} "
        f"mqtt={config.mqtt.host}:{config.mqtt.port} "
        f"calibration={calibration_status}"
    )


@main.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("/etc/wiight/wiight.toml"),
    show_default=True,
)
@click.option("--device", help="Explicit xwiimote sysfs device path.")
@click.option(
    "--duration",
    type=click.FloatRange(min=0, min_open=True),
    default=10.0,
    show_default=True,
    help="Maximum tare capture duration in seconds.",
)
@click.option(
    "--idle-timeout",
    type=click.FloatRange(min=0, min_open=True),
    default=2.0,
    show_default=True,
)
def tare(
    config_path: Path,
    device: str | None,
    duration: float,
    idle_timeout: float,
) -> None:
    """Tare an unloaded board and persist its corner offsets."""
    from wiight.calibration import CalibrationStoreError, store_calibration
    from wiight.config import ConfigError, load_config
    from wiight.hardware import (
        BalanceBoardError,
        capture_events,
        find_configured_balance_board_path,
        open_balance_board,
    )
    from wiight.measurement import CalibrationError
    from wiight.session import calculate_tare

    try:
        config = load_config(config_path)
        device_path = device or find_configured_balance_board_path(
            config.board.address, config.board.adapter
        )
        with open_balance_board(device_path) as interface:
            events = capture_events(
                interface, duration=duration, idle_timeout=idle_timeout
            )
            try:
                calibration = calculate_tare(events, config.calibration)
            finally:
                events.close()
        store_calibration(
            config.calibration.path,
            config.board.address,
            calibration,
        )
    except (ConfigError, BalanceBoardError, CalibrationError, CalibrationStoreError) as error:
        raise click.ClickException(str(error)) from error

    click.echo(
        f"tare saved: samples={calibration.sample_count} "
        f"maximum_corner_stddev={calibration.maximum_corner_stddev:.3f} ckg "
        f"path={config.calibration.path}"
    )


@main.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("/etc/wiight/wiight.toml"),
    show_default=True,
)
@click.option("--device", help="Explicit xwiimote sysfs device path.")
@click.option(
    "--timeout",
    type=click.FloatRange(min=0, min_open=True),
    default=30.0,
    show_default=True,
    help="Maximum measurement session duration in seconds.",
)
@click.option(
    "--idle-timeout",
    type=click.FloatRange(min=0, min_open=True),
    default=2.0,
    show_default=True,
)
@click.option("--continuous", is_flag=True, help="Emit each measurement after unload.")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON Lines.")
def measure(
    config_path: Path,
    device: str | None,
    timeout: float,
    idle_timeout: float,
    continuous: bool,
    json_output: bool,
) -> None:
    """Measure stable weight, using persisted tare when available."""
    from wiight.calibration import (
        CalibrationStoreError,
        load_optional_calibration,
        zero_calibration,
    )
    from wiight.config import ConfigError, load_config
    from wiight.hardware import (
        BalanceBoardError,
        capture_events,
        find_configured_balance_board_path,
        open_balance_board,
    )
    from wiight.measurement import centikilograms_to_kilograms
    from wiight.session import MeasurementTimeoutError, measure_once, stable_measurements

    def emit(measurement) -> None:
        weight_kg = centikilograms_to_kilograms(measurement.raw_total)
        dispersion_kg = centikilograms_to_kilograms(measurement.raw_stddev)
        if json_output:
            click.echo(
                json.dumps(
                    {
                        "weight_kg": weight_kg,
                        "dispersion_kg": dispersion_kg,
                        "monotonic_time": measurement.monotonic_time,
                        "sample_count": measurement.sample_count,
                    },
                    separators=(",", ":"),
                )
            )
        else:
            click.echo(f"{weight_kg:.2f} kg +/- {dispersion_kg:.2f} kg")

    try:
        config = load_config(config_path)
        stored = load_optional_calibration(
            config.calibration.path, config.board.address
        )
        calibration = stored.calibration if stored is not None else zero_calibration()
        device_path = device or find_configured_balance_board_path(
            config.board.address, config.board.adapter
        )
        with open_balance_board(device_path) as interface:
            events = capture_events(
                interface, duration=timeout, idle_timeout=idle_timeout
            )
            try:
                if continuous:
                    emitted = False
                    for measurement in stable_measurements(
                        events,
                        calibration,
                        config.measurement,
                    ):
                        emit(measurement)
                        emitted = True
                    if not emitted:
                        raise MeasurementTimeoutError(
                            "capture ended before a stable weight measurement was available"
                        )
                else:
                    emit(
                        measure_once(
                            events,
                            calibration,
                            config.measurement,
                        )
                    )
            finally:
                events.close()
    except (ConfigError, CalibrationStoreError, BalanceBoardError, MeasurementTimeoutError) as error:
        raise click.ClickException(str(error)) from error


@main.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("/etc/wiight/wiight.toml"),
    show_default=True,
)
def daemon(config_path: Path) -> None:
    """Run the foreground MQTT smart-scale service."""
    from wiight.calibration import CalibrationStoreError, load_optional_calibration
    from wiight.config import ConfigError, load_config
    from wiight.daemon import DaemonService
    from wiight.mqtt import MqttError, MqttPublisher

    try:
        config = load_config(config_path)
        stored = load_optional_calibration(
            config.calibration.path, config.board.address
        )
        publisher = MqttPublisher(
            config.mqtt,
            username=os.environ.get("WIIGHT_MQTT_USERNAME"),
            password=os.environ.get("WIIGHT_MQTT_PASSWORD"),
        )
        service = DaemonService(
            config,
            stored.calibration if stored is not None else None,
            publisher,
        )
    except (ConfigError, CalibrationStoreError, MqttError) as error:
        raise click.ClickException(str(error)) from error

    stop_event = Event()

    def request_stop(signum, frame) -> None:
        stop_event.set()

    previous_handlers = {
        signum: signal.signal(signum, request_stop)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        service.run(stop_event)
    except MqttError as error:
        raise click.ClickException(str(error)) from error
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    main()
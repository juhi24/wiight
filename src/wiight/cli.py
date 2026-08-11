from __future__ import annotations

import json
from pathlib import Path

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


if __name__ == "__main__":
    main()
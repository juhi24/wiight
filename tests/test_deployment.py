from pathlib import Path
import tomllib

from wiight.config import load_config


ROOT = Path(__file__).parent.parent
DEPLOY = ROOT / "deploy"


def test_example_service_configuration_is_valid() -> None:
    config = load_config(DEPLOY / "wiight.toml.example")

    assert config.board.address == "00:22:4C:60:0C:DB"
    assert config.calibration.path == Path("/var/lib/wiight/calibration.json")
    assert config.mqtt.base_topic == "wiight/scale"


def test_systemd_unit_has_service_lifecycle_and_hardening() -> None:
    unit = (DEPLOY / "wiight.service").read_text(encoding="utf-8")

    for directive in (
        "User=wiight",
        "ExecStart=/opt/wiight/venv/bin/wiight daemon",
        "Restart=on-failure",
        "TimeoutStopSec=10s",
        "StateDirectory=wiight",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ReadWritePaths=/var/lib/wiight",
    ):
        assert directive in unit


def test_deployment_credentials_are_not_in_toml() -> None:
    config = (DEPLOY / "wiight.toml.example").read_text(encoding="utf-8")

    assert "password" not in config.casefold()
    assert "username" not in config.casefold()


def test_package_targets_cpython_313_without_pypy_claim() -> None:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)["project"]

    assert project["requires-python"] == ">=3.13"
    assert "Programming Language :: Python :: 3.13" in project["classifiers"]
    assert all("PyPy" not in classifier for classifier in project["classifiers"])
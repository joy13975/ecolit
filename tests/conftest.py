"""Test fixtures and configuration for ecolit tests."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ecolit.charging.policies import EnergyMetrics


@pytest.fixture
def mock_config() -> dict[str, Any]:
    """Basic test configuration."""
    return {
        "network": {
            "scan_ranges": [],
            "echonet": {"interface": "0.0.0.0", "port": 3610, "timeout": 5},
            "discovery": {
                "device_timeout": 0.4,
                "validation_timeout": 10,
                "property_timeout": 3.0,
                "wait_iterations": 300,
                "wait_interval": 0.01,
            },
        },
        "devices": {"required": []},
        "app": {"polling_interval": 10},
        "ev_charging": {
            "enabled": True,
            "policy": "eco",
            "max_amps": 20,
            "eco": {"export_threshold": 50},
            "hurry": {"max_import": 1000},
            "adjustment_interval": 30,
            "measurement_interval": 10,
        },
        "tesla": {
            "enabled": False,
            "host": None,
            "refresh_token": None,
            "min_charging_amps": 5,
            "max_charging_amps": 32,
            "charging_voltage": 200,
        },
        "logging": {
            "level": "INFO",
            "file": "ecolit.log",
            "max_size": "10MB",
            "backup_count": 5,
        },
    }


@pytest.fixture
def energy_metrics_exporting() -> EnergyMetrics:
    """Energy metrics showing grid export situation."""
    return EnergyMetrics(
        battery_soc=85.0,
        battery_power=500,  # Charging
        grid_power_flow=-200,  # Exporting 200W
        solar_power=1500,
    )


@pytest.fixture
def energy_metrics_importing() -> EnergyMetrics:
    """Energy metrics showing grid import situation."""
    return EnergyMetrics(
        battery_soc=45.0,
        battery_power=-300,  # Discharging
        grid_power_flow=800,  # Importing 800W
        solar_power=200,
    )


@pytest.fixture
def energy_metrics_balanced() -> EnergyMetrics:
    """Energy metrics showing balanced grid situation."""
    return EnergyMetrics(
        battery_soc=60.0,
        battery_power=0,  # Idle
        grid_power_flow=0,  # Balanced
        solar_power=1000,
    )


@pytest.fixture
def energy_metrics_no_data() -> EnergyMetrics:
    """Energy metrics with no data available."""
    return EnergyMetrics(
        battery_soc=None, battery_power=None, grid_power_flow=None, solar_power=None
    )


@pytest.fixture
def energy_metrics_eco_ready() -> EnergyMetrics:
    """Energy metrics suitable for ECO policy (99%+ battery SOC)."""
    return EnergyMetrics(
        battery_soc=99.5,
        battery_power=300,  # Charging
        grid_power_flow=-400,  # Exporting 400W
        solar_power=2000,
    )


@pytest.fixture
def mock_echonet_api():
    """Mock ECHONET API client."""
    api_mock = MagicMock()
    api_mock.devices = {}
    api_mock._state = {}
    api_mock.discover = AsyncMock(return_value=True)
    api_mock.echonetMessage = AsyncMock(return_value={})
    return api_mock


@pytest.fixture
def mock_udp_server():
    """Mock UDP server for ECHONET communication."""
    server_mock = MagicMock()
    server_mock.run = MagicMock()
    return server_mock


@pytest.fixture
def mock_solar_device():
    """Mock solar power device."""
    device_mock = MagicMock()
    device_mock.getAllPropertyMaps = AsyncMock()
    device_mock.update = AsyncMock(return_value=1200)  # Default 1200W
    return device_mock


@pytest.fixture
def mock_battery_device():
    """Mock battery device."""
    device_mock = MagicMock()
    device_mock.getAllPropertyMaps = AsyncMock()
    device_mock.update = AsyncMock(return_value=75.0)  # Default 75% SOC
    return device_mock


@pytest.fixture
def mock_time():
    """Mock time.time() for testing rate limiting."""
    with patch("time.time") as mock:
        mock.return_value = 1000.0  # Fixed timestamp
        yield mock


@pytest.fixture
def temp_config_file(tmp_path):
    """Create a temporary config file for testing."""
    config_content = """
network:
  scan_ranges: ["192.168.1"]
devices:
  required: []
app:
  polling_interval: 5
ev_charging:
  enabled: true
  policy: "eco"
  max_amps: 16
"""
    config_file = tmp_path / "test_config.yaml"
    config_file.write_text(config_content)
    return config_file

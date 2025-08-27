"""Configuration management for Ecolit."""

import os
from pathlib import Path
from typing import Any

import yaml


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load configuration from file or environment."""
    default_config = {
        "network": {
            "scan_ranges": [],  # Empty = discovery mode
            "echonet": {
                "interface": "0.0.0.0",
                "port": 3610,
                "timeout": 5,
            },
            "discovery": {
                "device_timeout": 0.4,  # Timeout per IP during discovery scan
                "validation_timeout": 10,  # Timeout for required device validation
                "property_timeout": 3.0,  # Timeout for reading device properties
                "wait_iterations": 300,  # Max iterations to wait for discovery
                "wait_interval": 0.01,  # Sleep interval between wait iterations
            },
        },
        "devices": {
            "required": [],  # App fails if these aren't found
        },
        "app": {
            "polling_interval": 10,
        },
        "ev_charging": {
            "enabled": False,
            "policy": "eco",
            "max_amps": 20,
            "eco": {
                "export_threshold": 50,
            },
            "hurry": {
                "max_import": 1000,
            },
            "adjustment_interval": 30,
            "measurement_interval": 10,
            "amp_adjustments": {
                "increase_step": 1,  # Amps to increase when conditions allow
                "decrease_step": 2,  # Amps to decrease when limiting needed
            },
        },
        "tesla": {
            "enabled": False,
            "host": None,
            "refresh_token": None,
            "min_charging_amps": 5,
            "max_charging_amps": 32,
            "charging_voltage": 200,
        },
        "metrics": {
            "enabled": False,
            "folder": "data/ecolit/metrics",
        },
        "logging": {
            "level": "INFO",
            "file": "ecolit.log",
            "max_size": "10MB",
            "backup_count": 5,
        },
    }

    # Load main config
    if config_path is None:
        config_path = os.environ.get("ECOLIT_CONFIG")
        if config_path is None:
            # Try config.yaml, then config.local.yaml, then template
            for candidate in ["config.yaml", "config.local.yaml", "config.template.yaml"]:
                if Path(candidate).exists():
                    config_path = candidate
                    break
            else:
                config_path = "config.yaml"  # Default even if doesn't exist

    config_file = Path(config_path)
    user_config = {}

    if config_file.exists():
        with open(config_file) as f:
            user_config = yaml.safe_load(f) or {}

    # Load devices config separately
    devices_file = Path("devices.yaml")
    if devices_file.exists():
        with open(devices_file) as f:
            devices_config = yaml.safe_load(f) or {}
            if "devices" in devices_config:
                user_config.setdefault("devices", {}).update(devices_config["devices"])

    # Deep merge configuration
    return _deep_merge(default_config, user_config)


def save_config(config: dict[str, Any], config_path: str = "config.yaml") -> None:
    """Save configuration to YAML file."""
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, indent=2)


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result

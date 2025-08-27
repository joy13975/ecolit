# Configuration Guide

## Configuration File

Ecolit uses a JSON configuration file (`config.json`) for runtime settings. The file can be specified via the `ECOLIT_CONFIG` environment variable or defaults to `config.json` in the current directory.

## Configuration Structure

```json
{
  "polling_interval": 10,
  "echonet": {
    "interface": "auto",
    "port": 3610,
    "timeout": 10,
    "retry_count": 3
  },
  "tesla": {
    "enabled": false,
    "host": "192.168.1.100",
    "refresh_token": "your_token_here",
    "min_charging_amps": 5,
    "max_charging_amps": 32
  },
  "modes": {
    "default": "grid_free",
    "emergency_charging": false,
    "solar_only": false,
    "battery_preserve": false
  },
  "thresholds": {
    "min_battery_soc": 20,
    "target_battery_soc": 80,
    "max_grid_power": 0,
    "solar_buffer": 500,
    "battery_buffer": 100
  },
  "logging": {
    "level": "INFO",
    "file": "ecolit.log",
    "max_size": "10MB",
    "backup_count": 5
  }
}
```

## Configuration Parameters

### General Settings

- **polling_interval**: Seconds between device polls (default: 10)

### ECHONET Settings

- **interface**: Network interface or "auto" for automatic selection
- **port**: UDP port for ECHONET Lite (default: 3610)
- **timeout**: Request timeout in seconds
- **retry_count**: Number of retries for failed requests

### Tesla Integration

- **enabled**: Enable/disable Tesla integration
- **host**: Tesla Wall Connector IP address (for local API)
- **refresh_token**: Tesla API refresh token
- **min_charging_amps**: Minimum charging current (typically 5A)
- **max_charging_amps**: Maximum charging current (based on circuit/vehicle)

### Operating Modes

- **default**: Default operating mode on startup
- **emergency_charging**: Override all limits for maximum charging
- **solar_only**: Only charge from solar generation
- **battery_preserve**: Prevent battery discharge for EV charging

### Thresholds

- **min_battery_soc**: Minimum battery SOC to maintain (%)
- **target_battery_soc**: Target battery SOC for optimization (%)
- **max_grid_power**: Maximum allowed grid import (W)
- **solar_buffer**: Reserve solar power for house loads (W)
- **battery_buffer**: Reserve battery power margin (W)

### Logging

- **level**: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- **file**: Log file path
- **max_size**: Maximum log file size before rotation
- **backup_count**: Number of rotated log files to keep

## Operating Modes Explained

### Grid-Free Mode
Optimizes charging to minimize or eliminate grid power usage:
- Uses available solar generation first
- Can use battery power if SOC > min_battery_soc
- Dynamically adjusts charging current

### Emergency Mode
Maximum charging speed regardless of power source:
- Uses maximum available current
- Ignores cost optimization
- Suitable for urgent charging needs

### Solar-Only Mode
Strictly charges from solar generation:
- No grid power usage
- No battery discharge for charging
- Charging pauses when solar insufficient

### Battery Preserve Mode
Protects home battery from EV charging demands:
- Only uses solar + grid
- Maintains battery for home backup
- Useful during outage-prone periods

## Environment Variables

- `ECOLIT_CONFIG`: Path to configuration file
- `ECOLIT_LOG_LEVEL`: Override log level
- `TESLA_REFRESH_TOKEN`: Override Tesla token (security)

## Example Configurations

### Minimal Solar-Only Setup
```json
{
  "polling_interval": 30,
  "modes": {
    "default": "solar_only"
  },
  "tesla": {
    "enabled": true,
    "refresh_token": "your_token"
  }
}
```

### Aggressive Grid-Free with Battery
```json
{
  "modes": {
    "default": "grid_free"
  },
  "thresholds": {
    "min_battery_soc": 10,
    "max_grid_power": 100,
    "solar_buffer": 200
  }
}
```

### Conservative Battery Protection
```json
{
  "modes": {
    "default": "grid_free",
    "battery_preserve": true
  },
  "thresholds": {
    "min_battery_soc": 50,
    "target_battery_soc": 90,
    "battery_buffer": 500
  }
}
```
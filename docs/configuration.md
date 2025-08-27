# Configuration Guide

## Configuration File

Ecolit uses a YAML configuration file (`config.yaml`) for runtime settings. 

**To get started:**
1. Copy `config.template.yaml` to `config.yaml`
2. Customize the values for your setup
3. See the template file for detailed explanations of each setting

## Configuration Reference

All configuration options, defaults, and examples are documented in the [`config.template.yaml`](../config.template.yaml) file. This template includes:

- **Network & device discovery settings**
- **EV charging policies and parameters**
- **Tesla integration options**
- **Logging configuration**
- **Example configurations for different scenarios**

## Configuration Categories

For detailed parameter descriptions, see the [`config.template.yaml`](../config.template.yaml) file which contains inline documentation for all settings.

### Current Implementation Settings
The following configuration sections are actively used by the current implementation:
- **Network discovery**: Device scanning and ECHONET Lite settings
- **EV charging policies**: ECO, HURRY, and EMERGENCY policies  
- **Rate limiting**: Amp adjustment intervals and steps
- **Logging**: Output levels and file rotation

### Future Implementation Settings
The following configuration sections are prepared for future features:
- **Tesla API integration**: Refresh tokens and vehicle settings
- **Complex algorithms**: Advanced battery coordination thresholds

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

## Example Configurations

Multiple example configurations for different use cases are provided in [`config.template.yaml`](../config.template.yaml), including:

- **Discovery mode**: Automatic device detection
- **Production mode**: Validated specific devices  
- **EV charging enabled**: Various policy configurations
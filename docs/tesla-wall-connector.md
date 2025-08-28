# Tesla Wall Connector Integration

## Overview

Ecolit supports monitoring Tesla Wall Connector Gen 3 units that have WiFi connectivity. This feature provides real-time visibility into Wall Connector status, charging sessions, and electrical metrics.

## Configuration

Wall Connector monitoring uses Tesla Fleet API automatically - no additional configuration required beyond Tesla API setup.

## Prerequisites

1. Tesla Fleet API configured with `energy_device_data` scope
2. Wall Connector registered in Tesla app under "Products"
3. Wall Connector connected to WiFi (via Tesla app setup)

## Available Information

Tesla Fleet API provides Wall Connector live status including:

### Real-Time Status
- Power consumption (W) with context interpretation
- Wall Connector state (Standby, Charging, Vehicle Connected, etc.)
- Battery conditioning/trickle charging detection
- Connection status

## Usage

1. Run `make tesla-control`
2. Select option 1 to view combined vehicle and Wall Connector status

## API Details

Uses Tesla Fleet API `/api/1/energy_sites/{id}/live_status` endpoint to retrieve Wall Connector data from Tesla's cloud service.

## Power Consumption Notes

**Normal Standby Power**: 500-700W consumption when vehicle connected but not charging is normal for:
- Battery conditioning/trickle charging
- Pilot signal maintenance  
- Vehicle thermal management

**Zero Power Control**: No API command exists to stop standby power consumption. Physical smart switch/relay upstream of Wall Connector is required for true 0W control.

## Troubleshooting

Common issues:

1. **"No Wall Connectors found"**: Ensure `energy_device_data` scope in Tesla token
2. **API errors**: Re-run `make tesla-mint` to refresh token with energy scopes
3. **Missing data**: Wall Connector must be registered in Tesla app Products

## Notes

- Uses official Tesla Fleet API for reliable, authoritative data
- Only monitoring is supported; control functions require physical solutions
- Wall Connector state codes automatically mapped to human-readable names
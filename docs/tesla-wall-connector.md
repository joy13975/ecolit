# Tesla Wall Connector Integration

## Overview

Ecolit supports monitoring Tesla Wall Connector Gen 3 units that have WiFi connectivity. This feature provides real-time visibility into Wall Connector status, charging sessions, and electrical metrics.

## Configuration

To enable Wall Connector monitoring, add the Wall Connector's IP address to your `config.yaml`:

```yaml
tesla:
  # ... other Tesla settings ...
  wall_connector_ip: "192.168.1.100"  # Your Wall Connector's IP address
```

## Finding Your Wall Connector IP

1. Ensure your Wall Connector is connected to WiFi (configured via Tesla app)
2. Check your router's DHCP client list for "Tesla Wall Connector"
3. Or use network scanning tools to find devices on port 443

## Available Information

When configured, the tesla-control menu option 1 displays:

### Real-Time Status
- Vehicle connection status
- Active charging state
- Current power delivery (Amps, Volts, kW)
- Session energy delivered
- Session duration

### Electrical Metrics  
- Grid voltage and frequency
- Phase currents (for 3-phase installations)
- Temperature monitoring (PCB, handle)

### Lifetime Statistics
- Total energy delivered
- Number of charge sessions
- Uptime statistics
- Contactor cycles

## Usage

1. Configure Wall Connector IP in `config.yaml`
2. Run `make tesla-control`
3. Select option 1 to view combined vehicle and Wall Connector status

## API Details

The Wall Connector Gen 3 exposes several REST API endpoints:

- `/api/1/vitals` - Real-time charging and electrical data
- `/api/1/lifetime` - Cumulative usage statistics  
- `/api/1/wifi_status` - WiFi connection information
- `/api/1/version` - Firmware version information

These are read-only endpoints that provide monitoring capabilities. The Wall Connector uses a self-signed SSL certificate which the client automatically handles.

## Troubleshooting

If Wall Connector connection fails:

1. **Verify Network Access**
   - Ping the Wall Connector IP address
   - Ensure port 443 is accessible
   - Check firewall rules

2. **Check Wall Connector Status**
   - Ensure WiFi is configured and connected (via Tesla app)
   - Wall Connector LED should be solid green or blue
   - Try accessing `https://<ip>/api/1/vitals` in a browser

3. **Common Issues**
   - Wall Connector may be in sleep mode if no vehicle connected
   - Some firmware versions may have different API availability
   - Network isolation between VLANs can block access

## Standalone Testing

Test Wall Connector connectivity directly:

```bash
# Test the Wall Connector module
uv run python -m ecolit.tesla.wall_connector 192.168.1.100
```

This will attempt to connect and display all available status information.

## Notes

- The Wall Connector API is unofficial and undocumented by Tesla
- API availability may vary by firmware version
- Only monitoring is supported; control functions are not available via API
- The Wall Connector must be on the same network or accessible via routing
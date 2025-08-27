# Tesla Vehicle Command Protocol (TVCP) Setup Guide

## Overview

Modern Tesla vehicles (2024+ firmware) require the Tesla Vehicle Command Protocol (TVCP) for vehicle commands like setting charging amperage. This guide helps you set up the Tesla HTTP proxy to enable these commands.

## Why TVCP is Required

Tesla deprecated the old REST API vehicle commands in January 2024 for security reasons. The new TVCP provides end-to-end encrypted command authentication.

**Affected Commands:**
- `set_charging_amps` - Set charging amperage
- `charge_start` - Start charging  
- `charge_stop` - Stop charging
- `set_charge_limit` - Set charge limit
- Other vehicle control commands

**NOT Affected (still work without TVCP):**
- Vehicle data retrieval (`get_vehicle_data`, `get_charging_config`, etc.)
- Telemetry streaming
- Authentication

## Prerequisites

- Go 1.23.0+ installed
- Tesla Developer Account with Fleet API access
- Modern Tesla vehicle (2024+ firmware)
- Valid Tesla OAuth tokens (client_id, client_secret, refresh_token)

## Setup Steps

### 1. Install Tesla Vehicle Command SDK

```bash
# Clone the official Tesla repository
git clone https://github.com/teslamotors/vehicle-command.git
cd vehicle-command

# Install Go dependencies
go get ./...
go build ./...
go install ./...
```

### 2. Generate Authentication Keys

```bash
# Generate key pair for your vehicle
export TESLA_KEY_NAME=$(whoami)
tesla-keygen create > public_key.pem

# This creates:
# - public_key.pem (share with Tesla)
# - private_key.pem (keep secure)
```

### 3. Create TLS Certificates

```bash
# Create config directory
mkdir -p config

# Generate TLS certificate for the HTTP proxy
openssl req -x509 -nodes -newkey ec \
    -pkeyopt ec_paramgen_curve:secp521r1 \
    -pkeyopt ec_param_enc:named_curve \
    -subj '/CN=localhost' \
    -keyout config/tls-key.pem \
    -out config/tls-cert.pem
```

### 4. Add Key to Your Tesla Vehicle

```bash
# Add your public key to the vehicle
tesla-control -ble -key-file private_key.pem add-key public_key.pem YOUR_VIN
```

### 5. Start HTTP Proxy

```bash
# Start the proxy server
tesla-http-proxy \
    -tls-key config/tls-key.pem \
    -cert config/tls-cert.pem \
    -key-file private_key.pem \
    -port 4443

# Proxy will run on https://localhost:4443
```

### 6. Update Ecolit Configuration

Add proxy settings to your `config.yaml`:

```yaml
tesla:
  enabled: true
  # Your existing Fleet API credentials remain unchanged
  refresh_token: "your_refresh_token"
  client_id: "your_client_id"
  client_secret: "your_client_secret"
  vehicle_id: "your_vehicle_id"
  vehicle_tag: "your_vehicle_tag"
  
  # Add these new TVCP proxy settings
  use_tvcp_proxy: true
  proxy_base_url: "https://localhost:4443"
  
  # Existing settings
  min_charging_amps: 6
  max_charging_amps: 20
```

## Testing

Test your setup:

```bash
# Test with ecolit
make tesla-control

# Try setting charging amperage - should work without 403 errors
```

## Troubleshooting

### Common Issues

1. **"Key not added to vehicle"**
   - Ensure you added the public key to your vehicle using `tesla-control -ble add-key`
   - Vehicle must be awake during key addition

2. **"TLS certificate errors"**
   - Ensure the proxy certificate is valid
   - Check that the proxy is running on the expected port

3. **"Connection refused"**
   - Verify the HTTP proxy is running: `curl -k https://localhost:4443/api/1/status`
   - Check firewall settings

4. **"Still getting 403 errors"**
   - Confirm your vehicle has 2024+ firmware requiring TVCP
   - Verify the proxy is properly routing to Tesla's Fleet API

### Log Analysis

Check proxy logs for detailed error information:

```bash
# Proxy logs show detailed request/response information
tesla-http-proxy -tls-key config/tls-key.pem -cert config/tls-cert.pem -key-file private_key.pem -port 4443 -verbose
```

## Security Notes

- Keep your `private_key.pem` secure and backed up
- The HTTP proxy runs locally and bridges to Tesla's servers
- TLS certificates ensure secure local communication
- Consider running the proxy as a system service for production use

## Production Deployment

For production deployments:

1. **Run as system service**: Use systemd or similar to auto-restart
2. **Secure storage**: Store private keys in secure location
3. **Monitoring**: Monitor proxy health and restart if needed
4. **Updates**: Keep Tesla SDK updated for latest protocol changes
5. **Backup**: Backup all certificates and keys securely

## Further Reading

- [Tesla Vehicle Command GitHub](https://github.com/teslamotors/vehicle-command)
- [Tesla Fleet API Documentation](https://developer.tesla.com/docs/fleet-api)
- [TVCP Announcement](https://developer.tesla.com/docs/fleet-api/support/announcements#2023-10-09-rest-api-vehicle-commands-endpoint-deprecation-warning)
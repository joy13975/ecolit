# Tesla Integration - Complete Setup and Operations

**TL;DR**: Tesla Fleet API requires OAuth tokens for data access, partner registration for API acceptance, and TVCP for modern vehicle commands. Wall Connector monitoring uses the same API with `energy_device_data` scope.

## Prerequisites

- Tesla Developer Account at https://developer.tesla.com/
- Tesla App configured with scopes: `openid offline_access vehicle_device_data vehicle_cmds vehicle_charging_cmds vehicle_location energy_device_data`
- Redirect URI: `http://localhost:8750/callback`
- Origin URL: `https://joy13975.github.io`
- Public key hosted at: https://joy13975.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem

## Complete Setup (First Time)

### Initial Setup Command
```bash
make tesla-mint
```

**What this does:**
1. OAuth Flow - Opens browser for Tesla authorization
2. Token Exchange - Gets access_token and refresh_token  
3. Partner Registration - Registers app with Fleet API
4. Config Update - Saves all tokens to `config.yaml`

**Expected result:**
```yaml
tesla:
  refresh_token: NA_xxxxx...
  access_token: eyJhbGciOi...
  token_expires_in: 28800
```

### Ongoing Maintenance

**Token Refresh (run weekly/monthly):**
```bash
make tesla-refresh
```

## TVCP Setup for Modern Vehicles

**⚠️ CRITICAL**: 2024+ Tesla vehicles require TVCP for ALL commands (charging control, start/stop). Vehicle data reading works without TVCP, but commands will fail with 403 Forbidden errors.

### Prerequisites for TVCP
- Go 1.23.0+ installed
- Modern Tesla vehicle (2024+ firmware)

### Setup Steps

1. **Install Tesla Vehicle Command SDK**
```bash
git clone https://github.com/teslamotors/vehicle-command.git
cd vehicle-command
go get ./...
go build ./...
go install ./...
```

2. **Generate Authentication Keys**
```bash
export TESLA_KEY_NAME=$(whoami)
tesla-keygen create > public_key.pem
```

3. **Create TLS Certificates**
```bash
mkdir -p config
openssl req -x509 -nodes -newkey ec \
    -pkeyopt ec_paramgen_curve:secp521r1 \
    -pkeyopt ec_param_enc:named_curve \
    -subj '/CN=localhost' \
    -keyout config/tls-key.pem \
    -out config/tls-cert.pem
```

4. **Add Key to Vehicle**
```bash
tesla-control -ble -key-file private_key.pem add-key public_key.pem YOUR_VIN
```

5. **Start HTTP Proxy**
```bash
tesla-http-proxy \
    -tls-key config/tls-key.pem \
    -cert config/tls-cert.pem \
    -key-file private_key.pem \
    -port 4443
```

6. **Update Configuration**
```yaml
tesla:
  # Existing credentials
  refresh_token: "your_refresh_token"
  client_id: "your_client_id"
  client_secret: "your_client_secret"
  vehicle_id: "your_vehicle_id"
  vehicle_tag: "your_vehicle_tag"
  
  # Add TVCP proxy settings
  use_tvcp_proxy: true
  proxy_base_url: "https://localhost:4443"
  
  min_charging_amps: 6
  max_charging_amps: 20
```

## Wall Connector Integration

Tesla Wall Connector Gen 3 monitoring is included automatically with Fleet API setup.

### Prerequisites for Wall Connector
1. Tesla Fleet API with `energy_device_data` scope
2. Wall Connector registered in Tesla app under "Products"  
3. Wall Connector connected to WiFi

### Available Data
- Real-time power consumption (W)
- Wall Connector state (Standby, Charging, Vehicle Connected)
- Battery conditioning/trickle charging detection
- Connection status

**Normal Behavior**: 500-700W consumption when vehicle connected but not charging is normal for battery conditioning and thermal management.

## Public Key Infrastructure

Pre-configured keys in `data/tesla-reg-keys/`:
```
data/tesla-reg-keys/
├── private-key.pem                    # Secret key for command signing
├── public-key.pem                     # Public key (local copy)
└── joy13975.github.io/                # GitHub Pages repo
    └── .well-known/appspecific/
        └── com.tesla.3p.public-key.pem # Hosted public key
```

## Token Types and Regional Configuration

### User Tokens
- **Purpose**: Access vehicle data and send commands
- **Lifespan**: access_token ~8 hours, refresh_token long-lived
- **Usage**: `Authorization: Bearer {access_token}`

### Partner Tokens  
- **Purpose**: Register with Tesla Fleet API
- **Grant**: client_credentials
- **Usage**: Registration API calls only

### Regional Detection
Auto-detected from token prefixes:
- `NA_*` → North America endpoints
- `EU_*` → Europe endpoints  
- `AP_*` → Asia-Pacific endpoints

## Testing and Verification

```bash
make tesla-list          # Test API connectivity
make tesla-test          # Safe read-only testing
make tesla-control       # Interactive control interface
make run                 # Full ecolit integration
```

## Implementation Examples

### Vehicle Data Access
```python
async def get_tesla_data():
    try:
        vehicle_data = await tesla_api.get_vehicle_data()
        charging_state = vehicle_data.get('charge_state', {})
        
        return {
            'battery_level': charging_state.get('battery_level'),  # EV SOC (%)
            'charging_state': charging_state.get('charging_state'),
            'charge_current_request': charging_state.get('charge_current_request'),
            'charger_power': charging_state.get('charger_power'),  # kW
            'charge_energy_added': charging_state.get('charge_energy_added')
        }
    except Exception as e:
        logger.error(f"Tesla API error: {e}")
        return None
```

### Charging Control with Rate Limiting
```python
class TeslaController:
    def __init__(self):
        self.current_amps = 0
        self.last_update = 0
        
    async def update_charging(self, target_amps: int):
        now = time.time()
        
        # Limit to 2A change per 30 seconds
        if now - self.last_update < 30:
            max_change = 2
            if abs(target_amps - self.current_amps) > max_change:
                target_amps = self.current_amps + (
                    max_change if target_amps > self.current_amps else -max_change
                )
        
        if target_amps != self.current_amps:
            target_amps = max(6, min(20, target_amps))  # Safety bounds
            success = await tesla_api.charge_set_limit(target_amps)
            if success:
                self.current_amps = target_amps
                self.last_update = now
```

## Troubleshooting

### Common Errors

**403 "missing scopes vehicle_device_data"**
- Update Tesla app with required scopes, run `make tesla-mint`

**412 "must be registered in the current region"**  
- Verify public key accessible, run `make tesla-refresh`

**Authentication failures**
- Run `make tesla-refresh` to refresh, or `make tesla-mint` to re-authorize

**TVCP "Key not added to vehicle"**
- Add public key using `tesla-control -ble add-key`
- Vehicle must be awake during key addition

**Wall Connector "No devices found"**
- Ensure `energy_device_data` scope in token
- Wall Connector must be registered in Tesla app

### Scope Verification
```bash
python3 -c "
import base64, json
with open('config.yaml') as f:
    for line in f:
        if 'access_token:' in line:
            token = line.split(': ')[1].strip()
            payload = token.split('.')[1] + '==='
            data = json.loads(base64.urlsafe_b64decode(payload))
            print('Current scopes:', data.get('scp', []))
            break
"
```

**Required scopes**: `['openid', 'offline_access', 'vehicle_device_data', 'vehicle_cmds', 'vehicle_charging_cmds', 'vehicle_location', 'energy_device_data']`

## Daily Workflow

1. **Initial setup** (once): `make tesla-mint`
2. **Regular refresh** (weekly/monthly): `make tesla-refresh`  
3. **Monitor charging**: `make run`
4. **Test connectivity**: `make tesla-list`
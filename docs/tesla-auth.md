# Tesla Fleet API Setup - Complete ecolit Integration

**TL;DR**: Tesla Fleet API requires (A) User OAuth tokens for vehicle data access and (B) Partner registration so Tesla accepts your app. This guide covers the complete setup using ecolit's automated scripts.

## Prerequisites

Before starting, ensure you have:

1. **Tesla Developer Account**: Register at https://developer.tesla.com/
2. **Tesla App Configuration**: Create an app with:
   - **Scopes**: `openid offline_access vehicle_device_data vehicle_cmds vehicle_charging_cmds vehicle_location`  
   - **Redirect URI**: `http://localhost:8750/callback`
   - **Origin URL**: `https://joy13975.github.io`
3. **Public Key Setup**: Already prepared in `data/tesla-reg-keys/`
   - EC secp256r1 keypair generated and ready
   - Public key hosted at: https://joy13975.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem
   - GitHub Pages repository set up for domain verification

## Initial Setup

### Complete Tesla Setup (`make tesla-mint`)

Run this **once** for complete initial setup:

```bash
make tesla-mint
```

**What this does:**
1. **OAuth Flow**: Opens browser for Tesla authorization
2. **Token Exchange**: Gets access_token and refresh_token
3. **Partner Registration**: Registers your app with Fleet API
4. **Config Update**: Saves all tokens to `config.yaml`

This single command handles everything needed for first-time setup.

**Expected result:**
```yaml
tesla:
  refresh_token: NA_xxxxx...
  access_token: eyJhbGciOi...
  token_expires_in: 28800
```

### Troubleshooting Initial Setup

- **Port 8750 in use**: Stop other applications using this port
- **Browser doesn't open**: Manually visit the displayed authorization URL
- **invalid_redirect_uri**: Ensure Tesla app redirect URI exactly matches `http://localhost:8750/callback`
- **Missing scopes**: Verify Tesla app has all required scopes enabled in developer portal
- **Registration fails**: Check public key is accessible at https://joy13975.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem

## Ongoing Maintenance

### Token Refresh (`make tesla-refresh`)

Run this **periodically** to keep tokens fresh:

```bash
make tesla-refresh
```

**What this does:**
1. **Token Refresh**: Updates expired access_token using refresh_token
2. **Partner Registration**: Registers your app with Tesla Fleet API (idempotent)
3. **Config Update**: Saves refreshed tokens to `config.yaml`

**Example output:**
```
🔄 Refreshing user access token...
✅ User token refreshed successfully!
==================================================
🔐 Getting partner token for Fleet API registration...
✅ Partner account registered successfully
```

### Troubleshooting Token Refresh

- **No refresh token**: Run `make tesla-mint` first for initial setup
- **401 Unauthorized**: Token expired or invalid - run `make tesla-mint` again
- **Partner registration fails**: Verify public key is still accessible at required URL

## Public Key Infrastructure

The Tesla registration keys are pre-configured in `data/tesla-reg-keys/`:

```
data/tesla-reg-keys/
├── private-key.pem                    # Secret key for command signing
├── public-key.pem                     # Public key (local copy)
└── joy13975.github.io/                # GitHub Pages repo
    └── .well-known/appspecific/
        └── com.tesla.3p.public-key.pem # Hosted public key
```

### Using GitHub Pages for Public Key Hosting

The public key is hosted using GitHub Pages (personal page), which is a free and reliable solution:

1. **GitHub Personal Page**: Create a repository named `<username>.github.io`
2. **Well-known Path**: Place the public key at `.well-known/appspecific/com.tesla.3p.public-key.pem`
3. **Enable GitHub Pages**: In repository settings, enable GitHub Pages from main branch
4. **No Jekyll**: Add `.nojekyll` file to prevent Jekyll from ignoring `.well-known` directory
5. **Verify Access**: Test with `curl https://<username>.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem`

This approach is already configured for `joy13975.github.io` and works perfectly with Tesla's requirements.

**Key generation (already done):**
```bash
# Generate EC keypair (secp256r1/prime256v1)
openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem
openssl ec -in private-key.pem -pubout -out public-key.pem

# Copy public key to GitHub Pages repo structure
mkdir -p joy13975.github.io/.well-known/appspecific/
cp public-key.pem joy13975.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem

# Push to GitHub and it's automatically hosted
```

## Token Types and Usage

### User Tokens (Part A)
- **Purpose**: Access vehicle data and send charging commands
- **Scopes**: Determine API access (`vehicle_device_data` critical for readings)
- **Lifespan**: access_token ~8 hours, refresh_token long-lived
- **Usage**: All vehicle API calls use `Authorization: Bearer {access_token}`

### Partner Tokens (Part B)  
- **Purpose**: Register with Tesla Fleet API infrastructure
- **Grant**: client_credentials (app-to-app authentication)
- **Lifespan**: Short-lived, used only for registration
- **Usage**: Registration API calls use partner token

## Integration with ecolit

After successful setup, test your Tesla integration:

```bash
make tesla-list          # Test API connectivity and show vehicle status
make tesla-test          # Safe read-only API testing  
make run                 # Start ecolit with full Tesla integration
```

The Tesla API client automatically:
- Uses your user tokens for vehicle data access
- Refreshes tokens when they expire (every ~8 hours)
- Integrates with EV charging optimization policies
- Provides real-time SOC, charging power, and charging control

### Tesla API Integration Implementation

#### Vehicle Data Access
```python
async def get_tesla_data():
    """Poll Tesla vehicle data for charging optimization"""
    try:
        # Get vehicle state
        vehicle_data = await tesla_api.get_vehicle_data()
        
        # Extract charging information
        charging_state = vehicle_data.get('charge_state', {})
        
        return {
            'battery_level': charging_state.get('battery_level'),  # EV SOC (%)
            'charging_state': charging_state.get('charging_state'),  # Charging, Stopped, etc.
            'charge_current_request': charging_state.get('charge_current_request'),  # Current amps
            'charger_power': charging_state.get('charger_power'),  # Current power (kW)
            'charge_energy_added': charging_state.get('charge_energy_added')  # Session energy
        }
    except Exception as e:
        logger.error(f"Tesla API error: {e}")
        return None
```

#### Charging Current Control
```python
async def set_charging_current(target_amps: int):
    """Set Tesla charging current (6-20A range for typical setups)"""
    try:
        # Validate amperage range
        target_amps = max(6, min(20, target_amps))
        
        # Send command to vehicle
        await tesla_api.charge_set_limit(target_amps)
        
        logger.info(f"⚡ Tesla charging current set to {target_amps}A")
        return True
        
    except Exception as e:
        logger.error(f"Failed to set Tesla current: {e}")
        return False
```

#### Rate Limiting & Safety
```python
class TeslaController:
    def __init__(self):
        self.current_amps = 0
        self.last_update = 0
        
    async def update_charging(self, target_amps: int):
        """Apply rate limiting to prevent rapid changes"""
        now = time.time()
        
        # Limit to 2A change per 30 seconds
        if now - self.last_update < 30:
            max_change = 2
            if abs(target_amps - self.current_amps) > max_change:
                target_amps = self.current_amps + (
                    max_change if target_amps > self.current_amps else -max_change
                )
        
        # Apply the change
        if target_amps != self.current_amps:
            success = await set_charging_current(target_amps)
            if success:
                self.current_amps = target_amps
                self.last_update = now
```

## Regional Configuration  

ecolit auto-detects your Tesla region from token prefixes:
- `NA_*` → North America endpoints (`fleet-api.prd.na.vn.cloud.tesla.com`)
- `EU_*` → Europe endpoints (`fleet-api.prd.eu.vn.cloud.tesla.com`)
- `AP_*` → Asia-Pacific endpoints (`fleet-api.prd.ap.vn.cloud.tesla.com`)

**Note**: Japan users often have NA region tokens (common with Tesla mobile app registration).

## Scope Verification

Check your token has all required scopes:

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

**Required scopes**: `['openid', 'offline_access', 'vehicle_device_data', 'vehicle_cmds', 'vehicle_charging_cmds', 'vehicle_location']`

If `vehicle_device_data` is missing, you'll get 403 errors when reading vehicle data.

## Daily Usage Workflow

1. **Initial setup** (once): `make tesla-mint`
2. **Regular refresh** (weekly/monthly): `make tesla-refresh`  
3. **Monitor charging**: `make run` (integrates with solar/battery optimization)
4. **Test connectivity**: `make tesla-list` (debugging/verification)

## Error Resolution

### 403 "missing scopes vehicle_device_data"
- **Cause**: Tesla app not configured with `vehicle_device_data` scope
- **Fix**: Update Tesla app configuration, then `make tesla-mint`

### 412 "must be registered in the current region"  
- **Cause**: Partner registration incomplete or public key inaccessible
- **Fix**: Verify https://joy13975.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem is accessible, then `make tesla-refresh`

### Authentication failures
- **Cause**: Expired or invalid tokens
- **Fix**: `make tesla-refresh` to refresh, or `make tesla-mint` to re-authorize

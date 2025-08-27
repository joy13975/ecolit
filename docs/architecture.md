# Ecolit Architecture

## Overview

Ecolit is designed as a modular Python application that bridges ECHONET Lite compatible home energy management systems with intelligent energy consumption optimization, particularly focusing on Tesla vehicle charging.

## Core Components

### 1. ECHONET Lite Communication Layer
- **Protocol**: UDP broadcast on port 3610
- **Library**: pychonet for ECHONET Lite protocol implementation
- **Discovery**: Automatic device discovery via multicast
- **Polling**: Configurable interval-based data collection

### 2. Data Processing Pipeline
- Real-time energy flow monitoring
- State tracking for solar production, battery SOC, and grid interaction
- Historical data buffering for trend analysis

### 3. Control Logic
- Mode-based operation (Grid-free, Emergency, etc.)
- Threshold-based decision making
- Safety limits and battery protection

### 4. Tesla Integration (Optional)
- TeslaPy library for vehicle API communication
- Dynamic charging current adjustment
- Vehicle state monitoring

## Data Flow

```
ECHONET Devices → UDP Broadcast → Pychonet Parser → 
    → Data Processor → Control Logic → Tesla API
                     ↓
                  Monitoring/Logging
```

## Deployment Scenarios

### Local Development
- Direct execution on development machine
- Manual configuration via JSON files
- Console logging for debugging

### House Server Deployment
- Continuous operation on local server (Raspberry Pi, NUC, etc.)
- Systemd service management
- Local database for historical data

### Future Cloud Integration
- Remote monitoring capabilities
- API endpoints for mobile/web access
- Cloud-based analytics and optimization

## Network Requirements

- LAN access to ECHONET Lite devices
- Multicast/broadcast capability on local network
- Internet access for Tesla API (when enabled)
- Firewall exceptions for UDP port 3610

## Security Considerations

- Local network isolation recommended
- Secure storage of Tesla refresh tokens
- No direct internet exposure of ECHONET devices
- Configuration file encryption for sensitive data
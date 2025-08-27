# ECHONET Lite Integration

## Protocol Overview

ECHONET Lite is a communication protocol designed for smart home devices, particularly popular in Japan for HEMS (Home Energy Management Systems).

## Key Concepts

### Device Classes
- **Controller Class (0x05)**: Home controllers, energy management controllers
- **Housing/Facility Class (0x02)**: Solar power, storage batteries, electric vehicles
- **Management/Control Class (0x0E)**: Node profile objects

### Common Properties (EPC)

#### Solar Power Generation (0x0279)
- Instantaneous power generation: 0xE0
- Cumulative power generation: 0xE1
- Power generation status: 0x80

#### Storage Battery (0x027D)
- Battery capacity: 0xE0
- Remaining capacity: 0xE2
- Charging/discharging power: 0xD3
- Operation mode: 0xDA

#### Power Distribution Board (0x0287)
- Instantaneous power consumption: 0xE7
- Instantaneous current: 0xE8

## Pychonet Implementation

### Device Discovery
```python
import pychonet as echonet

# Initialize ECHONET instance
enl = echonet.ECHONETAPIClient()

# Start discovery
await enl.discover()

# Access discovered devices
devices = enl.devices
```

### Reading Device Properties
```python
# Get solar generation power
solar = enl.devices.get('solar_power')
if solar:
    power = await solar.get_property(0xE0)  # Instantaneous power
    total = await solar.get_property(0xE1)  # Cumulative generation
```

### Monitoring Changes
```python
# Register callback for property changes
def on_battery_change(device, epc, value):
    if epc == 0xE2:  # Battery remaining capacity
        print(f"Battery SOC: {value}%")

battery.add_listener(on_battery_change)
```

## Typical HEMS Setup

### Device Topology
See [architecture.md](architecture.md#energy-flow-topology) for complete system topology and data flow diagrams.

## Communication Flow

1. **Discovery Phase**
   - Multicast GET to 224.0.23.0:3610
   - Devices respond with instance lists
   - Build device registry

2. **Property Acquisition**
   - GET requests for specific EPCs
   - Parse response frames
   - Update internal state

3. **Continuous Monitoring**
   - Periodic polling of key properties
   - Event-based notifications (if supported)
   - State change callbacks

## Troubleshooting

### Common Issues

1. **No devices discovered**
   - Check network allows multicast
   - Verify ECHONET Lite enabled on devices
   - Ensure same network segment

2. **Property read failures**
   - Device may not support requested EPC
   - Check device documentation
   - Use property map (0x9F) to verify

3. **Intermittent communication**
   - Network congestion
   - Increase timeout values
   - Implement retry logic
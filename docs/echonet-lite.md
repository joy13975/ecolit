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
- Remaining capacity: 0xE2 (in Wh, requires calculation for SOC percentage)
- Charging/discharging power: 0xD3
- Operation mode: 0xDA
- AC charging capacity: 0xA0 (maximum charging capacity in Wh)
- AC discharging capacity: 0xA1 (maximum discharging capacity in Wh)
- Rated capacity: 0xD0 (total battery capacity in Wh)

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

## Home Battery SOC Calculation

### Understanding the Problem

ECHONET Lite's 0xE2 property (REMAINING_STORED_ELECTRICITY) returns raw energy values in watt-hours (Wh), not percentages. Converting this to accurate State of Charge (SOC) percentages requires understanding which capacity value to use as the denominator.

### Investigated EPC Properties

Through systematic investigation, we identified key capacity-related EPCs:

- **0xE2**: Raw remaining energy (11,172 Wh - dynamic)
- **0xA0**: AC charging capacity (11,913 Wh - static)  
- **0xA1**: AC discharging capacity (10,932 Wh - static)
- **0xD0**: Rated capacity (12,700 Wh - static, nameplate)

### Correct Calculation Method

**Formula**: `SOC% = (Raw_0xE2) ÷ ((0xA0 + 0xA1) ÷ 2) × 100`

**Example**: 
- Raw 0xE2: 11,172 Wh
- Average capacity: (11,913 + 10,932) ÷ 2 = 11,422.5 Wh  
- SOC: 11,172 ÷ 11,422.5 × 100 = **97.8%**

This matches wall display readings within ±0.2%, confirming the effective capacity is the average of AC charging and discharging capacities.

### Why This Works

The average of 0xA0 and 0xA1 represents the battery's **effective usable capacity** accounting for:
- Charging efficiency losses
- Discharging efficiency differences  
- Battery protection buffers
- Manufacturer calibration

Using the nameplate capacity (0xD0) or estimated protection ranges produces significant errors (6%+ deviation from wall display).

### Implementation

See `device_poller.py:312-339` for the production implementation with fallback handling for EPC read failures.

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
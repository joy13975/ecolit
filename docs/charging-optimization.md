# Tesla Charging Optimization Algorithm

## Overview
Real-time Tesla charging optimization using ECHONET Lite HEMS data, based on analysis of actual charging patterns and battery coordination strategies.

## Data-Driven Strategy

### Battery-First Coordination (Validated Pattern)
Analysis of actual Tesla and HEMS data reveals that battery SOC timing is critical for optimization:

**Success Pattern (8/27)**:
```
08-09h: Battery 69%→100% (Tesla conservative 1.6-2.3kW)
10-13h: Battery 100%→93% (Tesla aggressive 2.7-4.1kW)
Result: 80% end-of-day battery SOC, 15.5kWh Tesla charging
```

**Failed Patterns (8/25, 8/26)**:
```
08-10h: Battery 20-60% (Tesla aggressive immediately)
Result: 35-46% end-of-day battery SOC, grid dependency
```

### Real-Time Control Algorithm

#### Core Decision Logic
```python
def calculate_tesla_power(battery_soc, solar_surplus_w, grid_flow_w):
    """
    Calculate Tesla charging power based on validated patterns
    
    Args:
        battery_soc: Home battery charge level (%)
        solar_surplus_w: Available solar after house loads (W)
        grid_flow_w: Grid power flow (+import, -export, W)
    
    Returns:
        tesla_power_w: Target Tesla charging power (W)
    """
    
    # Safety: Stop if importing >200W from grid
    if grid_flow_w > 200:
        return 0
        
    # Phase 1: Battery Priority (SOC < 90%)
    if battery_soc < 90:
        # Conservative Tesla power to preserve solar for battery
        max_power = min(
            solar_surplus_w * 0.4,  # Max 40% of surplus
            1600                    # Cap at 1.6kW (8A @ 200V)
        )
        
    # Phase 2: Aggressive Charging (SOC >= 90%)
    else:
        # Use battery as buffer for higher Tesla power
        buffer_power = (battery_soc - 85) * 200  # Battery buffer capacity
        max_power = min(
            solar_surplus_w + buffer_power,
            4000                    # Cap at 4kW (20A @ 200V)
        )
    
    # Export prevention: Boost if exporting to grid
    if grid_flow_w < -100:  # Exporting >100W
        max_power = min(max_power + abs(grid_flow_w), 4000)
    
    return max(0, max_power)

def power_to_amperage(power_w, voltage=200):
    """Convert power to Tesla charging current"""
    amps = power_w / voltage
    return max(6, min(20, int(amps)))  # Tesla 6-20A range
```

#### Rate Limiting & Safety
```python
class TeslaChargeController:
    def __init__(self):
        self.current_amps = 0
        self.last_update = 0
        
    async def update_charging(self, target_amps):
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
            await self.set_tesla_current(target_amps)
            self.current_amps = target_amps
            self.last_update = now
```

## Implementation Requirements

### HEMS Data Integration
```python
async def get_hems_data():
    """Poll critical ECHONET Lite data points"""
    try:
        # Real-time energy data
        solar_power = await solar_device.update(0xE0)     # Instantaneous power (W)
        battery_soc = await battery_device.update(0xE2)   # SOC (%)
        grid_flow = await solar_device.update(0xE5)       # Grid flow (W)
        
        # Calculate house consumption estimate
        house_consumption = estimate_house_load(solar_power, grid_flow, battery_soc)
        solar_surplus = solar_power - house_consumption
        
        return {
            'battery_soc': battery_soc / 100 if battery_soc > 100 else battery_soc,
            'solar_surplus_w': solar_surplus,
            'grid_flow_w': grid_flow
        }
    except Exception as e:
        logger.error(f"HEMS data error: {e}")
        return None
```

### Tesla API Integration
```python
async def control_tesla_charging():
    """Main control loop"""
    hems_data = await get_hems_data()
    if not hems_data:
        return
        
    # Calculate target charging power
    target_power = calculate_tesla_power(
        hems_data['battery_soc'],
        hems_data['solar_surplus_w'], 
        hems_data['grid_flow_w']
    )
    
    target_amps = power_to_amperage(target_power)
    
    # Apply rate limiting and update Tesla
    await charge_controller.update_charging(target_amps)
    
    # Log decision
    logger.info(
        f"🔋 Battery: {hems_data['battery_soc']:.1f}% | "
        f"☀️ Surplus: {hems_data['solar_surplus_w']}W | "
        f"🔌 Grid: {hems_data['grid_flow_w']:+d}W | "
        f"⚡ Tesla: {target_amps}A ({target_power}W)"
    )
```

## Configuration Parameters

### Validated Thresholds
```yaml
tesla_charging:
  # Safety limits (validated from actual data)
  max_amperage: 20              # Breaker protection
  min_amperage: 6               # Tesla minimum
  wall_connector_voltage: 200   # Charging voltage
  
  # Battery coordination (data-driven thresholds)
  min_battery_soc: 70           # Start charging threshold
  battery_priority_soc: 90      # Conservative/aggressive boundary
  
  # Power management (validated from 8/27 success)
  conservative_power: 1600      # Max power when SOC < 90% (W)
  aggressive_power: 4000        # Max power when SOC >= 90% (W)
  
  # Grid protection
  max_grid_import: 200          # Stop charging if importing >200W
  export_boost_enabled: true    # Increase charging if exporting
  
  # Rate limiting (safety)
  max_amp_change: 2             # Max current change per cycle
  adjustment_interval: 30       # Seconds between adjustments
  
  # Operating window
  earliest_start: "06:00"       # No charging before
  latest_end: "20:00"          # No charging after
```

### Monitoring Configuration
```yaml
monitoring:
  poll_interval: 10             # HEMS data polling (seconds)
  data_timeout: 15              # ECHONET timeout (seconds)
  log_level: "INFO"            # Logging detail
  
dashboard:
  update_frequency: 5           # Dashboard refresh (seconds)
  history_retention: 30         # Days of data to keep
  
alerts:
  grid_import_threshold: 500    # Alert if importing >500W (W)
  battery_low_threshold: 30     # Alert if battery <30% (%)
  charging_failure_timeout: 300 # Alert if no charging data (seconds)
```

## Expected Performance

### Improvement Over Manual Control
- **Eliminate gaps**: No 40-minute charging pauses when battery full
- **Extended window**: Continue until solar drops (vs early 14h stop)
- **Consistent power**: Smooth surplus utilization vs manual variation
- **Additional capture**: 2-2.5 kWh improvement potential

### Daily Performance Targets
- **Tesla charging**: 16-18 kWh (vs 15.5 kWh manual best)
- **End-of-day battery SOC**: >80% (vs 80% manual best, 35-46% failed days)
- **Grid dependency**: <15% daily consumption
- **Charging efficiency**: >90% of available surplus utilized

## Validation & Testing

### Success Metrics
```python
def calculate_daily_performance():
    """Calculate daily optimization metrics"""
    return {
        'tesla_kwh': sum(charging_sessions),
        'end_soc': battery_soc_at_sunset,
        'grid_dependency': grid_import / total_consumption,
        'surplus_utilization': tesla_kwh / available_surplus,
        'charging_gaps': count_zero_charging_periods()
    }
```

### Real-Time Validation
- **Power progression**: Conservative → aggressive following battery SOC
- **Grid balance**: Stay within ±200W during charging
- **Battery preservation**: Maintain >70% SOC during Tesla charging
- **Continuous operation**: No gaps during solar availability (8-16h)

---
*Algorithm based on analysis of actual Tesla wall connector data and HEMS energy patterns, designed to improve on manual control baseline with real-time optimization*
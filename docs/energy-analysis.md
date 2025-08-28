# Energy Analysis - Data Patterns and Charging Optimization

## Overview
Analysis of 3 days (2025-08-25 to 27) combining HEMS energy data with Tesla wall connector measurements to understand charging patterns and optimization opportunities.

## Data Sources & Methodology

### HEMS Data Structure
- **Hourly intervals**: Solar generation, consumption, battery charge/discharge, grid import/export, Home Battery SOC
- **Source**: Omron portal samples (JSON format)
- **Integration**: ECHONET protocol via devices with standardized EPC properties

### Tesla Wall Connector Data  
- **5-minute intervals**: Actual Tesla charging power (kW) and session data
- **Ground truth**: Real Tesla energy consumption vs HEMS consumption estimates
- **API Access**: Tesla Fleet API with `energy_device_data` scope
- **Coverage**: Complete charging sessions with precise timing

### Analysis Approach
- **Pattern recognition** without assumptions about optimal strategies
- **Correlation analysis** between Home Battery SOC timing and end-of-day reserves
- **Quantified validation** of HEMS consumption estimates vs Tesla actuals
- **Manual control baseline** for improvement potential assessment

## Daily Energy Patterns

### Summary Data
| Date | Solar | Tesla Actual | HEMS Consumption | End Solar SOC | Grid Import | Strategy |
|------|-------|-------------|------------------|---------------|-------------|----------|
| 0825 | 27.3  | 17.9 kWh    | 37.0 kWh        | 46%           | 6.6 kWh     | Failed   |
| 0826 | 31.5  | 17.8 kWh    | 37.6 kWh        | 35%           | 7.2 kWh     | Poor     |
| 0827 | 33.4  | 15.5 kWh    | 29.2 kWh        | 80%           | 4.0 kWh     | Success  |

### Tesla Charging Characteristics
| Date | Active Hours | Power Range | Charging Window | Pattern       |
|------|-------------|-------------|-----------------|---------------|
| 0825 | 6.1h        | 0.3-6.4kW   | 07:30→23:30     | Early+Gaps    |
| 0826 | 7.8h        | 0.4-3.1kW   | 08:00→16:00     | Early+Continuous |
| 0827 | 7.6h        | 0.5-4.1kW   | 08:00→17:10     | Late+Continuous  |

## Critical Discovery: Home Battery SOC vs Tesla Success

### Home Battery SOC Progression During Solar Hours
```
Hour  0825  0826  0827
--------------------
08h    20    52    69  ← 0827 started with higher SOC
09h    20    54    83
10h    20    60   100  ← 0827 reached 100% early
11h    19    68    99
12h    32    79    99  ← 0827 maintained full battery
13h    37    75    93
17h    46    35    80  ← 0827 preserved evening reserves
```

### Tesla Power Correlation with Home Battery State
**8/25 (Failed Strategy)**:
```
07:30  2.4kW ← Started high power immediately (Home Battery 20%)
08-11h 3.0-3.7kW ← Sustained high power early 
12:25  0.0kW ← 7-hour gap (wasted solar!)
23:00  1.4kW ← Late night charging (grid power)
```

**8/27 (Success Strategy)**:
```
08:00  1.6kW ← Started conservative (Home Battery 69%)
09:50  4.1kW ← Ramped up (Home Battery reached 100%)
11-13h 3.0-4.0kW ← Peak charging with full Home Battery
14h+   0.6kW ← Gradual taper
```

**Key Pattern**: Tesla power increased AFTER Home Battery reached 100%, not before.

### Power Modulation vs Home Battery Status (8/27 Success)
```
Hour  Tesla_kW  Home_Battery_SOC  Coordination
08h     2.0      69→83%           Low Tesla, Home Battery charging
09h     2.3      83→100%          Moderate Tesla, Home Battery filling
10h     1.0      100%             Brief pause (Home Battery full)
11h     3.3      99%              HIGH Tesla with Home Battery buffer
12h     2.7      99→93%           Sustained high with Home Battery support
13h     3.0      93%              Continued aggressive charging
```

## HEMS vs Tesla Data Validation

### Consumption Spike Correlation
```
Hour  Tesla_Actual  HEMS_Spike  Accuracy
11h      3.3kWh      4.0kWh      82%
12h      2.7kWh      3.2kWh      84%  
13h      3.0kWh      3.6kWh      83%
```

**Finding**: HEMS consumption spikes track Tesla charging with 80-85% accuracy. The 15-20% difference represents other high-consumption activities during Tesla charging periods.

### Energy Accounting Validation
```
Date  Tesla_Actual  HEMS_Consumption_Spikes  Ratio
0825     17.9kWh        22.4kWh              0.80
0826     17.8kWh        24.2kWh              0.73
0827     15.5kWh        22.7kWh              0.68
```

**Energy Balance Verification**:
Energy balance follows the equation defined in [architecture.md](architecture.md#energy-balance-equation).
Example validation (0827): 33.4 + 4.0 ≈ 29.2 + 2.0 + 6.2 = 37.4 ✓

## Manual Control Inefficiencies Identified

### Charging Gaps
- **8/25**: 7-hour gap (14h-21h) during peak solar availability
- **8/27**: 40-minute gap (10:15-10:55) when Home Battery reached 100%

### Power Inconsistencies  
- **8/27**: Variation 2.7-3.3kW within peak period suggests imperfect surplus tracking
- **Early tapering**: Dropped to 0.6kW at 14h despite solar available until 16h

### Timing Suboptimalities
- **Late night charging** (8/25): 23h charging uses grid power instead of solar
- **Conservative ending**: Stopped aggressive charging too early vs solar availability

## Data-Driven Insights

### Battery-First Strategy
**Observation**: Days with higher Home Battery SOC before Tesla charging had better end-of-day reserves.

**Mechanism**: 
- Home Battery reaches 90%+ → Tesla can use higher power without competing for solar
- Full Home Battery provides buffer during Tesla charging → less grid dependency
- Result: More evening energy autonomy

### Validated Thresholds
- **Start condition**: Home Battery SOC > 70% (8/27 started at 69% successfully)
- **Conservative phase**: Home Battery SOC < 90% → limit Tesla to ~1.6kW
- **Aggressive phase**: Home Battery SOC ≥ 90% → allow Tesla up to 4kW
- **Rate limiting**: Max 2A change per adjustment cycle

### Tesla Charging Current Conversion
Power to current conversion validated from actual data:
- 1.6kW → 8A (conservative phase)  
- 4.1kW → 20A (aggressive phase, breaker limit)

## Real-Time Control Opportunities

### Improvement Potential Quantified
- **Current manual best**: 15.5 kWh (8/27)
- **Optimal theoretical**: 17-18 kWh based on available solar hours
- **Gap**: 2-2.5 kWh lost to timing inefficiencies

### Specific Enhancement Areas

**1. Gap Elimination**
- Continuous charging during solar hours (no 40-minute pauses)
- Predictive Home Battery SOC management to avoid charging interruptions

**2. Surplus Utilization**
- Real-time calculation: Solar - house consumption - grid import = Tesla surplus
- Dynamic power adjustment based on actual available energy

**3. Extended Optimization Window**
- Continue charging until solar actually drops (16h) vs conservative 14h stop
- Weather-aware planning for cloud coverage periods

## Success Metrics & Validation

### Daily Performance Indicators
- **Tesla energy captured**: Target 16-18 kWh (weather dependent)
- **End-of-day Home Battery SOC**: Target >80% (vs manual best 80%, failed 35-46%)
- **Grid dependency**: Target <15% daily consumption
- **Charging efficiency**: Tesla kWh / available solar surplus ratio

### Real-Time Validation
- **No charging gaps**: During solar availability windows (8-16h minimum)
- **Power progression**: Conservative → aggressive pattern following Home Battery SOC
- **Grid balance**: Minimize imports, tolerate minor exports for Home Battery health

## Limitations & Considerations

### Data Constraints
- **Weather variations**: Cloud patterns not captured in energy data
- **Manual control timing**: Human reaction delays vs real-time optimization potential  
- **Seasonal factors**: Analysis limited to late summer period

### External Variables
- **House load variations**: Unexpected consumption changes not predictable
- **Home Battery aging**: SOC thresholds may require adjustment over time
- **Grid conditions**: Utility demand charges not factored in analysis

## Implementation Requirements

### HEMS Data Integration
- **Real-time access**: Home Battery SOC (EPC 0xE2), Solar power (EPC 0xE0), Grid flow (EPC 0xE5)
- **Update frequency**: 10-second intervals for responsive control
- **Validation**: Cross-check grid flow for surplus calculations

### Tesla API Integration
- **Control capability**: Set charging current (6-20A range)
- **Status monitoring**: Current charging state, power level, session energy
- **Safety limits**: Breaker protection, rate limiting, emergency stop

### Decision Logic Framework
**Key validated thresholds:**
- Conservative phase: Home Battery SOC < 90% → limit Tesla power 
- Aggressive phase: Home Battery SOC ≥ 90% → allow higher Tesla power
- Grid protection: Stop charging if importing >200W
- Export utilization: Increase charging if exporting >100W

## Next Steps

### Algorithm Development
1. **Real-time surplus calculation**: Solar - house consumption - grid import
2. **Battery-aware power modulation**: Conservative until SOC >90%, then aggressive
3. **Continuous optimization**: Eliminate charging gaps during solar availability

### Validation Requirements
1. **Multi-day testing**: Confirm battery-first strategy across weather conditions
2. **Threshold refinement**: Optimize SOC breakpoints for different scenarios  
3. **Performance monitoring**: Track improvement vs manual control baseline

---
*Analysis methodology: Pattern recognition from actual HEMS and Tesla data without optimization assumptions, validated against user-reported manual control outcomes*
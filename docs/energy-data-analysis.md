# Energy Data Analysis

## Overview
Analysis of 3 days of HEMS energy data (2025-08-25, 26, 27) combined with actual Tesla wall connector data to understand optimal charging timing patterns.

## Data Sources & Methodology

### HEMS Data Structure
- **Hourly intervals**: Solar generation, consumption, battery charge/discharge, grid import/export, battery SOC
- **Source**: Omron portal samples (JSON format)
- **Validation**: Cross-referenced with Tesla wall connector actual consumption

### Tesla Wall Connector Data  
- **5-minute intervals**: Actual Tesla charging power (kW)
- **Ground truth**: Real Tesla energy consumption vs HEMS consumption estimates
- **Coverage**: Complete charging sessions with precise timing

### Analysis Approach
- **Pattern recognition** without assumptions about optimal strategies
- **Correlation analysis** between battery SOC timing and end-of-day reserves
- **Quantified validation** of HEMS consumption estimates vs Tesla actuals

## Key Findings

### Daily Energy Patterns

| Date | Solar | Tesla Actual | HEMS Consumption | End-of-Solar SOC | Grid Import |
|------|-------|-------------|------------------|------------------|-------------|
| 0825 | 27.3  | 17.9 kWh    | 37.0 kWh        | 46%              | 6.6 kWh     |
| 0826 | 31.5  | 17.8 kWh    | 37.6 kWh        | 35%              | 7.2 kWh     |
| 0827 | 33.4  | 15.5 kWh    | 29.2 kWh        | 80%              | 4.0 kWh     |

### Critical Discovery: Timing vs Battery State

**Battery SOC Progression During Solar Hours**:
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

**Tesla Charging Power Correlation**:
```
0827 Success Pattern:
08h: Tesla 2.0kW, Battery 69% → 83% (low Tesla power, battery priority)
09h: Tesla 2.3kW, Battery 83% → 100% (moderate Tesla power)
11h: Tesla 3.3kW, Battery 99% (HIGH Tesla power after battery full)
12h: Tesla 2.7kW, Battery 99% → 93% (using battery as buffer)
```

### Energy Flow Validation

**HEMS vs Tesla Accuracy**:
- **Correlation**: ~80% match between HEMS consumption spikes and Tesla actual
- **Difference**: ~20% represents other high-consumption activities during charging periods
- **Baseline consumption**: 0.3-0.7 kWh/hour house loads

**Tesla Charging Characteristics**:
- **Power range**: 0.5kW - 6.4kW depending on conditions
- **Typical progression**: Start conservative, ramp to aggressive, taper with solar decline
- **Duration**: 6-8 hours active charging over 9-17 hour windows

## Data-Driven Insights

### Battery-First Strategy
**Observation**: Days with higher battery SOC before Tesla charging had better end-of-day reserves.

**Mechanism**: 
- Battery reaches 90%+ → Tesla can use higher power without competing for solar
- Full battery provides buffer during Tesla charging → less grid dependency
- Result: More evening energy autonomy

### Power Modulation Impact
**8/25 (Failed)**: Started 2.4kW immediately when battery 20% → gaps and poor end SOC
**8/27 (Success)**: Started 1.6kW when battery 69%, ramped to 4.1kW when battery 100% → 80% end SOC

### Efficiency Opportunities
**Manual control gaps identified**:
- 40-minute charging pause when battery reached 100% (wasted solar)
- Early tapering at 14h despite solar availability until 16h
- Power inconsistency (2.7-3.3kW variation) suggesting imperfect surplus tracking

**Quantified improvement potential**: 2-2.5 kWh additional Tesla charging with real-time optimization

## Technical Validation

### Energy Balance Verification
```
Solar + Grid Import ≈ Consumption + Grid Export + Battery Net Storage

Example (0827):
33.4 + 4.0 ≈ 29.2 + 2.0 + 6.2
37.4 ≈ 37.4 ✓
```

### Temporal Consistency
- Battery SOC progression follows charge/discharge patterns
- Tesla charging periods align with HEMS consumption spikes
- Solar availability correlates with charging window opportunities

## Limitations & Considerations

### Data Constraints
- **Weather variations**: Cloud patterns not captured in energy data
- **Manual control timing**: Human reaction delays vs real-time optimization potential  
- **Seasonal factors**: Analysis limited to late summer period

### External Variables
- **House load variations**: Unexpected consumption changes not predictable
- **Battery aging**: SOC thresholds may require adjustment over time
- **Grid conditions**: Utility demand charges not factored in analysis

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
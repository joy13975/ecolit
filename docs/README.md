# Ecolit Documentation

Tesla charging optimization using ECHONET Lite HEMS data for real-time PV surplus management.

## 📋 Documentation Index

### 🔧 **System Architecture & Setup**
- [**architecture.md**](architecture.md) - System overview and component relationships
- [**configuration.md**](configuration.md) - Configuration settings and parameters
- [**echonet-lite.md**](echonet-lite.md) - ECHONET Lite protocol implementation details

### 📊 **Data Analysis & Insights**
- [**energy-analysis.md**](energy-analysis.md) - Energy data analysis, Tesla charging patterns and optimization insights
- [**charging-optimization.md**](charging-optimization.md) - Optimization algorithm and implementation

## 🎯 **Quick Start Guide**

1. **Understanding the System**: Start with [architecture.md](architecture.md) for system overview
2. **Data Insights**: Read [energy-analysis.md](energy-analysis.md) for data-driven learnings and Tesla charging patterns
4. **Implementation**: Use [charging-optimization.md](charging-optimization.md) for algorithm details

## 🔍 **Key Findings Summary**

### **Home Battery-First Strategy Validation**
- **8/27 Success**: Home Battery 69%→100% first, then Tesla charging at higher power
- **8/25/26 Failure**: Tesla charging competed with home battery for solar energy
- **Result**: End-of-solar Home Battery SOC 80% vs 35-46% with poor timing

### **Tesla Charging Reality**
- **Actual consumption**: 15.5-17.9 kWh (wall connector verified)
- **Power modulation**: 1.6kW → 4.1kW progression based on home battery state
- **Timing critical**: Home Battery SOC when Tesla starts determines success

### **Real-Time Control Opportunity**  
- **Manual gaps**: 40-minute charging pause when home battery reached 100%
- **Improvement potential**: 2-2.5 kWh additional charging with real-time optimization
- **Grid independence**: Better evening energy autonomy with optimal timing

---
*Documentation based on analysis of actual HEMS and Tesla wall connector data, August 25-27, 2025*
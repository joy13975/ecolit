#!/usr/bin/env python3
"""
Tesla Wall Connector Data Analysis

Analyzes actual Tesla charging data from wall connector logs
to validate and refine our understanding of charging patterns.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import matplotlib.pyplot as plt

def load_tesla_data():
    """Load Tesla wall connector data for all 3 days."""
    base_dir = Path(__file__).parent.parent
    tesla_dir = base_dir / "data" / "tesla" / "wallconnector"
    
    tesla_data = {}
    
    for csv_file in tesla_dir.glob("*.csv"):
        date = csv_file.stem
        
        # Load Tesla wall connector data (5-minute intervals)
        df = pd.read_csv(csv_file)
        df['datetime'] = pd.to_datetime(df['Date time'])
        df['hour'] = df['datetime'].dt.hour
        df['minute'] = df['datetime'].dt.minute
        df['time_decimal'] = df['hour'] + df['minute'] / 60.0
        
        tesla_data[date] = df
        
    return tesla_data

def analyze_tesla_patterns():
    """Analyze Tesla charging patterns from wall connector data."""
    tesla_data = load_tesla_data()
    
    print("⚡ TESLA WALL CONNECTOR DATA ANALYSIS")
    print("=" * 80)
    
    for date in sorted(tesla_data.keys()):
        df = tesla_data[date]
        
        print(f"\n📅 {date} (2025-08-{date[-2:]})")
        print("-" * 50)
        
        # Calculate daily Tesla totals
        charging_df = df[df['Vehicle (kW)'] > 0.1]  # Filter out near-zero values
        
        if len(charging_df) == 0:
            print("❌ No Tesla charging detected")
            continue
            
        # Calculate total energy (kW * hours = kWh, but data is 5-minute intervals)
        # Each interval is 5 minutes = 1/12 hour
        total_kwh = (df['Vehicle (kW)'] * (5/60)).sum()
        active_intervals = len(charging_df)
        active_hours = active_intervals * 5 / 60
        
        print(f"🔋 Total Tesla charging: {total_kwh:.1f} kWh")
        print(f"⏱️  Active charging time: {active_hours:.1f} hours ({active_intervals} intervals)")
        
        # Charging periods analysis
        charging_start = charging_df.iloc[0]['datetime'] if len(charging_df) > 0 else None
        charging_end = charging_df.iloc[-1]['datetime'] if len(charging_df) > 0 else None
        
        if charging_start and charging_end:
            start_time = charging_start.strftime("%H:%M")
            end_time = charging_end.strftime("%H:%M") 
            duration = (charging_end - charging_start).total_seconds() / 3600
            
            print(f"🕐 Charging window: {start_time} → {end_time} ({duration:.1f}h span)")
        
        # Power levels analysis
        if len(charging_df) > 0:
            avg_power = charging_df['Vehicle (kW)'].mean()
            max_power = charging_df['Vehicle (kW)'].max()
            min_power = charging_df['Vehicle (kW)'].min()
            
            print(f"⚡ Power levels: {min_power:.1f}kW → {avg_power:.1f}kW avg → {max_power:.1f}kW max")
        
        # Hourly charging pattern
        hourly_charging = df.groupby('hour')['Vehicle (kW)'].sum() * (5/60)  # Convert to kWh
        peak_hours = hourly_charging[hourly_charging > 0.5]  # Hours with significant charging
        
        if len(peak_hours) > 0:
            print(f"🎯 Peak charging hours: {', '.join([f'{h:02d}h' for h in peak_hours.index])}")
            print(f"📊 Hourly breakdown:")
            for hour in peak_hours.index:
                kwh = peak_hours[hour]
                print(f"   {hour:02d}h: {kwh:.1f}kWh")

def compare_tesla_vs_hems():
    """Compare Tesla data with HEMS consumption data."""
    tesla_data = load_tesla_data()
    
    # Load HEMS data for comparison
    base_dir = Path(__file__).parent.parent
    hems_dir = base_dir / "data" / "omron_portal_samples" / "daily"
    
    print(f"\n🔍 TESLA vs HEMS CONSUMPTION COMPARISON")
    print("=" * 80)
    
    for date in sorted(tesla_data.keys()):
        tesla_df = tesla_data[date]
        
        # Load corresponding HEMS data
        hems_file = hems_dir / f"{date}.json"
        if not hems_file.exists():
            continue
            
        import json
        with open(hems_file) as f:
            hems_raw = json.load(f)
        
        # Extract HEMS consumption data
        consumption_data = None
        for series in hems_raw['graphDatas']:
            if series['name'].startswith('消費'):
                consumption_data = [float(x) if x is not None else 0 for x in series['data']]
                break
        
        if not consumption_data:
            continue
            
        # Calculate totals
        tesla_total = (tesla_df['Vehicle (kW)'] * (5/60)).sum()
        hems_consumption_total = sum(consumption_data)
        baseline_consumption = sorted(consumption_data)[6]  # ~25th percentile as baseline
        high_consumption_total = sum([max(0, x - baseline_consumption) for x in consumption_data])
        
        print(f"\n📅 {date} Energy Accounting:")
        print(f"⚡ Tesla (actual):           {tesla_total:6.1f} kWh")
        print(f"🏠 HEMS total consumption:   {hems_consumption_total:6.1f} kWh")
        print(f"📊 HEMS baseline (house):    {baseline_consumption * 24:6.1f} kWh")
        print(f"📈 HEMS consumption spikes:  {high_consumption_total:6.1f} kWh")
        print(f"🔍 Tesla vs spike ratio:     {tesla_total / high_consumption_total:.2f}")
        
        # Hour-by-hour comparison for charging periods
        tesla_hourly = tesla_df.groupby('hour')['Vehicle (kW)'].sum() * (5/60)
        
        print(f"\n⏰ Hourly Tesla Charging vs HEMS Consumption:")
        print(f"Hour Tesla  HEMS   Baseline  Spike")
        print("-" * 35)
        
        for hour in range(6, 18):  # Focus on daylight hours
            tesla_h = tesla_hourly.get(hour, 0)
            hems_h = consumption_data[hour] if hour < len(consumption_data) else 0
            baseline_h = baseline_consumption
            spike_h = max(0, hems_h - baseline_h)
            
            if tesla_h > 0.1 or spike_h > 0.5:  # Show significant periods
                print(f"{hour:02d}h  {tesla_h:5.1f}  {hems_h:5.1f}    {baseline_h:5.1f}    {spike_h:5.1f}")

def identify_charging_strategies():
    """Identify the actual charging strategies used on each day."""
    tesla_data = load_tesla_data()
    
    print(f"\n🎯 TESLA CHARGING STRATEGY ANALYSIS")
    print("=" * 80)
    
    strategies = {}
    
    for date in sorted(tesla_data.keys()):
        df = tesla_data[date]
        charging_df = df[df['Vehicle (kW)'] > 0.1]
        
        if len(charging_df) == 0:
            continue
            
        # Extract strategy characteristics
        start_hour = charging_df.iloc[0]['hour']
        end_hour = charging_df.iloc[-1]['hour']
        total_kwh = (df['Vehicle (kW)'] * (5/60)).sum()
        
        # Power ramping analysis
        power_values = charging_df['Vehicle (kW)'].values
        initial_power = power_values[0]
        max_power = max(power_values)
        
        # Charging gaps (periods where charging stopped then resumed)
        gaps = []
        charging_hours = set(charging_df['hour'].unique())
        for h in range(start_hour, end_hour + 1):
            if h not in charging_hours:
                gaps.append(h)
        
        strategies[date] = {
            'start_hour': start_hour,
            'end_hour': end_hour, 
            'total_kwh': total_kwh,
            'initial_power': initial_power,
            'max_power': max_power,
            'gaps': gaps,
            'duration_hours': end_hour - start_hour + 1,
            'active_hours': len(charging_df) * 5 / 60
        }
        
        print(f"\n📅 {date} Strategy:")
        print(f"🕐 Charging window: {start_hour:02d}:xx → {end_hour:02d}:xx ({end_hour - start_hour + 1}h span)")
        print(f"⚡ Power progression: {initial_power:.1f}kW → {max_power:.1f}kW")
        print(f"🔋 Total energy: {total_kwh:.1f}kWh in {len(charging_df) * 5 / 60:.1f}h active")
        
        if gaps:
            gap_str = ', '.join([f'{g:02d}h' for g in gaps])
            print(f"⏸️  Charging gaps: {gap_str}")
        else:
            print(f"▶️  Continuous charging")
    
    # Compare strategies
    print(f"\n📊 STRATEGY COMPARISON:")
    print(f"Date  Start  End  Duration  Total   Max     Strategy")
    print(f"      Hour   Hour   (h)     (kWh)  (kW)    Pattern")
    print("-" * 60)
    
    for date, strategy in strategies.items():
        pattern = "Early" if strategy['start_hour'] <= 8 else "Late"
        if strategy['gaps']:
            pattern += "+Gaps"
        else:
            pattern += "+Continuous"
            
        print(f"{date}   {strategy['start_hour']:02d}     {strategy['end_hour']:02d}      "
              f"{strategy['duration_hours']:2d}      {strategy['total_kwh']:4.1f}   "
              f"{strategy['max_power']:3.1f}    {pattern}")

def main():
    """Main analysis function."""
    print("🔍 Tesla Wall Connector Data Analysis")
    print("=" * 80)
    
    analyze_tesla_patterns()
    compare_tesla_vs_hems()
    identify_charging_strategies()

if __name__ == "__main__":
    main()
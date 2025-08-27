#!/usr/bin/env python3
"""
EV Charging Test Data Generation

Generate realistic 10-second interval test data focused on grid_power_flow patterns
which are the PRIMARY control input for current EV charging policies.

Current EV policies only use:
- grid_power_flow (main decision factor): +import, -export
- Other metrics collected but unused: home_battery_soc, battery_power, solar_power
"""

import json
import csv
from pathlib import Path
from typing import Dict, List, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import numpy as np
import pandas as pd


@dataclass
class EnergyMetrics:
    """Energy system metrics matching the EnergyMetrics dataclass from policies.py"""
    timestamp: str
    home_battery_soc: float | None = None  # Home Battery SOC (%) - NOT EV SOC
    battery_power: int | None = None  # Home battery power (+charging, -discharging)
    grid_power_flow: int | None = None  # Grid power flow (+import, -export) - PRIMARY CONTROL INPUT
    solar_power: int | None = None  # Solar power (collected but unused by current policies)


class TestDataGenerator:
    """Generate realistic 10-second interval test data from hourly Omron data."""
    
    def __init__(self, data_dir: Path):
        """Initialize with path to Omron daily data directory."""
        self.data_dir = data_dir
        self.raw_data = {}
        self.load_omron_data()
        
    def load_omron_data(self) -> None:
        """Load and parse Omron daily JSON files."""
        print("Loading Omron hourly data...")
        
        for json_file in self.data_dir.glob("*.json"):
            date = json_file.stem
            print(f"  Loading {date}...")
            
            with open(json_file) as f:
                raw = json.load(f)
            
            # Parse energy metrics from Japanese labels
            parsed = {}
            for series in raw["graphDatas"]:
                name = series["name"].split(" <")[0]  # Remove HTML
                data_points = [float(x) if x is not None else np.nan for x in series["data"]]
                
                if name == "消費":  # Consumption
                    parsed["consumption"] = data_points
                elif name == "発電":  # Solar generation
                    parsed["solar"] = data_points  
                elif name == "充電":  # Battery charging
                    parsed["battery_charging"] = data_points
                elif name == "放電":  # Battery discharge  
                    parsed["battery_discharge"] = data_points
                elif name == "買電":  # Grid import
                    parsed["grid_import"] = data_points
                elif name == "売電":  # Grid export (negative)
                    parsed["grid_export"] = data_points
                elif name == "蓄電残量":  # Home Battery SOC
                    parsed["home_battery_soc"] = data_points
                    
            self.raw_data[date] = pd.DataFrame(parsed, index=range(24))
            
        print(f"Loaded {len(self.raw_data)} days of hourly data")
        
    def calculate_grid_flow(self, df: pd.DataFrame) -> List[float]:
        """
        Calculate grid power flow from import/export data.
        
        Grid flow convention: +import (buying), -export (selling)
        This is the PRIMARY metric used by EV charging policies.
        """
        grid_flow = []
        
        for hour in range(24):
            import_kwh = df.loc[hour, "grid_import"] if not pd.isna(df.loc[hour, "grid_import"]) else 0
            export_kwh = df.loc[hour, "grid_export"] if not pd.isna(df.loc[hour, "grid_export"]) else 0
            
            # Convert kWh to W (assuming 1 hour intervals)
            # Positive = importing, negative = exporting
            import_w = import_kwh * 1000  # kWh to Wh
            export_w = abs(export_kwh) * 1000  # Export is typically negative, make positive for calculation
            
            # Net grid flow: positive = net import, negative = net export
            net_flow_w = import_w - export_w
            grid_flow.append(net_flow_w)
            
        return grid_flow
        
    def interpolate_to_10s(self, hourly_data: List[float], date: str) -> Tuple[List[float], List[str]]:
        """
        Interpolate hourly data to 10-second intervals with realistic variation.
        
        Args:
            hourly_data: 24 hourly data points
            date: Date string for timestamp generation
            
        Returns:
            Tuple of (interpolated_values, timestamps)
        """
        # Create time points: hour 0-23
        hours = np.arange(24)
        
        # Handle NaN values by forward/backward filling
        clean_data = pd.Series(hourly_data).ffill().bfill().values
        
        # Use numpy's linear interpolation (simpler, no scipy dependency)
        # For smoother transitions, we'll add some curve fitting with polynomial fit
        def smooth_interpolate(x_points, y_points, new_x):
            """Simple smooth interpolation using numpy"""
            # First do linear interpolation
            linear_interp = np.interp(new_x, x_points, y_points)
            
            # Add some smoothness with a rolling average
            window_size = min(36, len(linear_interp) // 10)  # ~6 minutes of smoothing
            if window_size > 3:
                smoothed = pd.Series(linear_interp).rolling(window=window_size, center=True, min_periods=1).mean().values
                return smoothed
            else:
                return linear_interp
        
        # Generate 10-second intervals for 24 hours
        # 24 hours * 3600 seconds/hour / 10 seconds = 8640 data points
        interval_seconds = 10
        total_points = 24 * 3600 // interval_seconds
        time_points = np.linspace(0, 23.999, total_points)
        
        # Interpolate base values using our smooth interpolation
        interpolated = smooth_interpolate(hours, clean_data, time_points)
        
        # Add realistic noise/variation (2-5% of value)
        noise_factor = 0.02 + 0.03 * np.random.random(len(interpolated))
        noise = interpolated * noise_factor * (np.random.random(len(interpolated)) - 0.5)
        interpolated_with_noise = interpolated + noise
        
        # Generate timestamps (handle 2-digit date format)
        if len(date) == 4:  # e.g., "0825"
            base_date = datetime.strptime(f"2025-08-{date[2:]}", "%Y-%m-%d")
        else:
            base_date = datetime.strptime(f"2025-08-{date}", "%Y-%m-%d")
        timestamps = []
        for i, time_point in enumerate(time_points):
            hours_offset = int(time_point)
            minutes_offset = int((time_point - hours_offset) * 60)
            seconds_offset = int(((time_point - hours_offset) * 60 - minutes_offset) * 60)
            
            timestamp = base_date + timedelta(hours=hours_offset, minutes=minutes_offset, seconds=seconds_offset)
            timestamps.append(timestamp.isoformat() + "+09:00")  # JST timezone
            
        return interpolated_with_noise.tolist(), timestamps
        
    def generate_test_data(self, date: str, output_dir: Path) -> None:
        """
        Generate test data for a specific date.
        
        Focus on grid_power_flow as the primary control input for EV charging policies.
        """
        if date not in self.raw_data:
            raise ValueError(f"No data available for date {date}")
            
        df = self.raw_data[date]
        print(f"Generating 10-second interval test data for {date}...")
        
        # Calculate grid power flow (PRIMARY control metric)
        grid_flow_hourly = self.calculate_grid_flow(df)
        
        # Get other metrics (collected but unused by current policies)
        solar_hourly = df["solar"].fillna(0).values
        home_battery_soc_hourly = df["home_battery_soc"].fillna(50).values  # Default 50% if missing
        
        # Calculate home battery power from charging/discharging
        battery_power_hourly = []
        for hour in range(24):
            charging = df.loc[hour, "battery_charging"] if not pd.isna(df.loc[hour, "battery_charging"]) else 0
            discharging = df.loc[hour, "battery_discharge"] if not pd.isna(df.loc[hour, "battery_discharge"]) else 0
            
            # Convert kWh to W, positive = charging, negative = discharging
            charging_w = abs(charging) * 1000
            discharging_w = discharging * 1000
            
            net_power = charging_w - discharging_w
            battery_power_hourly.append(net_power)
        
        # Interpolate all metrics to 10-second intervals
        grid_flow_10s, timestamps = self.interpolate_to_10s(grid_flow_hourly, date)
        solar_power_10s, _ = self.interpolate_to_10s(solar_hourly, date)
        home_battery_soc_10s, _ = self.interpolate_to_10s(home_battery_soc_hourly, date)
        battery_power_10s, _ = self.interpolate_to_10s(battery_power_hourly, date)
        
        # Create EnergyMetrics objects
        test_data = []
        for i in range(len(timestamps)):
            metrics = EnergyMetrics(
                timestamp=timestamps[i],
                home_battery_soc=round(max(0, min(100, home_battery_soc_10s[i])), 1),  # Clamp 0-100%
                battery_power=int(battery_power_10s[i]),
                grid_power_flow=int(grid_flow_10s[i]),  # PRIMARY CONTROL INPUT
                solar_power=int(max(0, solar_power_10s[i]))  # No negative solar
            )
            test_data.append(metrics)
        
        # Save as CSV (for easy analysis)
        csv_file = output_dir / f"test_data_{date}_10s.csv" 
        with open(csv_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['timestamp', 'home_battery_soc', 'battery_power', 'grid_power_flow', 'solar_power'])
            writer.writeheader()
            for metrics in test_data:
                writer.writerow(asdict(metrics))
        
        # Save as JSON (for programmatic use)
        json_file = output_dir / f"test_data_{date}_10s.json"
        with open(json_file, 'w') as f:
            json.dump([asdict(m) for m in test_data], f, indent=2)
        
        # Generate summary statistics
        self.generate_summary(date, test_data, output_dir)
        
        print(f"  Generated {len(test_data)} data points")
        print(f"  Saved CSV: {csv_file}")
        print(f"  Saved JSON: {json_file}")
        
    def generate_summary(self, date: str, test_data: List[EnergyMetrics], output_dir: Path) -> None:
        """Generate summary statistics for the test data."""
        grid_flows = [m.grid_power_flow for m in test_data if m.grid_power_flow is not None]
        socs = [m.home_battery_soc for m in test_data if m.home_battery_soc is not None]
        
        # Focus on grid flow patterns (primary control input)
        export_periods = len([g for g in grid_flows if g < -50])  # Exporting >50W (triggers EV charge increase)
        import_periods = len([g for g in grid_flows if g > 0])    # Importing (triggers EV charge decrease)
        balanced_periods = len([g for g in grid_flows if -50 <= g <= 0])  # Balanced/small export
        
        summary = {
            "date": f"2025-08-{date}",
            "total_data_points": len(test_data),
            "interval_seconds": 10,
            "duration_hours": len(test_data) * 10 / 3600,
            
            # Grid flow analysis (PRIMARY for EV charging decisions)
            "grid_flow_stats": {
                "min_w": min(grid_flows),
                "max_w": max(grid_flows), 
                "avg_w": round(sum(grid_flows) / len(grid_flows), 1),
                "export_periods_gt50w": export_periods,  # Will trigger EV charge increase
                "import_periods": import_periods,         # Will trigger EV charge decrease
                "balanced_periods": balanced_periods,     # Will maintain current EV charge
                "percent_export_opportunities": round(100 * export_periods / len(grid_flows), 1)
            },
            
            # Home Battery SOC analysis (collected but unused)
            "home_battery_soc_stats": {
                "min_percent": round(min(socs), 1),
                "max_percent": round(max(socs), 1),
                "avg_percent": round(sum(socs) / len(socs), 1)
            },
            
            # EV charging policy implications
            "ev_charging_implications": {
                "eco_policy_behavior": {
                    "increase_charging_periods": export_periods,
                    "decrease_charging_periods": import_periods,
                    "maintain_charging_periods": balanced_periods
                },
                "hurry_policy_differences": "Allows charging during imports ≤1000W",
                "primary_control_metric": "grid_power_flow",
                "unused_metrics": ["home_battery_soc", "battery_power", "solar_power"]
            }
        }
        
        summary_file = output_dir / f"test_data_summary_{date}.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
            
        print(f"  Grid flow: {summary['grid_flow_stats']['percent_export_opportunities']}% export opportunities (>50W)")
        print(f"  Home Battery SOC: {summary['home_battery_soc_stats']['min_percent']:.1f}% - {summary['home_battery_soc_stats']['max_percent']:.1f}%")
        print(f"  Summary: {summary_file}")


def main():
    """Generate test data for EV charging policy testing."""
    
    # Setup paths
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    omron_data_dir = project_root / "data" / "omron" / "daily"
    output_dir = project_root / "data" / "generated_test_data"
    output_dir.mkdir(exist_ok=True)
    
    print("🔋 EV Charging Test Data Generator")
    print("=" * 50)
    print("Focused on grid_power_flow patterns (PRIMARY control input)")
    print("Other metrics collected for completeness but unused by current policies")
    print("")
    
    # Initialize generator
    try:
        generator = TestDataGenerator(omron_data_dir)
    except Exception as e:
        print(f"❌ Failed to load Omron data: {e}")
        return
    
    # Generate test data for available dates
    available_dates = sorted(generator.raw_data.keys())
    print(f"Available dates: {available_dates}")
    print("")
    
    for date in available_dates:
        try:
            generator.generate_test_data(date, output_dir)
            print("")
        except Exception as e:
            print(f"❌ Failed to generate data for {date}: {e}")
            print("")
    
    print("✅ Test data generation complete!")
    print(f"📁 Output directory: {output_dir}")
    print("")
    print("📋 Usage Notes:")
    print("- CSV files: Easy analysis and plotting")
    print("- JSON files: Programmatic use with EV charging controller")
    print("- Primary control metric: grid_power_flow (+import, -export)")
    print("- Current policies ignore: home_battery_soc, battery_power, solar_power")
    print("- Test with: `make run` to verify EV charging behavior")


if __name__ == "__main__":
    main()
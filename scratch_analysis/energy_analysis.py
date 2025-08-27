#!/usr/bin/env python3
"""
Energy Data Analysis for Tesla Charging Optimization

Analyzes daily energy patterns to understand optimal Tesla charging timing
based on PV surplus and home battery SOC management.
"""

import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from typing import Dict, List, Tuple

# Set up plotting style
plt.style.use('seaborn-v0_8')
sns.set_palette("husl")

class EnergyAnalyzer:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.daily_data = {}
        self.analysis_results = {}
        
    def load_daily_data(self) -> None:
        """Load and parse JSON energy data for all available days."""
        json_files = list(self.data_dir.glob("*.json"))
        print(f"Found {len(json_files)} data files: {[f.name for f in json_files]}")
        
        for json_file in json_files:
            date_str = json_file.stem  # e.g., "0825"
            print(f"\nLoading {date_str}...")
            
            with open(json_file, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
            
            # Parse the graphDatas structure
            parsed_data = self._parse_graph_data(raw_data['graphDatas'])
            
            # Create proper time index (0-23 hours)
            # Extract day part from date_str (0825 → 25)
            day = date_str[-2:]
            hours = pd.date_range(f"2025-08-{day}", periods=24, freq='h')
            df = pd.DataFrame(parsed_data, index=hours)
            
            self.daily_data[date_str] = df
            print(f"✅ Loaded {date_str}: {len(df)} hours of data")
            
    def _parse_graph_data(self, graph_data: List[Dict]) -> Dict[str, List]:
        """Parse the graphDatas structure into meaningful columns."""
        column_mapping = {
            "消費": "consumption_kwh",
            "発電": "solar_generation_kwh", 
            "充電": "battery_charging_kwh",
            "放電": "battery_discharge_kwh",
            "買電": "grid_import_kwh",
            "売電": "grid_export_kwh",
            "蓄電残量": "battery_soc_pct",
            "外部機器": "external_device_kwh"  # Likely Tesla
        }
        
        parsed = {}
        
        for series in graph_data:
            name = series['name']
            # Clean up HTML tags from names
            clean_name = name.split(' <')[0] if '<' in name else name
            
            if clean_name in column_mapping:
                col_name = column_mapping[clean_name]
                data = series['data']
                
                # Handle null values and convert to float
                clean_data = []
                for val in data:
                    if val is None or val == 'null':
                        clean_data.append(np.nan)
                    else:
                        clean_data.append(float(val))
                
                parsed[col_name] = clean_data
                print(f"  - {clean_name} → {col_name}: {len([x for x in clean_data if not np.isnan(x) if isinstance(x, float)])} valid values")
        
        return parsed
    
    def analyze_daily_patterns(self) -> None:
        """Analyze energy patterns for each day."""
        print("\n" + "="*80)
        print("DAILY ENERGY ANALYSIS")
        print("="*80)
        
        for date, df in self.daily_data.items():
            print(f"\n📅 Analysis for {date} (2025-08-{date})")
            print("-" * 50)
            
            # Calculate daily totals
            totals = {}
            for col in df.columns:
                if col.endswith('_kwh') and col != 'battery_soc_pct':
                    total = df[col].sum()
                    totals[col] = total
            
            # Key metrics
            solar_total = totals.get('solar_generation_kwh', 0)
            consumption_total = totals.get('consumption_kwh', 0)
            grid_import_total = totals.get('grid_import_kwh', 0)
            grid_export_total = abs(totals.get('grid_export_kwh', 0))  # Convert from negative
            battery_charge_total = abs(totals.get('battery_charging_kwh', 0))  # Convert from negative
            battery_discharge_total = totals.get('battery_discharge_kwh', 0)
            
            print(f"☀️  Solar Generation:    {solar_total:6.1f} kWh")
            print(f"🏠 Total Consumption:   {consumption_total:6.1f} kWh") 
            print(f"📥 Grid Import:         {grid_import_total:6.1f} kWh")
            print(f"📤 Grid Export:         {grid_export_total:6.1f} kWh")
            print(f"🔋 Battery Charged:     {battery_charge_total:6.1f} kWh")
            print(f"🔋 Battery Discharged:  {battery_discharge_total:6.1f} kWh")
            
            # Battery SOC analysis
            start_soc = df['battery_soc_pct'].iloc[0] if not pd.isna(df['battery_soc_pct'].iloc[0]) else 0
            end_soc = df['battery_soc_pct'].iloc[-1] if not pd.isna(df['battery_soc_pct'].iloc[-1]) else 0
            max_soc = df['battery_soc_pct'].max()
            
            print(f"🔋 Battery SOC: {start_soc:3.0f}% → {end_soc:3.0f}% (peak: {max_soc:3.0f}%)")
            
            # Identify consumption spikes (may include Tesla + other activities)
            consumption_spikes = self._identify_consumption_spikes(df)
            spike_total = consumption_spikes['total_spike_kwh']
            spike_hours = len(consumption_spikes['spike_periods'])
            
            print(f"📈 Consumption Spikes:  {spike_total:6.1f} kWh over {spike_hours} hours (includes Tesla + other)")
            print(f"   (User reported Tesla: ~13-14kWh for these days)")
            
            # Calculate self-consumption ratio
            self_consumption = solar_total - grid_export_total
            self_consumption_pct = (self_consumption / solar_total * 100) if solar_total > 0 else 0
            
            print(f"🎯 Self-consumption:    {self_consumption_pct:6.1f}% ({self_consumption:.1f}/{solar_total:.1f} kWh)")
            
            # Grid dependency 
            grid_dependency = grid_import_total / consumption_total * 100 if consumption_total > 0 else 0
            print(f"⚡ Grid dependency:     {grid_dependency:6.1f}%")
            
            # Critical timing analysis
            end_of_solar_hour = self._find_end_of_solar_generation(df)
            end_of_solar_soc = df['battery_soc_pct'].iloc[end_of_solar_hour] if end_of_solar_hour < len(df) else end_soc
            
            print(f"🌅 Solar ends ~{end_of_solar_hour:02d}:00, Battery SOC: {end_of_solar_soc:.0f}%")
            
            # Store results
            self.analysis_results[date] = {
                'solar_total': solar_total,
                'consumption_total': consumption_total,
                'grid_import': grid_import_total,
                'grid_export': grid_export_total,
                'consumption_spikes': spike_total,
                'spike_hours': spike_hours,
                'start_soc': start_soc,
                'end_soc': end_soc,
                'max_soc': max_soc,
                'end_of_solar_soc': end_of_solar_soc,
                'self_consumption_pct': self_consumption_pct,
                'grid_dependency': grid_dependency,
                'spike_periods': consumption_spikes['spike_periods'],
                'baseline_consumption': consumption_spikes['baseline_consumption']
            }
            
    def _identify_consumption_spikes(self, df: pd.DataFrame) -> Dict:
        """Identify consumption spikes (may include Tesla + other high-consumption activities)."""
        # Find consumption spikes above baseline - this may include Tesla + other things
        baseline_consumption = df['consumption_kwh'].quantile(0.3)  # 30th percentile as baseline
        
        # Look for periods with consumption > 2x baseline 
        spike_threshold = max(2.0, baseline_consumption * 2)
        
        spike_mask = df['consumption_kwh'] > spike_threshold
        spike_periods = []
        total_spike_kwh = 0
        
        for hour in df.index:
            if spike_mask[hour]:
                consumption = df.loc[hour, 'consumption_kwh']
                # Spike portion (consumption above baseline)
                spike_portion = max(0, consumption - baseline_consumption)
                total_spike_kwh += spike_portion
                spike_periods.append({
                    'hour': hour.hour,
                    'total_consumption': consumption,
                    'spike_above_baseline': spike_portion,
                    'battery_soc': df.loc[hour, 'battery_soc_pct']
                })
        
        return {
            'total_spike_kwh': total_spike_kwh,
            'spike_periods': spike_periods,
            'baseline_consumption': baseline_consumption
        }
    
    def _find_end_of_solar_generation(self, df: pd.DataFrame) -> int:
        """Find the hour when solar generation effectively ends."""
        # Find last hour with meaningful solar generation (>0.1 kWh)
        solar_hours = df[df['solar_generation_kwh'] > 0.1]
        return solar_hours.index[-1].hour if len(solar_hours) > 0 else 16
        
    def compare_optimization_strategies(self) -> None:
        """Compare the three days to identify optimal charging strategy."""
        print("\n" + "="*80)
        print("TESLA CHARGING OPTIMIZATION COMPARISON")
        print("="*80)
        
        results_df = pd.DataFrame(self.analysis_results).T
        results_df = results_df.sort_index()  # Sort by date
        
        print("\n📊 KEY METRICS COMPARISON:")
        print("-" * 50)
        print(f"{'Date':<6} {'Solar':<6} {'Tesla':<6} {'Export':<6} {'End SOC':<8} {'Grid Dep':<8}")
        print(f"{'':>6} {'(kWh)':<6} {'(kWh)':<6} {'(kWh)':<6} {'(%)':<8} {'(%)':<8}")
        print("-" * 50)
        
        for date, results in results_df.iterrows():
            print(f"{date:<6} {results['solar_total']:>6.1f} {results['tesla_charging']:>6.1f} "
                  f"{results['grid_export']:>6.1f} {results['end_of_solar_soc']:>8.0f} "
                  f"{results['grid_dependency']:>8.1f}")
        
        # Analysis of charging timing vs end-of-day SOC
        print("\n🎯 CHARGING TIMING ANALYSIS:")
        print("-" * 50)
        
        for date, results in results_df.iterrows():
            charging_periods = results['charging_periods']
            if charging_periods:
                early_charging = sum(1 for p in charging_periods if p['hour'] < 11)
                total_periods = len(charging_periods)
                early_ratio = early_charging / total_periods
                
                print(f"\n{date} Strategy:")
                print(f"  - Tesla charging periods: {total_periods} hours")
                print(f"  - Early charging (< 11AM): {early_charging} hours ({early_ratio:.1%})")
                print(f"  - End-of-solar battery SOC: {results['end_of_solar_soc']:.0f}%")
                print(f"  - Grid export wasted: {results['grid_export']:.1f} kWh")
                
                # Key insight: correlation between early charging and end SOC
                if early_ratio > 0.5:
                    print(f"  ⚠️  Heavy early charging → Lower end-of-day SOC")
                else:
                    print(f"  ✅ Later charging → Better end-of-day SOC")
        
        # Identify the best day
        best_day = self._identify_best_strategy(results_df)
        print(f"\n🏆 BEST STRATEGY: {best_day}")
        
    def _identify_best_strategy(self, results_df: pd.DataFrame) -> str:
        """Identify which day had the best energy management strategy."""
        # Scoring based on multiple factors
        scores = {}
        
        for date, results in results_df.iterrows():
            score = 0
            
            # Higher end-of-solar SOC is better (more evening autonomy)
            score += results['end_of_solar_soc'] / 100 * 40
            
            # Lower grid export is better (less waste)
            max_export = results_df['grid_export'].max()
            score += (1 - results['grid_export'] / max_export) * 30 if max_export > 0 else 30
            
            # Higher Tesla charging is better (more EV energy)
            max_tesla = results_df['tesla_charging'].max()
            score += (results['tesla_charging'] / max_tesla) * 20 if max_tesla > 0 else 0
            
            # Lower grid dependency is better
            score += (1 - results['grid_dependency'] / 100) * 10
            
            scores[date] = score
            
        best_day = max(scores, key=scores.get)
        return best_day
        
    def create_visualizations(self) -> None:
        """Create visualizations of the energy data."""
        print("\n📈 Creating visualizations...")
        
        fig, axes = plt.subplots(len(self.daily_data), 1, figsize=(15, 5 * len(self.daily_data)))
        if len(self.daily_data) == 1:
            axes = [axes]
            
        for idx, (date, df) in enumerate(self.daily_data.items()):
            ax = axes[idx]
            
            # Plot energy flows
            hours = range(24)
            
            ax.bar(hours, df['solar_generation_kwh'], alpha=0.7, label='Solar Generation', color='gold')
            ax.bar(hours, df['consumption_kwh'], alpha=0.7, label='Consumption', color='red')
            ax.bar(hours, -df['grid_export_kwh'], alpha=0.7, label='Grid Export', color='green')
            ax.bar(hours, df['grid_import_kwh'], alpha=0.7, label='Grid Import', color='orange')
            
            # Plot battery SOC on secondary axis
            ax2 = ax.twinx()
            ax2.plot(hours, df['battery_soc_pct'], 'b-', linewidth=2, label='Battery SOC %')
            ax2.set_ylabel('Battery SOC (%)', color='blue')
            ax2.set_ylim(0, 100)
            
            ax.set_title(f'Energy Flow Analysis - {date} (2025-08-{date})')
            ax.set_xlabel('Hour of Day')
            ax.set_ylabel('Energy (kWh)')
            ax.legend(loc='upper left')
            ax2.legend(loc='upper right')
            ax.grid(True, alpha=0.3)
            ax.set_xlim(0, 23)
            
        plt.tight_layout()
        plt.savefig(self.data_dir.parent / 'energy_analysis_charts.png', dpi=300, bbox_inches='tight')
        print("✅ Charts saved as 'energy_analysis_charts.png'")
        
    def generate_optimization_recommendations(self) -> None:
        """Generate specific recommendations for Tesla charging optimization."""
        print("\n" + "="*80)
        print("TESLA CHARGING OPTIMIZATION RECOMMENDATIONS")
        print("="*80)
        
        results_df = pd.DataFrame(self.analysis_results).T
        
        # Find patterns
        best_end_soc = results_df['end_of_solar_soc'].max()
        best_day = results_df[results_df['end_of_solar_soc'] == best_end_soc].index[0]
        
        print(f"🎯 TARGET: Achieve {best_end_soc:.0f}% battery SOC at end of solar generation")
        print(f"📅 BEST REFERENCE: {best_day} strategy")
        
        print(f"\n⚡ OPTIMAL CHARGING ALGORITHM:")
        print(f"-" * 50)
        
        best_periods = self.analysis_results[best_day]['charging_periods']
        if best_periods:
            charging_hours = [p['hour'] for p in best_periods]
            earliest_hour = min(charging_hours)
            latest_hour = max(charging_hours)
            
            print(f"1. **Battery Priority Phase (6-{earliest_hour:02d}h):**")
            print(f"   - Tesla charging: 0A")
            print(f"   - Let home battery charge from solar first")
            print(f"   - Target: Reach 80-90% home battery SOC")
            
            print(f"\n2. **Surplus Charging Phase ({earliest_hour:02d}-{latest_hour:02d}h):**") 
            print(f"   - Tesla charging: 6-20A based on surplus")
            print(f"   - Calculate: PV surplus = Solar - House - Grid_import")
            print(f"   - Target: Use 13-15kWh that would otherwise export")
            
            print(f"\n3. **End-of-Day Phase ({latest_hour:02d}h+):**")
            print(f"   - Tesla charging: Reduce as solar drops")
            print(f"   - Target: Maintain home battery >70% for evening")
            
        # Specific numerical targets
        avg_export = results_df['grid_export'].mean()
        print(f"\n📊 QUANTIFIED TARGETS:")
        print(f"-" * 30)
        print(f"- Reduce grid export from {avg_export:.1f}kWh → <2kWh")
        print(f"- Achieve end-of-solar battery SOC: >85%")
        print(f"- Tesla charging: 13-15kWh daily (your typical export amount)")
        print(f"- Grid dependency: <10%")
        
        print(f"\n🔧 IMPLEMENTATION PARAMETERS:")
        print(f"-" * 35) 
        print(f"```yaml")
        print(f"tesla_charging:")
        print(f"  max_amperage: 20  # Your breaker limit")
        print(f"  min_home_battery_soc: 70  # Before starting Tesla charge")
        print(f"  surplus_threshold: 1500   # Minimum 1.5kW surplus")
        print(f"  target_daily_kwh: 14      # Target Tesla charging")
        print(f"  charging_window:")
        print(f"    earliest: '{earliest_hour:02d}:00'")
        print(f"    latest: '16:00'")
        print(f"```")

def main():
    """Main analysis function."""
    print("🔍 Energy Data Analysis for Tesla Charging Optimization")
    print("=" * 80)
    
    # Setup paths
    base_dir = Path(__file__).parent.parent
    data_dir = base_dir / "data" / "omron_portal_samples" / "daily"
    
    if not data_dir.exists():
        print(f"❌ Data directory not found: {data_dir}")
        return
        
    # Initialize analyzer
    analyzer = EnergyAnalyzer(data_dir)
    
    # Load and analyze data
    analyzer.load_daily_data()
    analyzer.analyze_daily_patterns()
    analyzer.compare_optimization_strategies()
    analyzer.generate_optimization_recommendations()
    analyzer.create_visualizations()
    
    print(f"\n✅ Analysis complete! Results saved to {data_dir.parent}")

if __name__ == "__main__":
    main()
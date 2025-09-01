"""Metric data synthesis utility for backtesting."""

import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scipy import interpolate


class MetricsSynthesizer:
    """Synthesizes realistic metric data from historical CSV files."""

    def __init__(self, source_csv_path: str, time_compression: float = 1.0):
        """Initialize synthesizer with source data.

        Args:
            source_csv_path: Path to source CSV metrics file
            time_compression: Factor to compress time (e.g., 24 -> 1 hour runtime)
        """
        self.source_path = Path(source_csv_path)
        self.time_compression = time_compression
        self.source_data: list[dict[str, Any]] = []
        self.headers: list[str] = []
        self._load_source_data()

    def _load_source_data(self) -> None:
        """Load source CSV data into memory."""
        if not self.source_path.exists():
            raise FileNotFoundError(f"Source CSV not found: {self.source_path}")

        with open(self.source_path) as f:
            reader = csv.DictReader(f)
            self.headers = reader.fieldnames or []
            self.source_data = list(reader)

        if not self.source_data:
            raise ValueError(f"No data found in source CSV: {self.source_path}")

        print(f"Loaded {len(self.source_data)} records from {self.source_path}")

    def _parse_timestamp(self, ts_str: str) -> datetime:
        """Parse timestamp string to datetime object."""
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00").replace("+00:00", ""))

    def _interpolate_numeric_series(
        self, timestamps: list[datetime], values: list[float], target_timestamps: list[datetime]
    ) -> list[float]:
        """Interpolate numeric values at target timestamps."""
        if len(timestamps) < 2:
            # Not enough data for interpolation, return constant value
            return [values[0] if values else 0.0] * len(target_timestamps)

        # Convert to numeric timestamps for interpolation
        ts_numeric = [(ts - timestamps[0]).total_seconds() for ts in timestamps]
        target_numeric = [(ts - timestamps[0]).total_seconds() for ts in target_timestamps]

        # Use cubic spline for smooth interpolation, fallback to linear if needed
        try:
            interp_func = interpolate.interp1d(
                ts_numeric, values, kind="cubic", bounds_error=False, fill_value="extrapolate"
            )
        except ValueError:
            # Fallback to linear interpolation
            interp_func = interpolate.interp1d(
                ts_numeric, values, kind="linear", bounds_error=False, fill_value="extrapolate"
            )

        interpolated = interp_func(target_numeric)
        return interpolated.tolist()

    def _generate_solar_curve(
        self,
        start_time: datetime,
        duration_hours: float,
        peak_power: float,
        weather_factor: float = 1.0,
        ramp: str = None,
    ) -> list[tuple[datetime, float]]:
        """Generate realistic solar power curve for the day."""
        points = []
        interval_minutes = 2  # 2-minute intervals
        num_points = int((duration_hours * 60) / interval_minutes)

        for i in range(num_points):
            time_offset = timedelta(minutes=i * interval_minutes)
            timestamp = start_time + time_offset

            if ramp == "up":
                # Gradual increase from 0 to peak_power
                progress = i / max(num_points - 1, 1)
                solar_power = progress * peak_power * weather_factor
            elif ramp == "down":
                # Gradual decrease from peak_power to 0
                progress = 1 - (i / max(num_points - 1, 1))
                solar_power = progress * peak_power * weather_factor
            elif peak_power == 0:
                # No solar scenarios (morning/night)
                solar_power = 0.0
            else:
                # Normal solar curve: sine wave from sunrise to sunset (roughly 6 AM to 6 PM)
                hour_of_day = timestamp.hour + timestamp.minute / 60.0
                if 6 <= hour_of_day <= 18:
                    # Normalized sine curve (0 to 1) over 12 hours
                    solar_phase = (hour_of_day - 6) / 12 * math.pi
                    normalized_power = (
                        math.sin(solar_phase) ** 2
                    )  # Squared for more realistic curve
                    solar_power = normalized_power * peak_power * weather_factor

                    # Add some realistic noise (±5%)
                    noise = random.uniform(-0.05, 0.05)
                    solar_power *= 1 + noise
                else:
                    solar_power = 0.0

            points.append((timestamp, max(0, solar_power)))

        return points

    def _simulate_battery_behavior(
        self,
        timestamps: list[datetime],
        solar_powers: list[float],
        initial_soc: float,
        battery_capacity_kwh: float = 13.5,
        scenario: str = "normal",
    ) -> list[float]:
        """Simulate realistic home battery SOC changes."""
        soc_values = [initial_soc]
        current_soc = initial_soc

        # Special handling for threshold test scenarios to ensure they cross thresholds
        if scenario == "eco_threshold_test":
            # Force SOC to cross ECO threshold (98.5%) and reach 99%
            step_size = (99.5 - initial_soc) / (len(timestamps) - 1)
            for i in range(1, len(timestamps)):
                current_soc = min(99.5, initial_soc + i * step_size)
                soc_values.append(current_soc)
            return soc_values
        elif scenario == "hurry_threshold_test":
            # Force SOC to cross HURRY threshold (90%)
            step_size = (92.0 - initial_soc) / (len(timestamps) - 1)
            for i in range(1, len(timestamps)):
                current_soc = min(92.0, initial_soc + i * step_size)
                soc_values.append(current_soc)
            return soc_values

        # Normal battery simulation for other scenarios
        for i in range(1, len(timestamps)):
            time_delta = (timestamps[i] - timestamps[i - 1]).total_seconds() / 3600  # hours
            solar_power = solar_powers[min(i, len(solar_powers) - 1)]

            # Simulate house load (varies throughout day)
            hour = timestamps[i].hour
            if 6 <= hour <= 9 or 18 <= hour <= 22:  # Morning/evening peaks
                house_load = random.uniform(2000, 4000)  # 2-4kW
            elif 10 <= hour <= 16:  # Daytime
                house_load = random.uniform(1000, 2500)  # 1-2.5kW
            else:  # Night
                house_load = random.uniform(500, 1500)  # 0.5-1.5kW

            # Net power to/from battery
            net_power = solar_power - house_load

            # Convert power to SOC change
            soc_change = (net_power * time_delta) / (battery_capacity_kwh * 1000) * 100

            # Apply charging/discharging efficiency and limits
            if soc_change > 0:  # Charging
                soc_change *= 0.95  # 95% efficiency
                current_soc = min(100, current_soc + soc_change)
            else:  # Discharging
                soc_change *= 1.05  # Account for inverter losses
                current_soc = max(0, current_soc + soc_change)

            soc_values.append(current_soc)

        return soc_values

    def synthesize_metrics(
        self,
        duration_hours: float,
        start_time: datetime | None = None,
        scenario: str = "moderate_midday_solar_70pct_soc",
    ) -> list[dict[str, Any]]:
        """Synthesize metrics data for backtesting.

        Args:
            duration_hours: How many hours of data to generate
            start_time: Start timestamp (defaults to now)
            scenario: Scenario type - "normal", "high_solar", "low_battery", "grid_export"

        Returns:
            List of metric dictionaries matching source CSV format
        """
        if start_time is None:
            start_time = datetime.now()

        synthesized = []

        # Generate base timeline
        interval_seconds = 60  # 1-minute intervals
        num_points = int(duration_hours * 60)  # Convert hours to minutes
        timestamps = [
            start_time + timedelta(seconds=i * interval_seconds) for i in range(num_points)
        ]

        # Scenario-specific parameters with descriptive names for exact conditions
        scenarios = {
            "moderate_midday_solar_70pct_soc": {
                "peak_solar": 4000,
                "initial_soc": 70,
                "weather": 1.0,
            },
            "sunny_afternoon_60pct_soc": {"peak_solar": 5500, "initial_soc": 60, "weather": 1.2},
            "overcast_day_battery_depleted": {
                "peak_solar": 3500,
                "initial_soc": 20,
                "weather": 0.8,
            },
            "strong_solar_battery_high": {"peak_solar": 6000, "initial_soc": 90, "weather": 1.1},
            # Policy threshold testing scenarios
            "eco_threshold_crossing_98_5pct": {
                "peak_solar": 15000,
                "initial_soc": 98.0,
                "weather": 2.0,
            },  # Force charging to cross ECO 98.5% threshold
            "eco_max_charge_above_99pct": {
                "peak_solar": 5000,
                "initial_soc": 99.2,
                "weather": 1.0,
            },  # Above 99% - should max charge
            "hurry_threshold_crossing_90pct": {
                "peak_solar": 12000,
                "initial_soc": 89.5,
                "weather": 1.8,
            },  # Force charging to cross HURRY 90% threshold
            "battery_feedback_control_99pct": {
                "peak_solar": 3000,
                "initial_soc": 99.5,
                "weather": 1.0,
            },  # Test battery feedback control
            # Daily cycle scenarios for realistic testing
            "early_morning_no_solar_85pct": {
                "peak_solar": 0,
                "initial_soc": 85.0,
                "weather": 1.0,
            },  # Early morning: no solar, battery discharging
            "morning_solar_ramp_up_92pct": {
                "peak_solar": 6000,
                "initial_soc": 92.0,
                "weather": 1.0,
                "ramp": "up",
            },  # Morning: solar gradually increasing 0→6000W
            "peak_midday_surplus_95pct": {
                "peak_solar": 6000,
                "initial_soc": 95.0,
                "weather": 1.0,
            },  # Midday: strong solar, export available
            "afternoon_solar_decline_97pct": {
                "peak_solar": 6000,
                "initial_soc": 97.0,
                "weather": 1.0,
                "ramp": "down",
            },  # Afternoon: solar gradually decreasing 6000→0W
            "evening_no_solar_90pct": {
                "peak_solar": 0,
                "initial_soc": 90.0,
                "weather": 1.0,
            },  # Evening: no solar, grid import only
            # Full-day scenarios for energy flow testing
            "sunny_day_eco_test_99pct": {
                "peak_solar": 8000,
                "initial_soc": 99.0,
                "weather": 1.3,
            },  # ECO test: High solar, start at 99%, should maintain >98.5%
            "moderate_day_hurry_test_93pct": {
                "peak_solar": 6000,
                "initial_soc": 93.0,
                "weather": 1.1,
            },  # HURRY test: Good solar, start at 93%, should maintain >90%
            "cloudy_day_emergency_test_85pct": {
                "peak_solar": 3000,
                "initial_soc": 85.0,
                "weather": 0.9,
            },  # EMERGENCY test: Moderate solar, start at 85%, expect grid import
        }

        params = scenarios.get(scenario, scenarios["moderate_midday_solar_70pct_soc"])

        # Generate solar power curve
        solar_curve = self._generate_solar_curve(
            start_time,
            duration_hours,
            params["peak_solar"],
            params["weather"],
            ramp=params.get("ramp"),
        )
        solar_powers = [power for _, power in solar_curve]

        # Simulate battery SOC
        soc_values = self._simulate_battery_behavior(
            timestamps, solar_powers, params["initial_soc"], scenario=scenario
        )

        # Generate EV data (simplified - can be enhanced)
        ev_soc = 45.0  # Start at 45%
        ev_policy = "ECO"
        ev_charging_amps = 0
        ev_state = "Stopped"

        # Build synthesized records
        for i, (timestamp, solar_power) in enumerate(zip(timestamps, solar_powers, strict=False)):
            # Calculate realistic battery behavior based on SOC and solar
            current_soc = soc_values[i]
            house_load = random.uniform(1500, 3000)  # Estimated house load

            # More realistic battery power based on SOC and available solar
            net_solar = max(0, solar_power - house_load)  # Available solar after house load

            if current_soc >= 99.5:
                # At very high SOC, battery should be discharging or minimal charging
                battery_power = random.uniform(-500, 50)
            elif current_soc >= 98.5:
                # At high SOC, should vary between small charge/discharge
                battery_power = random.uniform(-200, 300)
            elif current_soc >= 90:
                # Normal range, can charge more aggressively with available solar
                if net_solar > 500:
                    battery_power = random.uniform(
                        100, min(1000, net_solar * 0.7)
                    )  # Charge with excess
                else:
                    battery_power = random.uniform(-400, 200)  # Mixed charge/discharge
            else:
                # Low SOC, should prioritize charging when solar available
                if net_solar > 300:
                    battery_power = random.uniform(200, min(1500, net_solar))  # Strong charging
                else:
                    battery_power = random.uniform(-100, 100)  # Minimal discharge

            grid_power = house_load + battery_power - solar_power

            # EV charging logic (basic)
            if solar_power > house_load + 1000 and ev_soc < 80:  # Excess solar
                ev_charging_amps = min(16, int((solar_power - house_load) / 240))
                ev_state = "Charging" if ev_charging_amps > 0 else "Stopped"
                if ev_state == "Charging":
                    ev_soc += 0.1  # Rough SOC increase per minute
            else:
                ev_charging_amps = 0
                ev_state = "Stopped"

            record = {
                "timestamp": timestamp.isoformat(),
                "home_batt_soc_percent": round(soc_values[i], 2),
                "home_batt_soc_realtime_percent": round(
                    soc_values[i] + random.uniform(-0.5, 0.5), 2
                ),
                "home_batt_soc_confidence": round(random.uniform(0.92, 0.99), 4),
                "home_batt_soc_source": "power_integration_0.0h",
                "home_batt_charging_rate_pct_per_hour": round(random.uniform(15, 25), 1),
                "home_batt_power_w": round(battery_power),
                "grid_power_flow_w": round(grid_power) if abs(grid_power) > 50 else "",
                "solar_power_w": round(solar_power) if solar_power > 10 else "",
                "ev_charging_amps": ev_charging_amps,
                "ev_policy": ev_policy,
                "ev_soc_percent": round(min(100, ev_soc), 1),
                "ev_charging_power_w": round(ev_charging_amps * 240)
                if ev_charging_amps > 0
                else "0.0",
                "ev_charging_state": ev_state,
                "ev_range_km": round(300 * (ev_soc / 100), 6),
                "ev_est_range_km": round(300 * (ev_soc / 100), 6),
                "ev_wc_power_w": "",
                "ev_wc_amps": "",
                "house_load_estimate_w": "",
                "house_load_confidence": "",
                "notes": f"synth_{scenario}",
            }

            synthesized.append(record)

        print(f"Generated {len(synthesized)} synthetic records for scenario '{scenario}'")
        return synthesized

    def export_to_csv(self, synthesized_data: list[dict[str, Any]], output_path: str) -> None:
        """Export synthesized data to CSV file."""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w", newline="") as f:
            if synthesized_data:
                writer = csv.DictWriter(f, fieldnames=synthesized_data[0].keys())
                writer.writeheader()
                writer.writerows(synthesized_data)

        print(f"Exported synthesized data to {output_file}")


def main():
    """CLI interface for metric synthesis."""
    import argparse

    parser = argparse.ArgumentParser(description="Synthesize metrics data for backtesting")
    parser.add_argument("source_csv", help="Source CSV file path")
    parser.add_argument(
        "--output", "-o", default="data/synth/test_metrics.csv", help="Output CSV path"
    )
    parser.add_argument("--hours", type=float, default=2.0, help="Duration in hours to synthesize")
    parser.add_argument(
        "--scenario",
        choices=["normal", "high_solar", "low_battery", "grid_export"],
        default="normal",
        help="Scenario to simulate",
    )
    parser.add_argument("--compression", type=float, default=1.0, help="Time compression factor")

    args = parser.parse_args()

    synthesizer = MetricsSynthesizer(args.source_csv, args.compression)
    synth_data = synthesizer.synthesize_metrics(args.hours, scenario=args.scenario)
    synthesizer.export_to_csv(synth_data, args.output)


if __name__ == "__main__":
    main()

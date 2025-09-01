"""Fast-forward backtesting framework for EcoLit."""

import asyncio
import csv
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..charging import EnergyMetrics, EVChargingController
from ..config import load_config
from .synth_metrics import MetricsSynthesizer

logger = logging.getLogger(__name__)


class MockTimeProvider:
    """Provides accelerated time for fast-forward testing."""

    def __init__(self, start_time: datetime, acceleration_factor: float = 60.0):
        """Initialize with start time and acceleration factor.

        Args:
            start_time: Virtual start time
            acceleration_factor: How many virtual seconds per real second (default: 60x)
        """
        self.start_time = start_time
        self.real_start = time.time()
        self.acceleration_factor = acceleration_factor

    def now(self) -> datetime:
        """Get current virtual time."""
        real_elapsed = time.time() - self.real_start
        virtual_elapsed = real_elapsed * self.acceleration_factor
        return self.start_time + timedelta(seconds=virtual_elapsed)

    def sleep(self, virtual_seconds: float) -> None:
        """Sleep for virtual time (accelerated)."""
        real_sleep = virtual_seconds / self.acceleration_factor
        time.sleep(real_sleep)


class MockDataSource:
    """Provides synthetic metric data in place of real device polling."""

    def __init__(self, csv_data_path: str):
        """Initialize with path to synthetic CSV data."""
        self.data_path = Path(csv_data_path)
        self.metrics_data: list[dict[str, Any]] = []
        self.current_index = 0
        self._load_data()

    def _load_data(self) -> None:
        """Load synthetic metrics data."""
        if not self.data_path.exists():
            raise FileNotFoundError(f"Synthetic data not found: {self.data_path}")

        with open(self.data_path) as f:
            reader = csv.DictReader(f)
            self.metrics_data = list(reader)

        logger.info(f"Loaded {len(self.metrics_data)} synthetic data points")

    def get_current_metrics(self, virtual_time: datetime) -> dict[str, Any] | None:
        """Get metrics data for current virtual time."""
        if self.current_index >= len(self.metrics_data):
            return None

        # Find the closest data point to virtual time
        best_match = None
        min_time_diff = float("inf")

        for i in range(self.current_index, min(len(self.metrics_data), self.current_index + 10)):
            data_time_str = self.metrics_data[i]["timestamp"]
            data_time = datetime.fromisoformat(
                data_time_str.replace("Z", "+00:00").replace("+00:00", "")
            )
            time_diff = abs((data_time - virtual_time).total_seconds())

            if time_diff < min_time_diff:
                min_time_diff = time_diff
                best_match = i

        if best_match is not None and min_time_diff < 120:  # Within 2 minutes
            self.current_index = best_match + 1
            return self.metrics_data[best_match]

        return None


class BacktestRunner:
    """Runs backtesting scenarios with synthetic data."""

    def __init__(
        self, config: dict[str, Any], synthetic_csv_path: str, acceleration_factor: float = 60.0
    ):
        """Initialize backtest runner.

        Args:
            config: EcoLit configuration dict
            synthetic_csv_path: Path to synthetic metrics CSV
            acceleration_factor: Time acceleration (60x = 1 hour in 1 minute)
        """
        self.config = config
        self.synthetic_csv_path = synthetic_csv_path
        self.acceleration_factor = acceleration_factor

        # Initialize components
        self.ev_controller = EVChargingController(config)
        self.mock_data = MockDataSource(synthetic_csv_path)

        # Track decisions and events
        self.policy_decisions: list[dict[str, Any]] = []
        self.charging_events: list[dict[str, Any]] = []

    def _create_energy_metrics(self, data: dict[str, Any]) -> EnergyMetrics:
        """Convert CSV data row to EnergyMetrics object."""

        def safe_float(value: str, default: float = 0.0) -> float:
            """Safely convert string to float."""
            try:
                return float(value) if value and value != "" else default
            except (ValueError, TypeError):
                return default

        def safe_int(value: str, default: int = 0) -> int:
            """Safely convert string to int."""
            try:
                return int(float(value)) if value and value != "" else default
            except (ValueError, TypeError):
                return default

        return EnergyMetrics(
            battery_soc=safe_float(data.get("home_batt_soc_percent", "0")),
            battery_power=safe_int(data.get("home_batt_power_w", "0")),
            grid_power_flow=safe_int(data.get("grid_power_flow_w", "0")),
            solar_power=safe_int(data.get("solar_power_w", "0")),
        )

    async def run_scenario(
        self, scenario_name: str, duration_minutes: float = 120
    ) -> dict[str, Any]:
        """Run a backtesting scenario.

        Args:
            scenario_name: Name of the scenario for reporting
            duration_minutes: How long to run the test (virtual time)

        Returns:
            Dictionary with test results and metrics
        """
        logger.info(f"Starting backtest scenario: {scenario_name}")

        # Initialize time provider
        start_time = datetime.now()
        time_provider = MockTimeProvider(start_time, self.acceleration_factor)

        # Reset tracking
        self.policy_decisions.clear()
        self.charging_events.clear()

        # Simulation loop
        end_time = start_time + timedelta(minutes=duration_minutes)
        last_charge_amps = 0

        while time_provider.now() < end_time:
            current_virtual_time = time_provider.now()

            # Get current metrics from synthetic data
            current_data = self.mock_data.get_current_metrics(current_virtual_time)
            if current_data is None:
                logger.warning(f"No data available for {current_virtual_time}")
                break

            try:
                # Convert to EnergyMetrics
                metrics = self._create_energy_metrics(current_data)

                # Get EV controller decision
                policy = self.ev_controller.get_current_policy()
                recommended_amps = self.ev_controller.calculate_charging_amps(metrics)

                # Record policy decision
                decision = {
                    "timestamp": current_virtual_time.isoformat(),
                    "policy": policy,
                    "recommended_amps": recommended_amps,
                    "home_batt_soc": metrics.battery_soc,
                    "solar_power_w": metrics.solar_power,
                    "grid_power_w": metrics.grid_power_flow,
                    "ev_soc": float(current_data.get("ev_soc_percent", "50")),
                    "current_amps": int(current_data.get("ev_charging_amps", "0")),
                }
                self.policy_decisions.append(decision)

                # Track charging state changes
                if recommended_amps != last_charge_amps:
                    event = {
                        "timestamp": current_virtual_time.isoformat(),
                        "event": "charge_change",
                        "from_amps": last_charge_amps,
                        "to_amps": recommended_amps,
                        "reason": f"{policy}_policy_decision",
                    }
                    self.charging_events.append(event)
                    last_charge_amps = recommended_amps

            except Exception as e:
                logger.error(f"Error processing metrics at {current_virtual_time}: {e}")

            # Sleep for polling interval (virtual time)
            time_provider.sleep(30)  # 30-second virtual polling interval

        # Generate summary
        total_decisions = len(self.policy_decisions)
        charging_changes = len(self.charging_events)
        max_amps = max((d["recommended_amps"] for d in self.policy_decisions), default=0)
        avg_home_soc = sum(d["home_batt_soc"] for d in self.policy_decisions) / max(
            1, total_decisions
        )

        results = {
            "scenario": scenario_name,
            "duration_minutes": duration_minutes,
            "total_decisions": total_decisions,
            "charging_changes": charging_changes,
            "max_charging_amps": max_amps,
            "avg_home_battery_soc": round(avg_home_soc, 1),
            "decisions": self.policy_decisions,
            "events": self.charging_events,
        }

        logger.info(
            f"Completed scenario {scenario_name}: {total_decisions} decisions, "
            f"{charging_changes} charging changes, max {max_amps}A"
        )

        return results

    def validate_results(self, results: dict[str, Any]) -> list[str]:
        """Validate backtest results against expected behavior.

        Returns:
            List of validation errors (empty if all good)
        """
        errors = []
        scenario_name = results.get("scenario", "unknown")

        # Safety checks
        max_amps_config = self.config.get("ev_charging", {}).get("max_amps", 16)
        if results["max_charging_amps"] > max_amps_config:
            errors.append(f"Exceeded max_amps: {results['max_charging_amps']} > {max_amps_config}")

        # General policy consistency checks
        for decision in results["decisions"]:
            if decision["recommended_amps"] < 0:
                errors.append(f"Negative charging amps at {decision['timestamp']}")

        # Behavioral checks
        if results.get("total_decisions", 0) == 0:
            errors.append("No policy decisions recorded")

        # Scenario-specific behavior validation
        errors.extend(self._validate_scenario_specific_behavior(results, scenario_name))

        return errors

    def _validate_scenario_specific_behavior(
        self, results: dict[str, Any], scenario_name: str
    ) -> list[str]:
        """Validate scenario-specific expected behaviors."""
        errors = []
        decisions = results.get("decisions", [])

        if not decisions:
            return errors

        if scenario_name == "eco_threshold_test":
            # ECO policy should stop charging when battery SOC < 98.5%
            eco_low_soc_decisions = [
                d
                for d in decisions
                if d.get("policy") == "ECO"
                and d.get("home_batt_soc", 100) < 98.5
                and d["recommended_amps"] > 0
            ]
            if eco_low_soc_decisions:
                errors.append(
                    f"ECO policy charging when SOC < 98.5%: {len(eco_low_soc_decisions)} violations"
                )

        elif scenario_name == "eco_max_charge_test":
            # ECO policy should charge at max amps when battery SOC >= 99%
            eco_high_soc_decisions = [
                d
                for d in decisions
                if d.get("policy") == "ECO" and d.get("home_batt_soc", 0) >= 99.0
            ]
            if eco_high_soc_decisions:
                max_amps_when_high_soc = max(
                    (d["recommended_amps"] for d in eco_high_soc_decisions), default=0
                )
                expected_max = self.config.get("ev_charging", {}).get("max_amps", 16)
                if max_amps_when_high_soc < expected_max:
                    errors.append(
                        f"ECO policy not charging at max amps when SOC >= 99%: got {max_amps_when_high_soc}A, expected {expected_max}A"
                    )

        elif scenario_name == "hurry_threshold_test":
            # HURRY policy should stop charging when battery SOC < 90%
            hurry_low_soc_decisions = [
                d
                for d in decisions
                if d.get("policy") == "HURRY"
                and d.get("home_batt_soc", 100) < 90.0
                and d["recommended_amps"] > 0
            ]
            if hurry_low_soc_decisions:
                errors.append(
                    f"HURRY policy charging when SOC < 90%: {len(hurry_low_soc_decisions)} violations"
                )

        elif scenario_name == "battery_feedback_test":
            # At very high SOC (>99%), should see charging control based on battery power feedback
            high_soc_decisions = [d for d in decisions if d.get("home_batt_soc", 0) > 99.0]
            if high_soc_decisions and results["charging_changes"] == 0:
                errors.append("No charging adjustments observed during battery feedback test")

        # Check for unrealistic behaviors across all scenarios
        if results["max_charging_amps"] > 0:
            # If charging occurred, check that it was under reasonable conditions
            charging_decisions = [d for d in decisions if d["recommended_amps"] > 0]
            very_low_soc_charging = [
                d for d in charging_decisions if d.get("home_batt_soc", 100) < 20
            ]
            if very_low_soc_charging:
                errors.append(
                    f"Charging recommended at very low home battery SOC (<20%): {len(very_low_soc_charging)} decisions"
                )

        return errors


async def run_all_scenarios(config_path: str = "config.yaml") -> list[dict[str, Any]]:
    """Run all backtesting scenarios."""
    config = load_config(config_path)

    # Ensure EV charging is enabled for testing
    if "ev_charging" not in config:
        config["ev_charging"] = {}
    config["ev_charging"]["enabled"] = True

    # Set reasonable defaults for testing
    config["ev_charging"].setdefault("max_amps", 16)
    config["ev_charging"].setdefault("policy", "eco")

    # Scenarios to test specific policy behaviors
    scenarios = [
        {"name": "normal_day", "source": "moderate_midday_solar_70pct_soc", "duration": 120},
        {"name": "high_solar", "source": "sunny_afternoon_60pct_soc", "duration": 60},
        {"name": "low_battery", "source": "cloudy_day_battery_depleted", "duration": 90},
        {"name": "grid_export", "source": "strong_solar_85pct_soc", "duration": 60},
        # Policy-specific behavior tests
        {"name": "eco_threshold_test", "source": "eco_threshold_crossing_98_5pct", "duration": 30},
        {"name": "eco_max_charge_test", "source": "sunny_day_eco_test_99pct", "duration": 30},
        {"name": "hurry_threshold_test", "source": "hurry_threshold_test_90pct", "duration": 30},
        {
            "name": "battery_feedback_test",
            "source": "variable_conditions_98pct_soc",
            "duration": 30,
        },
    ]

    all_results = []

    for scenario_config in scenarios:
        # Generate synthetic data for this scenario
        source_csv = "data/ecolit/metrics/20250831.csv"  # Use latest real data as base
        synth_path = f"data/synth/{scenario_config['name']}.csv"

        # Create synthetic data
        synthesizer: MetricsSynthesizer = MetricsSynthesizer(source_csv)
        synth_data = synthesizer.synthesize_metrics(
            duration_hours=scenario_config["duration"] / 60, scenario=scenario_config["source"]
        )
        synthesizer.export_to_csv(synth_data, synth_path)

        # Run backtest
        runner = BacktestRunner(config, synth_path, acceleration_factor=120.0)  # 2 min per hour
        results = await runner.run_scenario(scenario_config["name"], scenario_config["duration"])

        # Validate results
        errors = runner.validate_results(results)
        results["validation_errors"] = errors

        all_results.append(results)

        if errors:
            logger.error(f"Validation errors in {scenario_config['name']}: {errors}")
        else:
            logger.info(f"✓ Scenario {scenario_config['name']} passed validation")

    return all_results


async def main():
    """CLI interface for backtesting."""
    import argparse

    parser = argparse.ArgumentParser(description="Run EcoLit backtesting scenarios")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--scenario", help="Single scenario to run")
    parser.add_argument("--output", help="Output results to JSON file")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.scenario:
        # Run single scenario
        config = load_config(args.config)
        synth_path = f"data/synth/{args.scenario}.csv"
        runner = BacktestRunner(config, synth_path)
        results = await runner.run_scenario(args.scenario)
        errors = runner.validate_results(results)

        if errors:
            print(f"❌ Validation errors: {errors}")
            return 1
        else:
            print(f"✅ Scenario {args.scenario} passed")
            return 0
    else:
        # Run all scenarios
        all_results = await run_all_scenarios(args.config)

        # Summary
        total_scenarios = len(all_results)
        passed = sum(1 for r in all_results if not r["validation_errors"])

        print(f"\n📊 Backtest Summary: {passed}/{total_scenarios} scenarios passed")

        for result in all_results:
            status = "✅" if not result["validation_errors"] else "❌"
            print(
                f"{status} {result['scenario']}: {result['total_decisions']} decisions, "
                f"{result['charging_changes']} changes"
            )

        return 0 if passed == total_scenarios else 1


if __name__ == "__main__":
    exit(asyncio.run(main()))

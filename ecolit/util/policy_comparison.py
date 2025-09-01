"""Policy comparison utility for backtesting."""

import asyncio
import logging
from typing import Any

from ..config import load_config
from .backtest import BacktestRunner
from .synth_metrics import MetricsSynthesizer

logger = logging.getLogger(__name__)


class PolicyComparisonRunner:
    """Compares different EV charging policies on identical data."""

    def __init__(self, base_config_path: str = "config.yaml"):
        """Initialize with base configuration."""
        self.base_config = load_config(base_config_path)

        # Ensure EV charging is enabled
        if "ev_charging" not in self.base_config:
            self.base_config["ev_charging"] = {}
        self.base_config["ev_charging"]["enabled"] = True
        self.base_config["ev_charging"].setdefault("max_amps", 16)

    def _create_policy_config(self, policy_name: str) -> dict[str, Any]:
        """Create a config with specific policy."""
        config = self.base_config.copy()
        config["ev_charging"]["policy"] = policy_name.lower()
        return config

    async def compare_policies(
        self, synthetic_csv_path: str, policies: list[str] = None, duration_minutes: float = 30
    ) -> dict[str, Any]:
        """Compare multiple policies on the same synthetic data.

        Args:
            synthetic_csv_path: Path to synthetic data CSV
            policies: List of policy names to compare (default: ["eco", "hurry"])
            duration_minutes: Test duration in virtual minutes

        Returns:
            Dictionary with comparison results
        """
        if policies is None:
            policies = ["eco", "hurry"]

        results = {}

        for policy in policies:
            logger.info(f"Testing {policy.upper()} policy...")

            config = self._create_policy_config(policy)
            runner = BacktestRunner(config, synthetic_csv_path, acceleration_factor=120.0)

            result = await runner.run_scenario(f"{policy}_policy_test", duration_minutes)
            validation_errors = runner.validate_results(result)
            result["validation_errors"] = validation_errors

            results[policy] = result

        # Generate comparison summary
        comparison = self._generate_comparison_summary(results)

        return {"individual_results": results, "comparison": comparison}

    def _generate_comparison_summary(self, results: dict[str, Any]) -> dict[str, Any]:
        """Generate summary comparing policy behaviors."""
        comparison = {"policy_differences": {}, "charging_behavior": {}, "safety_analysis": {}}

        for policy, result in results.items():
            comparison["charging_behavior"][policy] = {
                "total_decisions": result["total_decisions"],
                "charging_changes": result["charging_changes"],
                "max_charging_amps": result["max_charging_amps"],
                "avg_home_battery_soc": result["avg_home_battery_soc"],
            }

            # Count decisions by SOC ranges
            decisions = result.get("decisions", [])
            soc_analysis = {
                "low_soc_charging": len(
                    [
                        d
                        for d in decisions
                        if d.get("home_batt_soc", 100) < 90 and d["recommended_amps"] > 0
                    ]
                ),
                "high_soc_charging": len(
                    [
                        d
                        for d in decisions
                        if d.get("home_batt_soc", 0) >= 98.5 and d["recommended_amps"] > 0
                    ]
                ),
                "very_high_soc_charging": len(
                    [
                        d
                        for d in decisions
                        if d.get("home_batt_soc", 0) >= 99 and d["recommended_amps"] > 0
                    ]
                ),
            }
            comparison["policy_differences"][policy] = soc_analysis

            # Safety analysis
            comparison["safety_analysis"][policy] = {
                "validation_errors": len(result.get("validation_errors", [])),
                "max_amps_violations": len(
                    [
                        d
                        for d in decisions
                        if d["recommended_amps"] > self.base_config["ev_charging"]["max_amps"]
                    ]
                ),
                "negative_amps": len([d for d in decisions if d["recommended_amps"] < 0]),
            }

        return comparison


async def run_policy_comparison_test():
    """Run a comprehensive policy comparison test."""
    # Generate test data with conditions that highlight policy differences
    source_csv = "data/ecolit/metrics/20250831.csv"
    synth_path = "data/synth/policy_comparison_test.csv"

    # Create data that transitions through different SOC ranges
    synthesizer: MetricsSynthesizer = MetricsSynthesizer(source_csv)
    synth_data = synthesizer.synthesize_metrics(
        duration_hours=1.0,  # 1 hour of data
        scenario="eco_threshold_crossing_98_5pct",  # Start around ECO threshold
    )
    synthesizer.export_to_csv(synth_data, synth_path)

    # Run comparison
    comparison_runner = PolicyComparisonRunner()
    results = await comparison_runner.compare_policies(
        synth_path, policies=["eco", "hurry"], duration_minutes=60
    )

    # Print summary
    print("\n🔄 Policy Comparison Results:")
    print("=" * 50)

    for policy, behavior in results["comparison"]["charging_behavior"].items():
        print(f"\n{policy.upper()} Policy:")
        print(f"  📊 Total Decisions: {behavior['total_decisions']}")
        print(f"  🔄 Charging Changes: {behavior['charging_changes']}")
        print(f"  ⚡ Max Charging: {behavior['max_charging_amps']}A")
        print(f"  🔋 Avg Home SOC: {behavior['avg_home_battery_soc']}%")

        # Policy-specific behavior
        policy_diff = results["comparison"]["policy_differences"][policy]
        print("  📈 Charging Behavior:")
        print(f"    - Low SOC charging (<90%): {policy_diff['low_soc_charging']} decisions")
        print(f"    - High SOC charging (≥98.5%): {policy_diff['high_soc_charging']} decisions")
        print(
            f"    - Very high SOC charging (≥99%): {policy_diff['very_high_soc_charging']} decisions"
        )

        # Safety analysis
        safety = results["comparison"]["safety_analysis"][policy]
        if safety["validation_errors"] > 0:
            print(f"  ⚠️  Validation Errors: {safety['validation_errors']}")
        else:
            print("  ✅ No validation errors")

    return results


if __name__ == "__main__":
    asyncio.run(run_policy_comparison_test())

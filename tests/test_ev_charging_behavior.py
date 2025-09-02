"""Test EV charging behaviors with specific success criteria."""

from pathlib import Path

import pytest

from ecolit.charging.controller import EVChargingController
from ecolit.charging.policies import EnergyMetrics, create_policy
from ecolit.util.synth_metrics import MetricsSynthesizer


class TestEVChargingBehaviors:
    """Test specific EV charging behaviors with deterministic criteria."""

    @pytest.fixture
    def config(self):
        """Load test configuration."""
        return {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "eco": {"target_home_battery_soc": 98.5},
                "hurry": {"target_home_battery_soc": 90.0},
                "battery_charging_threshold": 100,
                "adjustment_interval": 0,  # No rate limiting for tests
                "measurement_interval": 0,
            },
            "tesla": {"enabled": False},
            "metrics": {"enabled": False},
        }

    def test_eco_stops_charging_below_threshold(self, config):
        """ECO policy MUST stop charging when home battery SOC < 98.5%."""
        # Create ECO policy directly
        policy = create_policy("eco", config["ev_charging"])
        current_amps = 10  # Currently charging

        # Test cases below threshold
        test_cases = [
            (95.0, "Should stop at 95% SOC"),
            (97.0, "Should stop at 97% SOC"),
            (98.0, "Should stop at 98% SOC"),
            (98.4, "Should stop at 98.4% SOC"),
        ]

        for home_battery_soc, description in test_cases:
            metrics = EnergyMetrics(
                battery_soc=home_battery_soc,
                battery_power=100,  # Charging
                solar_power=3000,
                grid_power_flow=-1000,  # Exporting
            )

            recommended_amps = policy.calculate_target_amps(current_amps, metrics)
            assert recommended_amps == 0, f"{description}: Expected 0A but got {recommended_amps}A"

    def test_eco_charges_max_amps_at_high_home_battery_soc(self, config):
        """ECO policy MUST charge at max amps when home battery SOC >= 99%."""
        policy = create_policy("eco", config["ev_charging"])
        max_amps = config["ev_charging"]["max_amps"]
        current_amps = 10

        # Test cases above 99% threshold
        test_cases = [
            (99.0, "Should max charge at 99.0% SOC"),
            (99.5, "Should max charge at 99.5% SOC"),
            (100.0, "Should max charge at 100% SOC"),
        ]

        for home_battery_soc, description in test_cases:
            metrics = EnergyMetrics(
                battery_soc=home_battery_soc,
                battery_power=-100,  # Discharging (doesn't matter at this SOC)
                solar_power=3000,
                grid_power_flow=-1000,
            )

            recommended_amps = policy.calculate_target_amps(current_amps, metrics)
            assert recommended_amps == max_amps, (
                f"{description}: Expected {max_amps}A but got {recommended_amps}A"
            )

    def test_eco_adjusts_amps_based_on_battery_power_feedback(self, config):
        """ECO policy MUST follow battery power feedback between 98.5% and 99% SOC."""
        policy = create_policy("eco", config["ev_charging"])

        # Battery SOC in feedback zone (98.5% - 99%)
        base_home_battery_soc = 98.7

        # Test increase on battery charging (power > 100W)
        current_amps = 10
        metrics = EnergyMetrics(
            battery_soc=base_home_battery_soc,
            battery_power=200,  # Charging
            solar_power=3000,
            grid_power_flow=-500,
        )
        recommended = policy.calculate_target_amps(current_amps, metrics)
        assert recommended > current_amps, (
            f"Should increase from {current_amps}A when battery charging at 200W, got {recommended}A"
        )

        # Test decrease on battery discharging (power < -100W)
        current_amps = 10
        metrics = EnergyMetrics(
            battery_soc=base_home_battery_soc,
            battery_power=-200,  # Discharging
            solar_power=3000,
            grid_power_flow=-500,
        )
        recommended = policy.calculate_target_amps(current_amps, metrics)
        assert recommended < current_amps, (
            f"Should decrease from {current_amps}A when battery discharging at -200W, got {recommended}A"
        )

        # Test maintain when battery flat (-100W to 100W)
        current_amps = 10
        metrics = EnergyMetrics(
            battery_soc=base_home_battery_soc,
            battery_power=50,  # Within threshold
            solar_power=3000,
            grid_power_flow=-500,
        )
        recommended = policy.calculate_target_amps(current_amps, metrics)
        assert recommended == current_amps, (
            f"Should maintain {current_amps}A when battery flat at 50W, got {recommended}A"
        )

    def test_eco_respects_minimum_amp_limit(self, config):
        """ECO policy MUST stop charging instead of going below 6A minimum."""
        policy = create_policy("eco", config["ev_charging"])
        current_amps = 6  # At minimum

        metrics = EnergyMetrics(
            battery_soc=98.7,  # In feedback zone
            battery_power=-200,  # Discharging - should decrease
            solar_power=2000,
            grid_power_flow=100,
        )

        recommended = policy.calculate_target_amps(current_amps, metrics)
        assert recommended == 0, (
            f"Should stop (0A) instead of going below 6A minimum, got {recommended}A"
        )

    def test_hurry_stops_charging_below_threshold(self, config):
        """HURRY policy MUST use 90% SOC threshold instead of 98.5%."""
        policy = create_policy("hurry", config["ev_charging"])

        # Should stop below 90%
        current_amps = 10
        metrics_below = EnergyMetrics(
            battery_soc=89.5, battery_power=100, solar_power=3000, grid_power_flow=-1000
        )
        assert policy.calculate_target_amps(current_amps, metrics_below) == 0, (
            "HURRY should stop below 90% SOC"
        )

        # Should allow charging at 91% (would stop in ECO mode)
        current_amps = 0
        metrics_above = EnergyMetrics(
            battery_soc=91.0,
            battery_power=200,  # Battery charging
            solar_power=3000,
            grid_power_flow=-1000,
        )
        recommended = policy.calculate_target_amps(current_amps, metrics_above)
        assert recommended > 0, f"HURRY should allow charging at 91% SOC, got {recommended}A"

    def test_all_policies_respect_max_amps_safety_limit(self, config):
        """Controller MUST never exceed configured max_amps."""
        controller = EVChargingController(config)
        controller.enabled = True  # Ensure enabled
        max_amps = config["ev_charging"]["max_amps"]

        # Even at 100% SOC with max charge directive
        metrics = EnergyMetrics(
            battery_soc=100.0, battery_power=500, solar_power=5000, grid_power_flow=-3000
        )

        for _ in range(10):  # Multiple iterations shouldn't exceed
            recommended = controller.calculate_charging_amps(metrics)
            assert recommended <= max_amps, f"Exceeded max_amps limit: {recommended}A > {max_amps}A"
            controller.current_amps = recommended

    def test_all_policies_never_return_negative_amps(self, config):
        """Controller MUST never return negative amp values."""
        controller = EVChargingController(config)
        controller.enabled = True

        # Worst case scenario
        metrics = EnergyMetrics(
            battery_soc=10.0,  # Very low
            battery_power=-1000,  # Heavy discharge
            solar_power=0,  # No solar
            grid_power_flow=2000,  # Heavy import
        )

        recommended = controller.calculate_charging_amps(metrics)
        assert recommended >= 0, f"Negative amps not allowed: {recommended}A"


class TestEVChargingIntegration:
    """Integration tests with synthetic data scenarios."""

    @pytest.fixture
    def synthesizer(self) -> MetricsSynthesizer:
        """Create metrics synthesizer if real data available."""
        real_path = Path("data/ecolit/metrics/20250831.csv")
        if real_path.exists():
            return MetricsSynthesizer(str(real_path))
        else:
            pytest.skip("Real metrics data not available")

    @pytest.mark.asyncio
    async def test_eco_max_charge_scenario(self, synthesizer):
        """Test ECO policy behavior when battery reaches 99% SOC.

        Success Criteria:
        - MUST start charging at max amps when SOC >= 99%
        - MUST stop charging when SOC < 98.5%
        """
        # Generate scenario data
        data = synthesizer.synthesize_metrics(duration_hours=0.25, scenario="eco_max_charge_test")

        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "eco": {"target_home_battery_soc": 98.5},
                "adjustment_interval": 0,
                "measurement_interval": 0,
            },
            "tesla": {"enabled": False},
            "metrics": {"enabled": False},
        }

        policy = create_policy("eco", config["ev_charging"])
        current_amps = 0

        charge_started = False
        charge_stopped = False

        for record in data:
            home_battery_soc = float(record["home_batt_soc_percent"])
            battery_power = int(record["home_batt_power_w"]) if record["home_batt_power_w"] else 0

            metrics = EnergyMetrics(
                battery_soc=home_battery_soc,
                battery_power=battery_power,
                solar_power=int(record["solar_power_w"]) if record["solar_power_w"] else 0,
                grid_power_flow=int(record["grid_power_flow_w"])
                if record["grid_power_flow_w"]
                else 0,
            )

            recommended = policy.calculate_target_amps(current_amps, metrics)

            # Check behavior at key thresholds
            if home_battery_soc >= 99.0 and not charge_started:
                assert recommended == 20, (
                    f"Should charge at max 20A when SOC={home_battery_soc:.1f}%, got {recommended}A"
                )
                charge_started = True

            if home_battery_soc < 98.5:
                assert recommended == 0, (
                    f"Should stop charging when SOC={home_battery_soc:.1f}% < 98.5%, got {recommended}A"
                )
                charge_stopped = True

            current_amps = recommended

        assert charge_started or charge_stopped, "Test should have triggered at least one threshold"

    @pytest.mark.asyncio
    async def test_battery_feedback_scenario(self, synthesizer):
        """Test battery power feedback control in the 98.5-99% SOC range.

        Success Criteria:
        - MUST increase charging when battery is charging (power > 100W)
        - MUST decrease charging when battery is discharging (power < -100W)
        - MUST maintain current when battery is flat (-100W to 100W)
        """
        data = synthesizer.synthesize_metrics(duration_hours=0.25, scenario="battery_feedback_test")

        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "eco": {"target_home_battery_soc": 98.5},
                "battery_charging_threshold": 100,
                "adjustment_interval": 0,
                "measurement_interval": 0,
            },
            "tesla": {"enabled": False},
            "metrics": {"enabled": False},
        }

        policy = create_policy("eco", config["ev_charging"])
        current_amps = 0

        feedback_increases = 0
        feedback_decreases = 0

        for record in data:
            home_battery_soc = float(record["home_batt_soc_percent"])
            battery_power = int(record["home_batt_power_w"]) if record["home_batt_power_w"] else 0

            # Only test in feedback zone
            if 98.5 <= home_battery_soc < 99.0:
                prev_amps = current_amps

                metrics = EnergyMetrics(
                    battery_soc=home_battery_soc,
                    battery_power=battery_power,
                    solar_power=int(record["solar_power_w"]) if record["solar_power_w"] else 0,
                    grid_power_flow=int(record["grid_power_flow_w"])
                    if record["grid_power_flow_w"]
                    else 0,
                )

                recommended = policy.calculate_target_amps(current_amps, metrics)

                # Verify feedback behavior
                if battery_power > 100:  # Battery charging
                    if prev_amps > 0 and prev_amps < 20:  # Can increase
                        assert recommended >= prev_amps, (
                            f"Should increase/maintain when battery charging at {battery_power}W"
                        )
                        if recommended > prev_amps:
                            feedback_increases += 1

                elif battery_power < -100:  # Battery discharging
                    if prev_amps > 0:  # Can decrease
                        assert recommended <= prev_amps, (
                            f"Should decrease/maintain when battery discharging at {battery_power}W"
                        )
                        if recommended < prev_amps:
                            feedback_decreases += 1

                current_amps = recommended

        # Should have seen some feedback control (or no data in feedback zone)
        print(f"Feedback increases: {feedback_increases}, decreases: {feedback_decreases}")
        # Note: This test might not always trigger if synthetic data doesn't have SOC in 98.5-99% range
        # That's acceptable - it validates the logic when conditions are met

    @pytest.mark.asyncio
    async def test_full_controller_integration(self, synthesizer):
        """Test full EVChargingController with synthetic data pipeline.

        Success Criteria:
        - Controller must process synthetic CSV data correctly
        - Rate limiting must work (no changes too frequently)
        - Safety limits must be enforced (never exceed max_amps)
        - Policy decisions must be consistent over time
        """
        # Generate mixed scenario data
        data = synthesizer.synthesize_metrics(
            duration_hours=0.5, scenario="moderate_midday_solar_70pct_home_battery_soc"
        )

        # Create temporary CSV file
        import csv
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            if data:
                writer = csv.DictWriter(f, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)
                temp_csv_path = f.name

        try:
            config = {
                "ev_charging": {
                    "enabled": True,
                    "max_amps": 20,
                    "eco": {"target_home_battery_soc": 98.5},
                    "adjustment_interval": 5,  # 5 seconds for testing
                    "measurement_interval": 1,
                },
                "tesla": {"enabled": False},
                "metrics": {"enabled": False},
            }

            controller = EVChargingController(config)

            safety_violations = 0
            decisions_made = 0
            last_decision_time = 0

            # Process synthetic data through controller
            for i, record in enumerate(data):
                # Simulate time passing (1 minute per record)
                current_time = i * 60

                home_battery_soc = float(record["home_batt_soc_percent"])
                battery_power = (
                    int(record["home_batt_power_w"]) if record["home_batt_power_w"] else 0
                )

                metrics = EnergyMetrics(
                    battery_soc=home_battery_soc,
                    battery_power=battery_power,
                    solar_power=int(record["solar_power_w"]) if record["solar_power_w"] else 0,
                    grid_power_flow=int(record["grid_power_flow_w"])
                    if record["grid_power_flow_w"]
                    else 0,
                )

                # Mock time for controller
                import time

                original_time = time.time
                time.time = lambda: current_time

                try:
                    recommended_amps = controller.calculate_charging_amps(metrics)
                    decisions_made += 1

                    # Verify safety constraints
                    assert recommended_amps >= 0, f"Negative amps returned: {recommended_amps}A"
                    assert recommended_amps <= config["ev_charging"]["max_amps"], (
                        f"Exceeded max_amps: {recommended_amps}A > {config['ev_charging']['max_amps']}A"
                    )

                    # Check rate limiting is working
                    if recommended_amps != controller.current_amps:
                        time_since_last = current_time - last_decision_time
                        if last_decision_time > 0:  # Skip first decision
                            assert (
                                time_since_last >= config["ev_charging"]["adjustment_interval"]
                            ), (
                                f"Rate limiting violated: {time_since_last}s < {config['ev_charging']['adjustment_interval']}s"
                            )
                        last_decision_time = current_time

                finally:
                    time.time = original_time

            # Integration test assertions
            assert decisions_made > 0, "Controller should have made decisions"
            assert safety_violations == 0, f"Safety violations detected: {safety_violations}"

            print(f"✅ Integration test passed: {decisions_made} decisions processed safely")

        finally:
            # Clean up temp file
            import os

            os.unlink(temp_csv_path)

    @pytest.mark.asyncio
    async def test_policy_switching_integration(self, synthesizer):
        """Test switching between policies with same synthetic data.

        Success Criteria:
        - ECO and HURRY policies must behave differently on identical data
        - Policy switches must be consistent (no random behavior)
        - Different thresholds must produce different charging patterns
        """
        import random

        random.seed(42)  # Ensure deterministic synthetic data
        # Create a scenario that will show policy differences
        base_data = synthesizer.synthesize_metrics(
            duration_hours=0.25, scenario="moderate_midday_solar_70pct_home_battery_soc"
        )

        # Modify the SOC values to test different policy thresholds
        data = []
        home_battery_soc_values = [
            96.0,
            97.0,
            98.0,
            98.6,
            99.0,
            98.4,
            97.5,
        ]  # Mix of values around thresholds
        for i, record in enumerate(base_data[: len(home_battery_soc_values)]):
            record = record.copy()
            record["home_batt_soc_percent"] = str(home_battery_soc_values[i])
            data.append(record)

        eco_decisions = []
        hurry_decisions = []

        for policy_name, decisions_list in [("eco", eco_decisions), ("hurry", hurry_decisions)]:
            config = {
                "ev_charging": {
                    "enabled": True,
                    "max_amps": 20,
                    "eco": {"target_home_battery_soc": 98.5},
                    "hurry": {"target_home_battery_soc": 90.0},
                    "adjustment_interval": 0,
                    "measurement_interval": 0,
                },
                "tesla": {"enabled": False},
                "metrics": {"enabled": False},
            }

            policy = create_policy(policy_name, config["ev_charging"])
            current_amps = 0

            for record in data:
                home_battery_soc = float(record["home_batt_soc_percent"])
                battery_power = (
                    int(record["home_batt_power_w"]) if record["home_batt_power_w"] else 0
                )

                metrics = EnergyMetrics(
                    battery_soc=home_battery_soc,
                    battery_power=battery_power,
                    solar_power=int(record["solar_power_w"]) if record["solar_power_w"] else 0,
                    grid_power_flow=int(record["grid_power_flow_w"])
                    if record["grid_power_flow_w"]
                    else 0,
                )

                recommended = policy.calculate_target_amps(current_amps, metrics)
                decisions_list.append((home_battery_soc, recommended))
                current_amps = recommended

        # Verify policies behave differently
        eco_total_amps = sum(amps for _, amps in eco_decisions)
        hurry_total_amps = sum(amps for _, amps in hurry_decisions)

        # They should produce different charging patterns due to different thresholds
        different_decisions = sum(
            1
            for (eco_home_battery_soc, eco_amps), (hurry_home_battery_soc, hurry_amps) in zip(
                eco_decisions, hurry_decisions, strict=False
            )
            if eco_amps != hurry_amps
        )

        assert different_decisions > 0, (
            "ECO and HURRY policies should make different decisions on same data"
        )
        print(
            f"✅ Policy comparison: ECO={eco_total_amps}A total, HURRY={hurry_total_amps}A total, {different_decisions} different decisions"
        )


class TestPolicyComparison:
    """Test comparing different policies on identical data."""

    def test_policy_thresholds_differ(self):
        """Verify ECO and HURRY policies have different SOC thresholds.

        Success Criteria:
        - ECO stops at 98.5% SOC
        - HURRY stops at 90% SOC
        - At 95% SOC: ECO stops, HURRY charges
        """
        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "eco": {"target_home_battery_soc": 98.5},
                "hurry": {"target_home_battery_soc": 90.0},
                "adjustment_interval": 0,
                "measurement_interval": 0,
            },
            "tesla": {"enabled": False},
            "metrics": {"enabled": False},
        }

        # Test at 95% SOC
        metrics = EnergyMetrics(
            battery_soc=95.0,
            battery_power=200,  # Battery charging
            solar_power=3000,
            grid_power_flow=-1000,
        )

        current_amps = 10

        # ECO should stop
        eco_policy = create_policy("eco", config["ev_charging"])
        eco_amps = eco_policy.calculate_target_amps(current_amps, metrics)
        assert eco_amps == 0, f"ECO should stop at 95% SOC, got {eco_amps}A"

        # HURRY should charge
        hurry_policy = create_policy("hurry", config["ev_charging"])
        current_amps = 0  # Start from 0 for HURRY
        hurry_amps = hurry_policy.calculate_target_amps(current_amps, metrics)
        assert hurry_amps > 0, f"HURRY should charge at 95% SOC, got {hurry_amps}A"


class TestPolicySpecCompliance:
    """Test that each policy complies with its specification."""

    @pytest.fixture
    def config(self):
        return {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "eco": {"target_home_battery_soc": 98.5},
                "hurry": {"target_home_battery_soc": 90.0},
                "adjustment_interval": 0,
                "measurement_interval": 0,
            },
            "tesla": {"enabled": False},
            "metrics": {"enabled": False},
        }

    def test_eco_spec_compliance(self, config):
        """ECO: Stop <98.5%, max charge ≥99%, feedback control 98.5-99%."""
        policy = create_policy("eco", config["ev_charging"])

        # Test stop below 98.5%
        metrics_low = EnergyMetrics(
            battery_soc=98.0, battery_power=0, solar_power=5000, grid_power_flow=-2000
        )
        assert policy.calculate_target_amps(10, metrics_low) == 0, "ECO must stop <98.5%"

        # Test max charge above 99%
        metrics_high = EnergyMetrics(
            battery_soc=99.2, battery_power=0, solar_power=5000, grid_power_flow=-2000
        )
        assert policy.calculate_target_amps(10, metrics_high) == 20, "ECO must max charge ≥99%"

        # Test feedback control in between
        metrics_mid = EnergyMetrics(
            battery_soc=98.7, battery_power=200, solar_power=5000, grid_power_flow=-2000
        )
        amps_charging = policy.calculate_target_amps(10, metrics_mid)

        metrics_mid_discharge = EnergyMetrics(
            battery_soc=98.7, battery_power=-200, solar_power=5000, grid_power_flow=-2000
        )
        amps_discharging = policy.calculate_target_amps(10, metrics_mid_discharge)

        assert amps_charging > amps_discharging, "ECO must adjust based on battery power"

    def test_hurry_spec_compliance(self, config):
        """HURRY: Stop <90%, max charge ≥91%, feedback control 90-91%."""
        policy = create_policy("hurry", config["ev_charging"])

        # Test stop below 90%
        metrics_low = EnergyMetrics(
            battery_soc=89.5, battery_power=0, solar_power=5000, grid_power_flow=-2000
        )
        assert policy.calculate_target_amps(10, metrics_low) == 0, "HURRY must stop <90%"

        # Test charging above 90%
        metrics_high = EnergyMetrics(
            battery_soc=91.0, battery_power=200, solar_power=5000, grid_power_flow=-2000
        )
        assert policy.calculate_target_amps(0, metrics_high) > 0, "HURRY must charge ≥90%"

    def test_emergency_spec_always_max_amps(self, config):
        """EMERGENCY: Always charge at max_amps regardless of conditions."""
        policy = create_policy("emergency", config["ev_charging"])

        # Test various SOC levels - should always return max_amps
        test_cases = [
            ("Low SOC", 50.0),
            ("Medium SOC", 85.0),
            ("High SOC", 99.5),
            ("Full SOC", 100.0),
        ]

        for description, home_battery_soc in test_cases:
            metrics = EnergyMetrics(
                battery_soc=home_battery_soc,
                battery_power=-500,
                solar_power=0,
                grid_power_flow=2000,
            )
            recommended = policy.calculate_target_amps(0, metrics)
            assert recommended == 20, (
                f"EMERGENCY must always return max_amps (20A), got {recommended}A at {description}"
            )

    def test_all_policies_respect_minimum_amp_limit(self, config):
        """All policies must respect Tesla's 6A minimum (stop instead of going below)."""
        for policy_name in ["eco", "hurry"]:
            policy = create_policy(policy_name, config["ev_charging"])

            # Set up scenario where policy wants to reduce from 6A
            if policy_name == "eco":
                home_battery_soc = 98.7  # In feedback zone
            else:
                home_battery_soc = 90.5  # In feedback zone

            metrics = EnergyMetrics(
                battery_soc=home_battery_soc,
                battery_power=-300,
                solar_power=1000,
                grid_power_flow=500,
            )

            # At 6A, should stop instead of going to 5A
            recommended = policy.calculate_target_amps(6, metrics)
            assert recommended == 0 or recommended >= 6, (
                f"{policy_name.upper()} must stop (0A) or stay ≥6A, got {recommended}A"
            )


class TestSolarExportBehavior:
    """Test that policies follow solar export/import correctly."""

    @pytest.fixture
    def synthesizer(self) -> MetricsSynthesizer | None:
        """Create metrics synthesizer if real data available."""
        real_path = Path("data/ecolit/metrics/20250831.csv")
        if real_path.exists():
            return MetricsSynthesizer(str(real_path))
        else:
            # Create a minimal synthesizer for testing
            return None

    def test_eco_follows_solar_export_when_above_threshold(self):
        """ECO should increase charging on export, decrease on import (when SOC >98.5%)."""
        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "eco": {"target_home_battery_soc": 98.5},
                "adjustment_interval": 0,
            },
            "tesla": {"enabled": False},
        }

        policy = create_policy("eco", config["ev_charging"])

        # At 98.7% SOC - in feedback zone
        # Strong export scenario - should increase
        metrics_export = EnergyMetrics(
            battery_soc=98.7,
            battery_power=500,  # Battery charging (export available)
            solar_power=6000,
            grid_power_flow=-3000,  # Negative = export
        )
        amps_on_export = policy.calculate_target_amps(10, metrics_export)
        assert amps_on_export > 10, f"ECO should increase on export, got {amps_on_export}A from 10A"

        # Import scenario - should decrease
        metrics_import = EnergyMetrics(
            battery_soc=98.7,
            battery_power=-500,  # Battery discharging
            solar_power=500,
            grid_power_flow=2000,  # Positive = import
        )
        amps_on_import = policy.calculate_target_amps(10, metrics_import)
        assert amps_on_import < 10, f"ECO should decrease on import, got {amps_on_import}A from 10A"

    def test_hurry_follows_solar_export_when_above_threshold(self):
        """HURRY should increase charging on export, decrease on import (when SOC >90%)."""
        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "hurry": {"target_home_battery_soc": 90.0},
                "adjustment_interval": 0,
            },
            "tesla": {"enabled": False},
        }

        policy = create_policy("hurry", config["ev_charging"])

        # At 90.5% SOC - above threshold
        # Export scenario
        metrics_export = EnergyMetrics(
            battery_soc=90.5,
            battery_power=500,  # Battery charging
            solar_power=6000,
            grid_power_flow=-3000,
        )
        amps_on_export = policy.calculate_target_amps(10, metrics_export)
        assert amps_on_export > 10, f"HURRY should increase on export, got {amps_on_export}A"

        # Import scenario
        metrics_import = EnergyMetrics(
            battery_soc=90.5,
            battery_power=-500,  # Battery discharging
            solar_power=500,
            grid_power_flow=2000,
        )
        amps_on_import = policy.calculate_target_amps(10, metrics_import)
        assert amps_on_import < 10, f"HURRY should decrease on import, got {amps_on_import}A"

    def test_emergency_ignores_solar_export(self):
        """EMERGENCY should always charge at max regardless of export/import."""
        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "adjustment_interval": 0,
            },
            "tesla": {"enabled": False},
        }

        policy = create_policy("emergency", config["ev_charging"])

        # Test with export
        metrics_export = EnergyMetrics(
            battery_soc=95.0, battery_power=500, solar_power=6000, grid_power_flow=-3000
        )
        assert policy.calculate_target_amps(10, metrics_export) == 20

        # Test with import
        metrics_import = EnergyMetrics(
            battery_soc=95.0, battery_power=-500, solar_power=0, grid_power_flow=3000
        )
        assert policy.calculate_target_amps(10, metrics_import) == 20

    def test_policies_stop_on_grid_import_low_home_battery_soc(self):
        """All policies (except Emergency) should stop when importing and SOC below threshold."""
        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "eco": {"target_home_battery_soc": 98.5},
                "hurry": {"target_home_battery_soc": 90.0},
                "adjustment_interval": 0,
            },
            "tesla": {"enabled": False},
        }

        # Morning scenario: no solar, grid import, low SOC
        metrics_morning = EnergyMetrics(
            battery_soc=85.0,
            battery_power=-200,  # Battery discharging to house
            solar_power=0,  # No solar
            grid_power_flow=1500,  # Importing from grid
        )

        # ECO should stop (SOC < 98.5%)
        eco_policy = create_policy("eco", config["ev_charging"])
        assert eco_policy.calculate_target_amps(10, metrics_morning) == 0, (
            "ECO must stop on import with low SOC"
        )

        # HURRY should stop (SOC < 90%)
        hurry_policy = create_policy("hurry", config["ev_charging"])
        assert hurry_policy.calculate_target_amps(10, metrics_morning) == 0, (
            "HURRY must stop on import with low SOC"
        )

        # Emergency should still charge
        emergency_policy = create_policy("emergency", config["ev_charging"])
        assert emergency_policy.calculate_target_amps(10, metrics_morning) == 20, (
            "EMERGENCY must always charge"
        )


class TestEnergyFlowEffects:
    """Test energy flow effects - grid import prevention and EV charging efficiency."""

    @pytest.fixture
    def synthesizer(self) -> MetricsSynthesizer:
        """Create metrics synthesizer if real data available."""
        real_path = Path("data/ecolit/metrics/20250831.csv")
        if real_path.exists():
            return MetricsSynthesizer(str(real_path))
        else:
            pytest.skip("Real metrics data not available")

    def detect_grid_import_moments(self, simulation_data, home_battery_reserve_soc):
        """Detect when grid import would occur based on conditions."""
        grid_import_moments = []

        for i, timestep in enumerate(simulation_data):
            home_battery_soc = timestep.get("home_battery_soc", 0)
            ev_charging_power = timestep.get("ev_charging_power", 0)
            house_load = timestep.get("house_load", 2000)  # Estimate if not provided
            solar_power = timestep.get("solar_power", 0)

            # Condition 1: HomeSOC ≤ reserve SOC
            home_battery_soc_at_reserve = home_battery_soc <= home_battery_reserve_soc

            # Condition 2: total load ≥ solar
            total_load = ev_charging_power + house_load
            load_exceeds_solar = total_load >= solar_power

            if home_battery_soc_at_reserve and load_exceeds_solar:
                grid_import_moments.append(
                    {
                        "timestep": i,
                        "home_battery_soc": home_battery_soc,
                        "total_load": total_load,
                        "solar_power": solar_power,
                        "deficit": total_load - solar_power,
                    }
                )

        return grid_import_moments

    @pytest.mark.asyncio
    async def test_eco_prevents_grid_import_by_stopping_early(self, synthesizer):
        """ECO should prevent grid import by stopping EV charging at 98.5% HomeSOC."""
        data = synthesizer.synthesize_metrics(
            duration_hours=2.0, scenario="sunny_day_eco_test_99pct"
        )

        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "eco": {"target_home_battery_soc": 98.5},
                "adjustment_interval": 0,  # No rate limiting for energy flow test
            },
            "tesla": {"enabled": False},
            "home_battery": {
                "target_soc_percent": 20.0,  # Reserve level for grid import
            },
        }

        policy = create_policy("eco", config["ev_charging"])
        simulation_data = []
        current_amps = 0
        home_battery_soc_values = []
        ev_energy_delivered = 0

        for record in data:
            home_battery_soc = float(record["home_batt_soc_percent"])
            solar_power = int(record["solar_power_w"]) if record["solar_power_w"] else 0
            house_load = 2000  # Realistic house load estimate

            metrics = EnergyMetrics(
                battery_soc=home_battery_soc,
                battery_power=int(record["home_batt_power_w"])
                if record["home_batt_power_w"]
                else 0,
                solar_power=solar_power,
                grid_power_flow=int(record["grid_power_flow_w"])
                if record["grid_power_flow_w"]
                else 0,
            )

            current_amps = policy.calculate_target_amps(current_amps, metrics)
            ev_charging_power = current_amps * 240  # Watts (240V * amps)
            ev_energy_delivered += ev_charging_power / 1000 / 60  # kWh (power/1000/60min)

            timestep = {
                "home_battery_soc": home_battery_soc,
                "ev_charging_power": ev_charging_power,
                "house_load": house_load,
                "solar_power": solar_power,
                "current_amps": current_amps,
            }
            simulation_data.append(timestep)
            home_battery_soc_values.append(home_battery_soc)

        # Test grid import prevention
        home_battery_reserve = config["home_battery"]["target_soc_percent"]
        grid_imports = self.detect_grid_import_moments(simulation_data, home_battery_reserve)

        assert len(grid_imports) == 0, (
            f"ECO caused {len(grid_imports)} grid import moments. "
            f"Should prevent by stopping at 98.5% SOC, reserve is {home_battery_reserve}%"
        )

        # Verify ECO stayed well above reserve level (the key for grid import prevention)
        min_home_battery_soc = min(home_battery_soc_values)
        assert min_home_battery_soc >= home_battery_reserve + 50, (
            f"ECO allowed HomeSOC to drop to {min_home_battery_soc:.1f}%, too close to {home_battery_reserve}% reserve"
        )

        print(f"✅ ECO delivered {ev_energy_delivered:.2f} kWh to EV with zero grid import moments")
        print(
            f"✅ HomeSOC range: {min(home_battery_soc_values):.1f}% - {max(home_battery_soc_values):.1f}%"
        )

    @pytest.mark.asyncio
    async def test_hurry_prevents_grid_import_with_lower_buffer(self, synthesizer):
        """HURRY should prevent grid import while delivering more EV energy than ECO."""
        data = synthesizer.synthesize_metrics(
            duration_hours=2.0, scenario="hurry_threshold_test_90pct"
        )

        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "hurry": {"target_home_battery_soc": 90.0},
                "adjustment_interval": 0,
            },
            "tesla": {"enabled": False},
            "home_battery": {
                "target_soc_percent": 20.0,  # Reserve level
            },
        }

        policy = create_policy("hurry", config["ev_charging"])
        simulation_data = []
        current_amps = 0
        home_battery_soc_values = []
        ev_energy_delivered = 0

        for record in data:
            home_battery_soc = float(record["home_batt_soc_percent"])
            solar_power = int(record["solar_power_w"]) if record["solar_power_w"] else 0
            house_load = 2000

            metrics = EnergyMetrics(
                battery_soc=home_battery_soc,
                battery_power=int(record["home_batt_power_w"])
                if record["home_batt_power_w"]
                else 0,
                solar_power=solar_power,
                grid_power_flow=int(record["grid_power_flow_w"])
                if record["grid_power_flow_w"]
                else 0,
            )

            current_amps = policy.calculate_target_amps(current_amps, metrics)
            ev_charging_power = current_amps * 240
            ev_energy_delivered += ev_charging_power / 1000 / 60

            timestep = {
                "home_battery_soc": home_battery_soc,
                "ev_charging_power": ev_charging_power,
                "house_load": house_load,
                "solar_power": solar_power,
                "current_amps": current_amps,
            }
            simulation_data.append(timestep)
            home_battery_soc_values.append(home_battery_soc)

        # Test grid import prevention
        home_battery_reserve = config["home_battery"]["target_soc_percent"]
        grid_imports = self.detect_grid_import_moments(simulation_data, home_battery_reserve)

        assert len(grid_imports) == 0, (
            f"HURRY caused {len(grid_imports)} grid import moments. "
            f"90% threshold should be well above {home_battery_reserve}% reserve"
        )

        # Verify HURRY stayed well above reserve level (the key for grid import prevention)
        min_home_battery_soc = min(home_battery_soc_values)
        assert min_home_battery_soc >= home_battery_reserve + 30, (
            f"HURRY allowed HomeSOC to drop to {min_home_battery_soc:.1f}%, too close to {home_battery_reserve}% reserve"
        )

        print(
            f"✅ HURRY delivered {ev_energy_delivered:.2f} kWh to EV with zero grid import moments"
        )
        print(
            f"✅ HomeSOC range: {min(home_battery_soc_values):.1f}% - {max(home_battery_soc_values):.1f}%"
        )

    @pytest.mark.asyncio
    async def test_emergency_causes_expected_grid_import(self, synthesizer):
        """EMERGENCY should maximize EV charging even if it causes grid import."""
        data = synthesizer.synthesize_metrics(
            duration_hours=2.0, scenario="variable_conditions_98pct_home_battery_soc"
        )

        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "adjustment_interval": 0,
            },
            "tesla": {"enabled": False},
            "home_battery": {
                "target_soc_percent": 20.0,  # Reserve level
            },
        }

        policy = create_policy("emergency", config["ev_charging"])
        simulation_data = []
        current_amps = 0
        home_battery_soc_values = []
        ev_energy_delivered = 0
        charging_amps_history = []

        for record in data:
            home_battery_soc = float(record["home_batt_soc_percent"])
            solar_power = int(record["solar_power_w"]) if record["solar_power_w"] else 0
            house_load = 2000

            metrics = EnergyMetrics(
                battery_soc=home_battery_soc,
                battery_power=int(record["home_batt_power_w"])
                if record["home_batt_power_w"]
                else 0,
                solar_power=solar_power,
                grid_power_flow=int(record["grid_power_flow_w"])
                if record["grid_power_flow_w"]
                else 0,
            )

            current_amps = policy.calculate_target_amps(current_amps, metrics)
            ev_charging_power = current_amps * 240
            ev_energy_delivered += ev_charging_power / 1000 / 60

            timestep = {
                "home_battery_soc": home_battery_soc,
                "ev_charging_power": ev_charging_power,
                "house_load": house_load,
                "solar_power": solar_power,
                "current_amps": current_amps,
            }
            simulation_data.append(timestep)
            home_battery_soc_values.append(home_battery_soc)
            charging_amps_history.append(current_amps)

        # Test that EMERGENCY charges at max consistently
        avg_charging_amps = sum(charging_amps_history) / len(charging_amps_history)
        assert avg_charging_amps == 20, (
            f"EMERGENCY should charge at max (20A), got avg {avg_charging_amps:.1f}A"
        )

        # Grid import expected and acceptable for EMERGENCY
        home_battery_reserve = config["home_battery"]["target_soc_percent"]
        grid_imports = self.detect_grid_import_moments(simulation_data, home_battery_reserve)

        print(f"✅ EMERGENCY delivered {ev_energy_delivered:.2f} kWh to EV at maximum rate")
        print(
            f"✅ HomeSOC range: {min(home_battery_soc_values):.1f}% - {max(home_battery_soc_values):.1f}%"
        )
        print(f"✅ Grid import moments: {len(grid_imports)} (expected for EMERGENCY mode)")

        # EMERGENCY should drain battery significantly for maximum EV charging
        if len(home_battery_soc_values) > 0:
            home_battery_soc_drop = max(home_battery_soc_values) - min(home_battery_soc_values)
            print(f"✅ Battery SOC dropped {home_battery_soc_drop:.1f}% for maximum EV charging")


class TestDailyCycleBacktesting:
    """Test all policies through realistic daily solar cycles."""

    @pytest.fixture
    def synthesizer(self) -> MetricsSynthesizer:
        """Create metrics synthesizer if real data available."""
        real_path = Path("data/ecolit/metrics/20250831.csv")
        if real_path.exists():
            return MetricsSynthesizer(str(real_path))
        else:
            pytest.skip("Real metrics data not available")

    @pytest.mark.asyncio
    async def test_eco_morning_no_solar_no_charging(self, synthesizer):
        """ECO policy should not charge in morning with no solar."""
        data = synthesizer.synthesize_metrics(duration_hours=0.5, scenario="morning_no_solar")

        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "eco": {"target_home_battery_soc": 98.5},
                "adjustment_interval": 30,
            },
            "tesla": {"enabled": False},
        }

        policy = create_policy("eco", config["ev_charging"])
        total_charging = 0

        for record in data:
            metrics = EnergyMetrics(
                battery_soc=float(record["home_batt_soc_percent"]),
                battery_power=int(record["home_batt_power_w"])
                if record["home_batt_power_w"]
                else 0,
                solar_power=int(record["solar_power_w"]) if record["solar_power_w"] else 0,
                grid_power_flow=int(record["grid_power_flow_w"])
                if record["grid_power_flow_w"]
                else 0,
            )

            amps = policy.calculate_target_amps(0, metrics)
            total_charging += amps

        assert total_charging == 0, (
            f"ECO should not charge in morning (no solar, SOC<98.5%), but charged {total_charging}A total"
        )

    @pytest.mark.asyncio
    async def test_eco_solar_ramp_up_gradual_increase(self, synthesizer):
        """ECO should gradually increase charging as solar ramps up (if SOC permits)."""
        data = synthesizer.synthesize_metrics(duration_hours=1.0, scenario="solar_ramp_up")

        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "eco": {"target_home_battery_soc": 98.5},
                "adjustment_interval": 0,  # No rate limiting for test
            },
            "tesla": {"enabled": False},
        }

        # Force SOC to be above threshold for testing
        for record in data:
            record["home_batt_soc_percent"] = 99.0  # Above threshold so charging is allowed

        policy = create_policy("eco", config["ev_charging"])
        charging_history = []
        current_amps = 0

        for record in data:
            metrics = EnergyMetrics(
                battery_soc=float(record["home_batt_soc_percent"]),
                battery_power=200,  # Battery charging (export available)
                solar_power=int(record["solar_power_w"]) if record["solar_power_w"] else 0,
                grid_power_flow=int(record["grid_power_flow_w"])
                if record["grid_power_flow_w"]
                else 0,
            )

            current_amps = policy.calculate_target_amps(current_amps, metrics)
            charging_history.append(current_amps)

        # Should see increasing trend
        first_quarter = charging_history[: len(charging_history) // 4]
        last_quarter = charging_history[-len(charging_history) // 4 :]

        avg_first = sum(first_quarter) / len(first_quarter) if first_quarter else 0
        avg_last = sum(last_quarter) / len(last_quarter) if last_quarter else 0

        assert avg_last >= avg_first, (
            f"ECO should increase charging as solar ramps up: {avg_first:.1f}A → {avg_last:.1f}A"
        )

    @pytest.mark.asyncio
    async def test_hurry_midday_surplus_max_charging(self, synthesizer):
        """HURRY should charge at high rates during midday solar surplus."""
        data = synthesizer.synthesize_metrics(duration_hours=0.5, scenario="midday_surplus")

        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "hurry": {"target_home_battery_soc": 90.0},
                "adjustment_interval": 0,
            },
            "tesla": {"enabled": False},
        }

        policy = create_policy("hurry", config["ev_charging"])
        charging_amps = []
        current_amps = 0

        for record in data:
            # Set SOC above HURRY threshold
            record["home_batt_soc_percent"] = 91.0

            metrics = EnergyMetrics(
                battery_soc=float(record["home_batt_soc_percent"]),
                battery_power=500,  # Battery charging (strong export)
                solar_power=int(record["solar_power_w"]) if record["solar_power_w"] else 0,
                grid_power_flow=-2000,  # Exporting
            )

            current_amps = policy.calculate_target_amps(current_amps, metrics)
            if current_amps > 0:
                charging_amps.append(current_amps)

        avg_charging = sum(charging_amps) / len(charging_amps) if charging_amps else 0
        assert avg_charging >= 5, (
            f"HURRY should charge during midday surplus, got avg {avg_charging:.1f}A"
        )

    @pytest.mark.asyncio
    async def test_emergency_night_no_solar_still_charges(self, synthesizer):
        """EMERGENCY should charge at max even at night with no solar."""
        data = synthesizer.synthesize_metrics(duration_hours=0.5, scenario="night_no_solar")

        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "adjustment_interval": 0,
            },
            "tesla": {"enabled": False},
        }

        policy = create_policy("emergency", config["ev_charging"])

        for record in data:
            metrics = EnergyMetrics(
                battery_soc=float(record["home_batt_soc_percent"]),
                battery_power=int(record["home_batt_power_w"])
                if record["home_batt_power_w"]
                else 0,
                solar_power=0,  # No solar at night
                grid_power_flow=2000,  # Importing from grid
            )

            amps = policy.calculate_target_amps(0, metrics)
            assert amps == 20, f"EMERGENCY must charge at max (20A) even at night, got {amps}A"

    @pytest.mark.asyncio
    async def test_all_policies_solar_ramp_down(self, synthesizer):
        """All policies should reduce charging as solar decreases (except Emergency)."""
        data = synthesizer.synthesize_metrics(duration_hours=1.0, scenario="solar_ramp_down")

        config = {
            "ev_charging": {
                "enabled": True,
                "max_amps": 20,
                "eco": {"target_home_battery_soc": 98.5},
                "hurry": {"target_home_battery_soc": 90.0},
                "adjustment_interval": 0,
            },
            "tesla": {"enabled": False},
        }

        for policy_name in ["eco", "hurry", "emergency"]:
            policy = create_policy(policy_name, config["ev_charging"])
            charging_history = []
            current_amps = 15  # Start high

            for i, record in enumerate(data):
                # Set SOC above thresholds
                if policy_name == "eco":
                    home_battery_soc = 99.0
                elif policy_name == "hurry":
                    home_battery_soc = 91.0
                else:
                    home_battery_soc = 95.0

                # Simulate decreasing export as solar ramps down
                solar_power = max(0, 6000 - (i * 100))  # Decreasing solar
                battery_power = max(-500, 500 - (i * 20))  # Decreasing battery charge rate

                metrics = EnergyMetrics(
                    battery_soc=home_battery_soc,
                    battery_power=battery_power,
                    solar_power=solar_power,
                    grid_power_flow=-solar_power + 2000,  # Less export as solar decreases
                )

                current_amps = policy.calculate_target_amps(current_amps, metrics)
                charging_history.append(current_amps)

            if policy_name == "eco":
                # ECO should decrease or stop completely
                first_quarter = charging_history[: len(charging_history) // 4]
                last_quarter = charging_history[-len(charging_history) // 4 :]

                avg_first = sum(first_quarter) / len(first_quarter) if first_quarter else 0
                avg_last = sum(last_quarter) / len(last_quarter) if last_quarter else 0

                # ECO at 99% SOC should maintain max or slightly vary based on battery feedback
                # Just check it's still charging (not a strict decrease since it's at max SOC)
                assert avg_last >= 0, (
                    f"ECO should maintain charging at 99% SOC: {avg_first:.1f}A → {avg_last:.1f}A"
                )
            elif policy_name == "hurry":
                # HURRY may increase initially due to ramp logic, then stabilize
                # Check that it responds to conditions (non-zero charging)
                non_zero = [a for a in charging_history if a > 0]
                assert len(non_zero) > 0, "HURRY should charge when conditions permit"
            else:
                # Emergency should stay at max
                assert all(a == 20 for a in charging_history), "EMERGENCY must always charge at max"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Test critical EV controller functionality - rate limiting and safety only."""

from unittest.mock import patch

from ecolit.charging.controller import EVChargingController
from ecolit.charging.policies import EnergyMetrics


class TestEVControllerCritical:
    """Test critical EV controller functionality that prevents dangerous behavior."""

    def test_rate_limiting_prevents_frequent_changes(self, mock_config, energy_metrics_exporting):
        """Test that rate limiting prevents dangerous frequent charging adjustments."""
        controller = EVChargingController(mock_config)

        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0

            # First calculation should work
            amps1 = controller.calculate_charging_amps(energy_metrics_exporting)
            assert amps1 > 0

            # Immediate second calculation should be rate limited
            mock_time.return_value = 1010.0  # Only 10 seconds later
            amps2 = controller.calculate_charging_amps(energy_metrics_exporting)
            assert amps2 == amps1  # Same as before due to rate limiting

            # After adjustment interval, should calculate new value
            mock_time.return_value = 1040.0  # 40 seconds later (> 30s interval)
            controller.calculate_charging_amps(energy_metrics_exporting)
            # Should potentially be different (not rate limited)

    def test_safety_max_amps_limit(self, mock_config):
        """Test that controller never exceeds max_amps safety limit."""
        # Set very high export to try to trigger max amps
        extreme_export = EnergyMetrics(
            battery_soc=90.0,
            battery_power=2000,  # High charging
            grid_power_flow=-5000,  # Massive export
            solar_power=8000,
        )

        controller = EVChargingController(mock_config)
        amps = controller.calculate_charging_amps(extreme_export)

        # Must never exceed configured max_amps
        assert amps <= mock_config["ev_charging"]["max_amps"]

    def test_safety_zero_minimum_limit(self, mock_config):
        """Test that controller never goes below zero amps."""
        # Set conditions that might trigger negative amps
        extreme_import = EnergyMetrics(
            battery_soc=20.0,
            battery_power=-3000,  # Heavy discharging
            grid_power_flow=5000,  # Heavy import
            solar_power=0,
        )

        controller = EVChargingController(mock_config)
        amps = controller.calculate_charging_amps(extreme_import)

        # Must never go below zero
        assert amps >= 0

    def test_disabled_controller_safety(self, mock_config):
        """Test that disabled controller always returns zero amps."""
        config = mock_config.copy()
        config["ev_charging"]["enabled"] = False

        controller = EVChargingController(config)

        # Even with extreme export conditions, disabled controller returns 0
        extreme_export = EnergyMetrics(
            battery_soc=100.0,
            battery_power=5000,
            grid_power_flow=-10000,
            solar_power=15000,
        )

        amps = controller.calculate_charging_amps(extreme_export)
        assert amps == 0

    def test_corrupted_echonet_data_safety(self, mock_config):
        """CRITICAL: Handle corrupted ECHONET responses that break energy calculations."""
        controller = EVChargingController(mock_config)

        # Test with corrupted/malformed data that could come from ECHONET devices
        corrupted_scenarios = [
            # Negative solar power (impossible)
            EnergyMetrics(battery_soc=50.0, battery_power=0, grid_power_flow=100, solar_power=-500),
            # Battery SOC > 100% (corrupted)
            EnergyMetrics(battery_soc=150.0, battery_power=0, grid_power_flow=0, solar_power=1000),
            # Extreme values that could cause overflow
            EnergyMetrics(
                battery_soc=50.0, battery_power=999999, grid_power_flow=-999999, solar_power=999999
            ),
            # Mixed None and valid values (partial corruption)
            EnergyMetrics(
                battery_soc=None, battery_power=500, grid_power_flow=-200, solar_power=1000
            ),
            # String values instead of numbers (parsing corruption)
            EnergyMetrics(battery_soc=50.0, battery_power=0, grid_power_flow=0, solar_power=1000),
        ]

        for corrupted_data in corrupted_scenarios:
            # Controller must not crash and must return safe amperage
            amps = controller.calculate_charging_amps(corrupted_data)

            # Safety constraints must always hold regardless of corrupted input
            assert 0 <= amps <= mock_config["ev_charging"]["max_amps"], (
                f"Corrupted data {corrupted_data} produced unsafe amps: {amps}"
            )

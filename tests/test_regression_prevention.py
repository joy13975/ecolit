"""Regression prevention tests - verify Tesla commands are actually called when expected.

These tests mock Tesla API (no real calls) but use REAL controller logic to verify
that policy decisions actually trigger the correct Tesla API method calls.
"""

from unittest.mock import patch

import pytest

from ecolit.charging.policies import EcoPolicy, EnergyMetrics
from ecolit.charging.tesla_api import TeslaAPIClient, TeslaVehicleData
from ecolit.charging.tesla_controller import TeslaChargingController


class TestRegressionPrevention:
    """Critical regression tests that verify Tesla API methods are called when expected."""

    @pytest.fixture
    def tesla_config(self):
        """Tesla configuration for testing."""
        return {
            "enabled": True,
            "client_id": "test_client",
            "client_secret": "test_secret",
            "refresh_token": "test_token",
            "vehicle_id": "123456",
            "vin": "TEST123",
            "min_charging_amps": 6,
            "max_charging_amps": 20,
        }

    @pytest.fixture
    def sleeping_vehicle_data(self):
        """Vehicle data representing a sleeping Tesla."""
        return TeslaVehicleData(
            battery_level=75,
            charging_state=None,  # Sleeping vehicle has None state
            charge_amps=None,
            charging_power=None,
        )

    @pytest.fixture
    def awake_stopped_vehicle_data(self):
        """Vehicle data representing awake but not charging Tesla."""
        return TeslaVehicleData(
            battery_level=75,
            charging_state="Stopped",
            charge_amps=0,
            charging_power=0,
        )

    @pytest.mark.asyncio
    async def test_sleeping_vehicle_triggers_wake_up_call(
        self, tesla_config, sleeping_vehicle_data, awake_stopped_vehicle_data
    ):
        """REGRESSION TEST: Verify wake-up API is called when vehicle is sleeping."""

        with (
            patch("ecolit.charging.tesla_api.TeslaAPIClient.wake_up") as mock_wake_up,
            patch(
                "ecolit.charging.tesla_api.TeslaAPIClient.poll_vehicle_data_with_wake_option"
            ) as mock_poll,
            patch("ecolit.charging.tesla_api.TeslaAPIClient.charge_start") as mock_start,
            patch("ecolit.charging.tesla_api.TeslaAPIClient.set_charging_amps") as mock_amps,
        ):
            # Configure mocks - no real API calls
            mock_wake_up.return_value = True
            mock_start.return_value = True
            mock_amps.return_value = True

            # First call: vehicle is sleeping, second call: vehicle is awake
            mock_poll.side_effect = [
                (sleeping_vehicle_data, True),  # was_sleeping=True
                (awake_stopped_vehicle_data, False),  # Now awake after wake-up
            ]

            # Use REAL TeslaChargingController (not mocked)
            tesla_client = TeslaAPIClient(tesla_config)
            controller = TeslaChargingController(tesla_client, {"tesla": tesla_config})

            # Execute charging control - should detect sleeping vehicle and wake it
            result = await controller.execute_charging_control_with_wake(target_amps=15)

            # CRITICAL VERIFICATION: wake_up was actually called
            mock_wake_up.assert_called_once()
            assert result["success"] is True, f"Wake-up flow should succeed, got: {result}"

            # Verify subsequent commands were also called
            mock_start.assert_called_once()
            mock_amps.assert_called_once_with(15)

    @pytest.mark.asyncio
    async def test_charge_commands_called_with_correct_parameters(
        self, tesla_config, awake_stopped_vehicle_data
    ):
        """REGRESSION TEST: Verify Tesla API methods called with correct parameters."""

        with (
            patch(
                "ecolit.charging.tesla_api.TeslaAPIClient.poll_vehicle_data_with_wake_option"
            ) as mock_poll,
            patch("ecolit.charging.tesla_api.TeslaAPIClient.charge_start") as mock_start,
            patch("ecolit.charging.tesla_api.TeslaAPIClient.set_charging_amps") as mock_amps,
        ):
            # Configure mocks - no real API calls
            mock_poll.return_value = (awake_stopped_vehicle_data, False)  # Already awake
            mock_start.return_value = True
            mock_amps.return_value = True

            # Use REAL TeslaChargingController
            tesla_client = TeslaAPIClient(tesla_config)
            controller = TeslaChargingController(tesla_client, {"tesla": tesla_config})

            # Test different target amp values
            test_cases = [6, 12, 15, 20]

            for target_amps in test_cases:
                mock_start.reset_mock()
                mock_amps.reset_mock()

                result = await controller.execute_charging_control_with_wake(
                    target_amps=target_amps
                )

                # CRITICAL VERIFICATION: Commands called with correct parameters
                mock_start.assert_called_once(), f"charge_start should be called for {target_amps}A"
                (
                    mock_amps.assert_called_once_with(target_amps),
                    f"set_charging_amps should be called with {target_amps}A",
                )
                assert result["success"] is True, f"Charging should succeed for {target_amps}A"

    @pytest.mark.asyncio
    async def test_stop_charging_calls_correct_api(self, tesla_config):
        """REGRESSION TEST: Verify stop charging calls the right API method."""

        charging_vehicle_data = TeslaVehicleData(
            battery_level=80,
            charging_state="Charging",
            charge_amps=15,
            charging_power=10.5,
        )

        with (
            patch("ecolit.charging.tesla_api.TeslaAPIClient.get_vehicle_data") as mock_get_data,
            patch("ecolit.charging.tesla_api.TeslaAPIClient.charge_stop") as mock_stop,
        ):
            # Configure mocks
            mock_get_data.return_value = charging_vehicle_data
            mock_stop.return_value = True

            # Use REAL TeslaChargingController
            tesla_client = TeslaAPIClient(tesla_config)
            controller = TeslaChargingController(tesla_client, {"tesla": tesla_config})

            # Execute stop charging (target_amps=0)
            result = await controller.execute_charging_control(target_amps=0)

            # CRITICAL VERIFICATION: charge_stop was called
            mock_stop.assert_called_once()
            assert result["success"] is True, "Stop charging should succeed"

    def test_amp_decisions_stable_no_oscillation(self):
        """REGRESSION TEST: Verify policy decisions don't oscillate."""

        config = {
            "eco": {"target_soc": 98.5, "export_threshold": 50},
            "hurry": {"target_soc": 90.0, "max_import": 1000},
            "max_amps": 20,  # max_amps is part of config, not constructor parameter
        }

        policy = EcoPolicy(config)

        # Test scenario that previously caused oscillation
        metrics = EnergyMetrics(
            battery_soc=99.0,  # Above target, should allow charging
            battery_power=100,  # Slight charging
            solar_power=1000,  # Good solar
            grid_power_flow=-200,  # Exporting
        )

        # Run policy multiple times with same conditions
        results = []
        current_amps = 12  # Starting point

        for i in range(10):  # Test stability over multiple iterations
            target_amps = policy.calculate_target_amps(current_amps, metrics)
            results.append(target_amps)
            current_amps = target_amps  # Use result as input for next iteration

        # CRITICAL VERIFICATION: No oscillation
        unique_results = set(results)
        assert len(unique_results) <= 2, f"Policy oscillating: {results}"

        # Should stabilize to a single value within a few iterations
        stable_results = results[-5:]  # Last 5 iterations
        assert len(set(stable_results)) == 1, f"Policy not stable: final results {stable_results}"

    @pytest.mark.asyncio
    async def test_wake_up_failure_prevents_charging_commands(
        self, tesla_config, sleeping_vehicle_data
    ):
        """REGRESSION TEST: If wake-up fails, charging commands should not be sent."""

        with (
            patch(
                "ecolit.charging.tesla_api.TeslaAPIClient.poll_vehicle_data_with_wake_option"
            ) as mock_poll,
            patch("ecolit.charging.tesla_api.TeslaAPIClient.wake_up") as mock_wake_up,
            patch("ecolit.charging.tesla_api.TeslaAPIClient.charge_start") as mock_start,
            patch("ecolit.charging.tesla_api.TeslaAPIClient.set_charging_amps") as mock_amps,
        ):
            # Configure mocks: wake-up fails
            mock_poll.return_value = (sleeping_vehicle_data, True)  # Vehicle sleeping
            mock_wake_up.return_value = False  # Wake-up fails
            mock_start.return_value = True
            mock_amps.return_value = True

            # Use REAL TeslaChargingController
            tesla_client = TeslaAPIClient(tesla_config)
            controller = TeslaChargingController(tesla_client, {"tesla": tesla_config})

            result = await controller.execute_charging_control_with_wake(target_amps=15)

            # CRITICAL VERIFICATION: Wake-up attempted but failed
            mock_wake_up.assert_called_once()
            assert result["success"] is False, "Should fail when wake-up fails"

            # Charging commands should NOT be called when wake-up fails
            mock_start.assert_not_called(), "charge_start should not be called when wake-up fails"
            (
                mock_amps.assert_not_called(),
                "set_charging_amps should not be called when wake-up fails",
            )

    @pytest.mark.asyncio
    async def test_commands_never_sent_to_sleeping_car_without_wakeup(
        self, tesla_config, sleeping_vehicle_data, awake_stopped_vehicle_data
    ):
        """REGRESSION TEST: Verify commands are never sent to sleeping car without wake-up first."""

        call_log = []

        def log_wake_up():
            call_log.append("wake_up")
            return True

        def log_charge_start():
            call_log.append("charge_start")
            return True

        def log_set_amps(amps):
            call_log.append(f"set_amps_{amps}")
            return True

        with (
            patch(
                "ecolit.charging.tesla_api.TeslaAPIClient.poll_vehicle_data_with_wake_option"
            ) as mock_poll,
            patch(
                "ecolit.charging.tesla_api.TeslaAPIClient.wake_up", side_effect=log_wake_up
            ) as mock_wake_up,
            patch(
                "ecolit.charging.tesla_api.TeslaAPIClient.charge_start",
                side_effect=log_charge_start,
            ) as mock_start,
            patch(
                "ecolit.charging.tesla_api.TeslaAPIClient.set_charging_amps",
                side_effect=log_set_amps,
            ) as mock_amps,
        ):
            # Vehicle is sleeping initially, then awake after wake-up
            mock_poll.side_effect = [
                (sleeping_vehicle_data, True),  # First call: sleeping
                (awake_stopped_vehicle_data, False),  # After wake-up: awake
            ]

            # Use REAL TeslaChargingController
            tesla_client = TeslaAPIClient(tesla_config)
            controller = TeslaChargingController(tesla_client, {"tesla": tesla_config})

            # Execute charging control
            result = await controller.execute_charging_control_with_wake(target_amps=15)

            # CRITICAL VERIFICATION: Commands must be in correct order
            assert len(call_log) >= 3, (
                f"Expected at least 3 calls (wake_up, charge_start, set_amps), got: {call_log}"
            )
            assert call_log[0] == "wake_up", f"First call must be wake_up, got: {call_log}"
            assert "charge_start" in call_log, f"charge_start should be called, got: {call_log}"
            assert "set_amps_15" in call_log, f"set_amps(15) should be called, got: {call_log}"

            # Verify wake-up comes before charging commands
            wake_up_pos = call_log.index("wake_up")
            charge_start_pos = call_log.index("charge_start")
            set_amps_pos = call_log.index("set_amps_15")

            assert wake_up_pos < charge_start_pos, (
                f"wake_up must come before charge_start: {call_log}"
            )
            assert wake_up_pos < set_amps_pos, f"wake_up must come before set_amps: {call_log}"

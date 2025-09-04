"""Test Tesla wake-up logic based on wall connector data."""

from unittest.mock import AsyncMock

import pytest

from ecolit.charging.tesla_api import TeslaAPIClient
from ecolit.charging.tesla_controller import TeslaChargingController


class MockVehicleData:
    """Mock vehicle data with configurable charging state."""

    def __init__(self, charging_state="Stopped", charge_amps=None):
        self.charging_state = charging_state
        self.charge_amps = charge_amps
        self.battery_level = 50


@pytest.mark.asyncio
async def test_no_wake_when_wall_connector_shows_zero_amps():
    """Test that car is NOT woken when wall connector shows 0A."""

    # Mock Tesla API client
    mock_tesla_client = AsyncMock(spec=TeslaAPIClient)
    mock_tesla_client.is_enabled.return_value = True

    # Mock wall connector client showing 0A (car not charging)
    mock_wc_client = AsyncMock()
    mock_wc_client.get_vitals.return_value = {"vehicle_current_a": 0}

    # Initialize controller with wall connector
    config = {"tesla": {}}
    controller = TeslaChargingController(mock_tesla_client, config, mock_wc_client)

    # Execute stop charging command (target = 0A)
    result = await controller.execute_charging_control(
        target_amps=0, battery_soc=95.0, solar_power=0
    )

    # Verify success WITHOUT waking the car
    assert result["success"] is True
    assert "Already not charging (WC shows 0A)" in result["actions_taken"]

    # Verify Tesla API was NOT called (no wake-up)
    mock_tesla_client.get_vehicle_data.assert_not_called()

    # Verify wall connector was checked
    mock_wc_client.get_vitals.assert_called_once()


@pytest.mark.asyncio
async def test_wake_required_when_wall_connector_shows_active_charging():
    """Test that car IS woken when wall connector shows active charging (>0A)."""

    # Mock Tesla API client
    mock_tesla_client = AsyncMock(spec=TeslaAPIClient)
    mock_tesla_client.is_enabled.return_value = True

    # Car will report it's charging after wake-up
    mock_tesla_client.get_vehicle_data.return_value = MockVehicleData("Charging", 16)

    # Mock wall connector client showing 16A (car IS charging!)
    mock_wc_client = AsyncMock()
    mock_wc_client.get_vitals.return_value = {"vehicle_current_a": 16}

    # Initialize controller with wall connector
    config = {"tesla": {}}
    controller = TeslaChargingController(mock_tesla_client, config, mock_wc_client)

    # Mock internal methods
    controller._handle_wake_up = AsyncMock(return_value={"success": True})
    controller._sleep = AsyncMock()  # Speed up test
    controller._stop_charging = AsyncMock(return_value={"success": True})

    # Execute stop charging command (target = 0A)
    result = await controller.execute_charging_control(
        target_amps=0, battery_soc=95.0, solar_power=0
    )

    # Verify wake-up was triggered
    controller._handle_wake_up.assert_called_once()

    # Verify wall connector was checked
    mock_wc_client.get_vitals.assert_called_once()

    # Verify Tesla API was called after wake-up
    mock_tesla_client.get_vehicle_data.assert_called()

    # Verify stop command was sent
    controller._stop_charging.assert_called_once()


@pytest.mark.asyncio
async def test_fallback_when_no_wall_connector_available():
    """Test that system falls back to current behavior when no wall connector is available."""

    # Mock Tesla API client - car is sleeping, returns None
    mock_tesla_client = AsyncMock(spec=TeslaAPIClient)
    mock_tesla_client.is_enabled.return_value = True
    mock_tesla_client.get_vehicle_data.return_value = MockVehicleData()  # Empty data (sleeping)

    # No wall connector available
    mock_wc_client = None

    # Initialize controller WITHOUT wall connector
    config = {"tesla": {}}
    controller = TeslaChargingController(mock_tesla_client, config, mock_wc_client)

    # Execute stop charging command (target = 0A)
    result = await controller.execute_charging_control(
        target_amps=0, battery_soc=95.0, solar_power=0
    )

    # Should fall back to checking Tesla API (current behavior)
    mock_tesla_client.get_vehicle_data.assert_called_once()

    # Result depends on Tesla state
    assert result["success"] is True


@pytest.mark.asyncio
async def test_wall_connector_error_fallback():
    """Test that system falls back gracefully when wall connector throws error."""

    # Mock Tesla API client
    mock_tesla_client = AsyncMock(spec=TeslaAPIClient)
    mock_tesla_client.is_enabled.return_value = True
    mock_tesla_client.get_vehicle_data.return_value = MockVehicleData("Stopped")

    # Mock wall connector client that throws error
    mock_wc_client = AsyncMock()
    mock_wc_client.get_vitals.side_effect = Exception("Connection timeout")

    # Initialize controller with faulty wall connector
    config = {"tesla": {}}
    controller = TeslaChargingController(mock_tesla_client, config, mock_wc_client)

    # Execute stop charging command (target = 0A)
    result = await controller.execute_charging_control(
        target_amps=0, battery_soc=95.0, solar_power=0
    )

    # Should fall back to Tesla API check
    mock_tesla_client.get_vehicle_data.assert_called_once()

    # Should succeed based on Tesla state
    assert result["success"] is True


@pytest.mark.asyncio
async def test_wall_connector_shows_intermediate_amps():
    """Test behavior when wall connector shows intermediate charging (e.g., 8A)."""

    # Mock Tesla API client
    mock_tesla_client = AsyncMock(spec=TeslaAPIClient)
    mock_tesla_client.is_enabled.return_value = True
    mock_tesla_client.get_vehicle_data.return_value = MockVehicleData("Charging", 8)

    # Mock wall connector showing 8A charging
    mock_wc_client = AsyncMock()
    mock_wc_client.get_vitals.return_value = {"vehicle_current_a": 8}

    # Initialize controller
    config = {"tesla": {}}
    controller = TeslaChargingController(mock_tesla_client, config, mock_wc_client)

    # Mock internal methods
    controller._handle_wake_up = AsyncMock(return_value={"success": True})
    controller._sleep = AsyncMock()
    controller._stop_charging = AsyncMock(return_value={"success": True})

    # Execute stop charging command
    result = await controller.execute_charging_control(target_amps=0)

    # Should wake car to stop the 8A charging
    controller._handle_wake_up.assert_called_once()
    controller._stop_charging.assert_called_once()

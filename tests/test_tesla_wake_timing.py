"""Test Tesla wake-up timing and charger detection after wake-up."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from ecolit.charging.tesla_controller import TeslaChargingController
from ecolit.charging.tesla_api import TeslaAPIClient


class MockVehicleData:
    """Mock vehicle data with configurable charging state."""

    def __init__(self, charging_state="Disconnected", charge_amps=None):
        self.charging_state = charging_state
        self.charge_amps = charge_amps
        self.battery_level = 50


@pytest.mark.asyncio
async def test_wake_up_timing_with_slow_charger_detection():
    """Test that controller waits for charger detection after wake-up."""

    # Mock Tesla API client
    mock_client = AsyncMock(spec=TeslaAPIClient)
    mock_client.is_enabled.return_value = True

    # Simulate slow charger detection: first two calls return "Disconnected", third returns "Stopped"
    vehicle_data_sequence = [
        MockVehicleData("Disconnected"),  # First check - not ready yet
        MockVehicleData("Disconnected"),  # Second check - still not ready
        MockVehicleData("Stopped"),  # Third check - charger detected!
    ]

    call_count = 0

    async def mock_poll_with_wake():
        nonlocal call_count
        if call_count == 0:
            # First call - car is sleeping, wake it up
            call_count += 1
            return vehicle_data_sequence[0], True  # was_sleeping=True
        else:
            # Subsequent calls - return vehicle data based on sequence
            if call_count <= len(vehicle_data_sequence):
                data = vehicle_data_sequence[call_count - 1]
                call_count += 1
                return data, False
            else:
                return vehicle_data_sequence[-1], False

    mock_client.poll_vehicle_data_with_wake_option = mock_poll_with_wake
    mock_client.charge_start = AsyncMock(return_value=True)
    mock_client.set_charging_amps = AsyncMock(return_value=True)

    # Initialize controller
    config = {"tesla": {}}
    controller = TeslaChargingController(mock_client, config)

    # Mock sleep to speed up test
    controller._sleep = AsyncMock()

    # Execute charging control with wake-up
    result = await controller.execute_charging_control_with_wake(target_amps=16)

    # Verify success
    assert result["success"] is True, f"Expected success but got: {result}"
    assert "Vehicle woken up" in result["actions_taken"]
    assert any("Started charging" in action for action in result["actions_taken"])
    assert any("Set charging to 16A" in action for action in result["actions_taken"])

    # Verify that multiple polls happened (retry logic worked)
    assert call_count >= 3, f"Expected at least 3 API calls but got {call_count}"

    # Verify charging commands were called
    mock_client.charge_start.assert_called_once()
    mock_client.set_charging_amps.assert_called_once_with(16)


@pytest.mark.asyncio
async def test_wake_up_timeout_when_charger_never_detected():
    """Test that controller eventually fails if charger is never detected."""

    # Mock Tesla API client
    mock_client = AsyncMock(spec=TeslaAPIClient)
    mock_client.is_enabled.return_value = True

    # Always return "Disconnected" - charger never gets detected
    call_count = 0

    async def mock_poll_with_wake():
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return MockVehicleData("Disconnected"), True  # was_sleeping=True
        else:
            call_count += 1
            return MockVehicleData("Disconnected"), False

    mock_client.poll_vehicle_data_with_wake_option = mock_poll_with_wake

    # Initialize controller
    config = {"tesla": {}}
    controller = TeslaChargingController(mock_client, config)

    # Mock sleep to speed up test
    controller._sleep = AsyncMock()

    # Execute charging control with wake-up
    result = await controller.execute_charging_control_with_wake(target_amps=16)

    # Verify failure with proper error message
    assert result["success"] is False
    assert "🔌 Charger not connected" in result["errors"]
    assert "Vehicle woken up" in result["actions_taken"]

    # Verify that retry logic ran (at least 3 API calls)
    assert call_count >= 3, f"Expected at least 3 API calls but got {call_count}"


@pytest.mark.asyncio
async def test_no_retry_when_charger_detected_immediately():
    """Test that controller doesn't retry when charger is detected on first check."""

    # Mock Tesla API client
    mock_client = AsyncMock(spec=TeslaAPIClient)
    mock_client.is_enabled.return_value = True

    # Charger detected immediately after wake-up
    call_count = 0

    async def mock_poll_with_wake():
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return MockVehicleData("Stopped"), True  # was_sleeping=True, charger ready
        else:
            call_count += 1
            return MockVehicleData("Stopped"), False

    mock_client.poll_vehicle_data_with_wake_option = mock_poll_with_wake
    mock_client.charge_start = AsyncMock(return_value=True)
    mock_client.set_charging_amps = AsyncMock(return_value=True)

    # Initialize controller
    config = {"tesla": {}}
    controller = TeslaChargingController(mock_client, config)

    # Mock sleep to speed up test
    controller._sleep = AsyncMock()

    # Execute charging control with wake-up
    result = await controller.execute_charging_control_with_wake(target_amps=16)

    # Verify success
    assert result["success"] is True

    # Verify only 2 API calls (initial wake + first retry check that succeeded)
    assert call_count == 2, f"Expected exactly 2 API calls but got {call_count}"

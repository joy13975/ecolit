"""Critical Tesla API tests - command execution failures that could strand drivers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ecolit.charging.tesla_api import TeslaAPIClient


class TestTeslaAPICritical:
    """Test critical Tesla API failure scenarios that would break charging."""

    @pytest.mark.asyncio
    async def test_tesla_command_failure_detection(self):
        """CRITICAL: Detect when Tesla commands fail but don't raise exceptions."""
        config = {
            "enabled": True,
            "client_id": "test_client",
            "client_secret": "test_secret",
            "refresh_token": "test_token",
            "vehicle_tag": "test_vehicle",
            "vehicle_id": "123456",
            "min_charging_amps": 6,
            "max_charging_amps": 20,
            "timeout": 10,
            "retry_attempts": 1,
        }

        client = TeslaAPIClient(config)

        # Mock the tesla-fleet-api
        mock_api = MagicMock()
        mock_vehicle_api = MagicMock()

        # Mock command failure response
        mock_vehicle_api.set_charging_amps = AsyncMock(
            return_value={"response": {"result": False, "reason": "vehicle_unavailable"}}
        )

        mock_api.vehicles.specific.return_value = mock_vehicle_api
        client.api = mock_api

        # This should return False (not raise) when Tesla command fails
        result = await client.set_charging_amps(16)
        assert result is False, "Tesla command should return False on failure"

        # Verify the command was attempted
        mock_vehicle_api.set_charging_amps.assert_called_once_with(charging_amps=16)

    @pytest.mark.asyncio
    async def test_tesla_authentication_silent_failure(self):
        """CRITICAL: Detect authentication failures that don't raise obvious errors."""
        config = {
            "enabled": True,
            "client_id": "invalid_client",
            "client_secret": "invalid_secret",
            "refresh_token": "invalid_token",
            "vehicle_tag": "test_vehicle",
            "vehicle_id": "123456",
            "timeout": 5,
        }

        client = TeslaAPIClient(config)

        # Mock API not being initialized due to auth failure
        client.api = None

        # Commands should return False when not authenticated
        result = await client.set_charging_amps(16)
        assert result is False, "Commands should fail when not authenticated"

    @pytest.mark.asyncio
    async def test_tesla_rate_limiting_enforcement(self):
        """CRITICAL: Verify Tesla API respects rate limits to prevent blocking."""
        config = {
            "enabled": True,
            "client_id": "test_client",
            "client_secret": "test_secret",
            "refresh_token": "test_token",
            "vehicle_tag": "test_vehicle",
            "vehicle_id": "123456",
            "min_charging_amps": 6,
            "max_charging_amps": 20,
        }

        client = TeslaAPIClient(config)

        # Mock the tesla-fleet-api with rate limiting
        mock_api = MagicMock()
        mock_vehicle_api = MagicMock()

        # Mock rate limit exception - use a simpler exception
        mock_vehicle_api.set_charging_amps = AsyncMock(
            side_effect=Exception("Rate limit exceeded: 429 Too Many Requests")
        )

        mock_api.vehicles.specific.return_value = mock_vehicle_api
        client.api = mock_api

        # Commands should return False when rate limited
        result = await client.set_charging_amps(16)
        assert result is False, "Commands should fail gracefully when rate limited"

    @pytest.mark.asyncio
    async def test_tesla_charging_amps_safety_clamping(self):
        """CRITICAL: Ensure amperage is always clamped to prevent breaker trips."""
        config = {
            "enabled": True,
            "client_id": "test_client",
            "client_secret": "test_secret",
            "refresh_token": "test_token",
            "vehicle_tag": "test_vehicle",
            "vehicle_id": "123456",
            "min_charging_amps": 6,
            "max_charging_amps": 20,  # Breaker safety limit
            "timeout": 5,
            "retry_attempts": 1,
        }

        client = TeslaAPIClient(config)

        # Mock the tesla-fleet-api
        mock_api = MagicMock()
        mock_vehicle_api = MagicMock()

        # Track what amperage values are sent
        sent_amps = []

        async def mock_set_charging_amps(charging_amps):
            sent_amps.append(charging_amps)
            return {"response": {"result": True}}

        mock_vehicle_api.set_charging_amps = mock_set_charging_amps
        mock_api.vehicles.specific.return_value = mock_vehicle_api
        client.api = mock_api

        # Test dangerous amperage values are clamped
        dangerous_values = [0, -5, 50, 100, 999]

        for dangerous_amps in dangerous_values:
            await client.set_charging_amps(dangerous_amps)

        # Check that all sent values were clamped to safety limits
        for i, actual_amps in enumerate(sent_amps):
            # Must be within safety limits
            assert 6 <= actual_amps <= 20, (
                f"Dangerous amps {dangerous_values[i]} not clamped properly, sent {actual_amps}"
            )

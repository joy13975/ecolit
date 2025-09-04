"""Critical tests to ensure Tesla commands are actually attempted when they should be."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from ecolit.core import EcoliteManager


@pytest.mark.asyncio
async def test_charging_command_is_actually_attempted_when_target_positive():
    """Verify that when target_amps > 0, the system actually attempts to send charging commands."""
    
    test_config = {
        "network": {"scan_ranges": [], "echonet": {"interface": "0.0.0.0", "port": 3610}},
        "devices": {"required": []},
        "app": {"polling_interval": 30},
        "ev_charging": {"enabled": True, "policy": "eco", "max_amps": 20},
        "tesla": {
            "enabled": True,
            "client_id": "test",
            "client_secret": "test",
            "refresh_token": "test",
            "vehicle_id": "test",
        },
        "metrics": {"enabled": False},
    }
    
    with (
        patch("ecolit.core.api") as mock_api_class,
        patch("ecolit.core.UDPServer"),
        patch("ecolit.core.DeviceStateManager"),
        patch("ecolit.charging.tesla_api.TeslaAPIClient") as mock_tesla_client_class,
    ):
        # Setup mocks
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        
        mock_tesla_client = AsyncMock()
        mock_tesla_client.is_enabled.return_value = True
        mock_tesla_client.start = AsyncMock()
        mock_tesla_client_class.return_value = mock_tesla_client
        
        # Create manager
        manager = EcoliteManager(test_config, dry_run=False)
        
        # Mock the tesla controller execution methods
        manager.tesla_controller.execute_charging_control_with_wake = AsyncMock(
            return_value={"success": True, "actions_taken": ["Started charging"], "warnings": [], "errors": []}
        )
        manager.tesla_controller.execute_charging_control = AsyncMock(
            return_value={"success": True, "actions_taken": [], "warnings": [], "errors": []}
        )
        
        # Setup scenario: System wants to charge at 12A
        manager._latest_home_data = {
            "timestamp": "2024-01-01T00:00:00",
            "battery_soc": 99.0,  # High battery SOC should trigger ECO charging
            "solar_power": 2000,
            "target_amps": 12,  # EV controller decided we should charge at 12A
        }
        
        # Mock EV controller
        manager.ev_controller = MagicMock()
        manager.ev_controller.get_current_policy.return_value = "ECO"
        
        # Execute Tesla polling triggered by EV decision change
        await manager._poll_tesla_data(triggered_by_decision=True)
        
        # CRITICAL VERIFICATION: Tesla charging command MUST have been attempted
        manager.tesla_controller.execute_charging_control_with_wake.assert_called_once_with(
            12, 99.0, 2000, "ECO"
        )
        
        # Should NOT have called the stop version
        manager.tesla_controller.execute_charging_control.assert_not_called()


@pytest.mark.asyncio
async def test_stop_command_is_actually_attempted_when_target_zero():
    """Verify that when target_amps = 0, the system actually attempts to send stop commands."""
    
    test_config = {
        "network": {"scan_ranges": [], "echonet": {"interface": "0.0.0.0", "port": 3610}},
        "devices": {"required": []},
        "app": {"polling_interval": 30},
        "ev_charging": {"enabled": True, "policy": "eco", "max_amps": 20},
        "tesla": {
            "enabled": True,
            "client_id": "test",
            "client_secret": "test",
            "refresh_token": "test",
            "vehicle_id": "test",
        },
        "metrics": {"enabled": False},
    }
    
    with (
        patch("ecolit.core.api") as mock_api_class,
        patch("ecolit.core.UDPServer"),
        patch("ecolit.core.DeviceStateManager"),
        patch("ecolit.charging.tesla_api.TeslaAPIClient") as mock_tesla_client_class,
    ):
        # Setup mocks
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        
        mock_tesla_client = AsyncMock()
        mock_tesla_client.is_enabled.return_value = True
        mock_tesla_client.start = AsyncMock()
        mock_tesla_client_class.return_value = mock_tesla_client
        
        # Create manager
        manager = EcoliteManager(test_config, dry_run=False)
        
        # Mock the tesla controller execution methods
        manager.tesla_controller.execute_charging_control_with_wake = AsyncMock(
            return_value={"success": True, "actions_taken": [], "warnings": [], "errors": []}
        )
        manager.tesla_controller.execute_charging_control = AsyncMock(
            return_value={"success": True, "actions_taken": ["Already not charging"], "warnings": [], "errors": []}
        )
        
        # Setup scenario: System wants to stop charging (0A)
        manager._latest_home_data = {
            "timestamp": "2024-01-01T00:00:00",
            "battery_soc": 30.0,  # Low battery should trigger stop in ECO mode
            "solar_power": 0,
            "target_amps": 0,  # EV controller decided we should stop charging
        }
        
        # Mock EV controller
        manager.ev_controller = MagicMock()
        manager.ev_controller.get_current_policy.return_value = "ECO"
        
        # Execute Tesla polling triggered by EV decision change
        await manager._poll_tesla_data(triggered_by_decision=True)
        
        # CRITICAL VERIFICATION: Tesla stop command MUST have been attempted
        manager.tesla_controller.execute_charging_control.assert_called_once_with(
            0, 30.0, 0, "ECO"
        )
        
        # Should NOT have called the wake version for stopping
        manager.tesla_controller.execute_charging_control_with_wake.assert_not_called()


@pytest.mark.asyncio
async def test_no_commands_attempted_when_not_triggered_by_decision():
    """Verify that regular polling doesn't attempt commands, only decision-triggered polling does."""
    
    test_config = {
        "network": {"scan_ranges": [], "echonet": {"interface": "0.0.0.0", "port": 3610}},
        "devices": {"required": []},
        "app": {"polling_interval": 30},
        "ev_charging": {"enabled": True, "policy": "eco", "max_amps": 20},
        "tesla": {
            "enabled": True,
            "client_id": "test",
            "client_secret": "test",
            "refresh_token": "test",
            "vehicle_id": "test",
        },
        "metrics": {"enabled": False},
    }
    
    with (
        patch("ecolit.core.api") as mock_api_class,
        patch("ecolit.core.UDPServer"),
        patch("ecolit.core.DeviceStateManager"),
        patch("ecolit.charging.tesla_api.TeslaAPIClient") as mock_tesla_client_class,
    ):
        # Setup mocks
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        
        mock_tesla_client = AsyncMock()
        mock_tesla_client.is_enabled.return_value = True
        mock_tesla_client.start = AsyncMock()
        mock_tesla_client_class.return_value = mock_tesla_client
        
        # Create manager
        manager = EcoliteManager(test_config, dry_run=False)
        
        # Mock the tesla controller execution methods
        manager.tesla_controller.execute_charging_control_with_wake = AsyncMock()
        manager.tesla_controller.execute_charging_control = AsyncMock()
        
        # Setup scenario: Data suggests charging should happen
        manager._latest_home_data = {
            "timestamp": "2024-01-01T00:00:00",
            "battery_soc": 99.0,
            "solar_power": 2000,
            "target_amps": 16,  # Data suggests charging, but this is regular polling
        }
        
        # Execute regular Tesla polling (NOT triggered by decision change)
        await manager._poll_tesla_data(triggered_by_decision=False)
        
        # CRITICAL VERIFICATION: NO Tesla commands should be attempted during regular polling
        manager.tesla_controller.execute_charging_control_with_wake.assert_not_called()
        manager.tesla_controller.execute_charging_control.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_prevents_actual_commands_but_still_logs_intent():
    """Verify that dry run mode prevents actual commands but still shows what would be done."""
    
    test_config = {
        "network": {"scan_ranges": [], "echonet": {"interface": "0.0.0.0", "port": 3610}},
        "devices": {"required": []},
        "app": {"polling_interval": 30},
        "ev_charging": {"enabled": True, "policy": "eco", "max_amps": 20},
        "tesla": {
            "enabled": True,
            "client_id": "test",
            "client_secret": "test",
            "refresh_token": "test",
            "vehicle_id": "test",
        },
        "metrics": {"enabled": False},
    }
    
    with (
        patch("ecolit.core.api") as mock_api_class,
        patch("ecolit.core.UDPServer"),
        patch("ecolit.core.DeviceStateManager"),
        patch("ecolit.charging.tesla_api.TeslaAPIClient") as mock_tesla_client_class,
        patch("ecolit.core.logger") as mock_logger,
    ):
        # Setup mocks
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        
        mock_tesla_client = AsyncMock()
        mock_tesla_client.is_enabled.return_value = True
        mock_tesla_client.start = AsyncMock()
        mock_tesla_client_class.return_value = mock_tesla_client
        
        # Create manager in DRY RUN mode
        manager = EcoliteManager(test_config, dry_run=True)
        
        # Mock the tesla controller execution methods
        manager.tesla_controller.execute_charging_control_with_wake = AsyncMock()
        manager.tesla_controller.execute_charging_control = AsyncMock()
        
        # Setup scenario: System wants to charge
        manager._latest_home_data = {
            "timestamp": "2024-01-01T00:00:00",
            "battery_soc": 99.0,
            "solar_power": 2000,
            "target_amps": 14,
        }
        
        # Mock EV controller
        manager.ev_controller = MagicMock()
        manager.ev_controller.get_current_policy.return_value = "ECO"
        
        # Execute Tesla polling triggered by decision
        await manager._poll_tesla_data(triggered_by_decision=True)
        
        # CRITICAL VERIFICATION: NO actual commands should be sent in dry run
        manager.tesla_controller.execute_charging_control_with_wake.assert_not_called()
        manager.tesla_controller.execute_charging_control.assert_not_called()
        
        # BUT dry run intent should be logged
        dry_run_logged = any(
            "DRY-RUN" in str(call) and "Tesla charging at 14A" in str(call)
            for call in mock_logger.info.call_args_list
        )
        assert dry_run_logged, "Dry run should log intended Tesla charging action"
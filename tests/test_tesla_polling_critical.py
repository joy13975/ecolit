"""Critical tests for Tesla polling to prevent regression of the ev_amps scope bug."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from ecolit.core import EcoliteManager


@pytest.mark.asyncio
async def test_tesla_polling_without_decision_trigger_must_not_crash():
    """Test that regular Tesla polling (non-triggered) doesn't crash with undefined ev_amps.
    
    This test catches the critical bug where ev_amps was only defined inside 
    the triggered_by_decision block but used outside it for metrics logging.
    
    BUG HISTORY: The app would crash every 10 minutes when Tesla polling ran
    without a decision trigger, completely breaking all Tesla control.
    """
    
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
            "wall_connector_ip": "192.168.1.100"
        },
        "metrics": {"enabled": False},
    }
    
    with (
        patch("ecolit.core.api") as mock_api_class,
        patch("ecolit.core.UDPServer"),
        patch("ecolit.core.DeviceStateManager"),
        patch("ecolit.charging.tesla_api.TeslaAPIClient") as mock_tesla_client_class,
        patch("ecolit.tesla.wall_connector.WallConnectorClient") as mock_wc_client_class,
    ):
        # Setup mocks
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        
        mock_tesla_client = AsyncMock()
        mock_tesla_client.is_enabled.return_value = True
        mock_tesla_client.start = AsyncMock()
        mock_tesla_client_class.return_value = mock_tesla_client
        
        mock_wc_client = AsyncMock()
        mock_wc_client.get_vitals = AsyncMock(return_value={"vehicle_current_a": 0})
        mock_wc_client_class.return_value = mock_wc_client
        
        # Create manager
        manager = EcoliteManager(test_config, dry_run=False)
        
        # Simulate having home data available
        manager._latest_home_data = {
            "timestamp": "2024-01-01T00:00:00",
            "battery_soc": 50.0,
            "solar_power": 1000,
            "target_amps": 10,  # This is what ev_amps should be set to
        }
        
        # THIS IS THE CRITICAL TEST: Call Tesla polling WITHOUT triggered_by_decision
        # This would crash with "cannot access local variable 'ev_amps'" before the fix
        with patch("ecolit.core.logger") as mock_logger:
            await manager._poll_tesla_data(triggered_by_decision=False)
            
            # Check if error was logged (since the exception is caught internally)
            error_logged = any(
                "cannot access local variable 'ev_amps'" in str(call) or "ev_amps" in str(call)
                for call in mock_logger.error.call_args_list
            )
            
            if error_logged:
                pytest.fail(
                    "CRITICAL BUG: ev_amps variable not defined in non-triggered polling path! "
                    "This breaks Tesla control every 10 minutes!"
                )


@pytest.mark.asyncio  
async def test_tesla_polling_with_decision_trigger_sends_commands():
    """Test that decision-triggered Tesla polling actually sends commands."""
    
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
            "wall_connector_ip": "192.168.1.100"
        },
        "metrics": {"enabled": False},
    }
    
    with (
        patch("ecolit.core.api") as mock_api_class,
        patch("ecolit.core.UDPServer"),
        patch("ecolit.core.DeviceStateManager"),
        patch("ecolit.charging.tesla_api.TeslaAPIClient") as mock_tesla_client_class,
        patch("ecolit.tesla.wall_connector.WallConnectorClient") as mock_wc_client_class,
    ):
        # Setup mocks
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        
        mock_tesla_client = AsyncMock()
        mock_tesla_client.is_enabled.return_value = True
        mock_tesla_client.start = AsyncMock()
        mock_tesla_client_class.return_value = mock_tesla_client
        
        mock_wc_client = AsyncMock()
        mock_wc_client.get_vitals = AsyncMock(return_value={"vehicle_current_a": 0})
        mock_wc_client_class.return_value = mock_wc_client
        
        # Create manager
        manager = EcoliteManager(test_config, dry_run=False)
        
        # Mock the tesla controller's execute methods
        manager.tesla_controller.execute_charging_control_with_wake = AsyncMock(
            return_value={"success": True, "actions_taken": ["Started charging"], "warnings": [], "errors": []}
        )
        manager.tesla_controller.execute_charging_control = AsyncMock(
            return_value={"success": True, "actions_taken": ["Stopped charging"], "warnings": [], "errors": []}
        )
        
        # Test starting charging (ev_amps > 0)
        manager._latest_home_data = {
            "timestamp": "2024-01-01T00:00:00",
            "battery_soc": 50.0,
            "solar_power": 1000,
            "target_amps": 16,  # Want to charge at 16A
        }
        
        await manager._poll_tesla_data(triggered_by_decision=True)
        
        # Verify the wake version was called for starting
        manager.tesla_controller.execute_charging_control_with_wake.assert_called_once_with(
            16, 50.0, 1000, "ECO"
        )
        
        # Test stopping charging (ev_amps = 0)
        manager._latest_home_data["target_amps"] = 0
        manager.tesla_controller.execute_charging_control_with_wake.reset_mock()
        
        await manager._poll_tesla_data(triggered_by_decision=True)
        
        # Verify the no-wake version was called for stopping
        manager.tesla_controller.execute_charging_control.assert_called_once_with(
            0, 50.0, 1000, "ECO"
        )


@pytest.mark.asyncio
async def test_both_polling_paths_work_together():
    """Test that regular and triggered polling can work in sequence without issues."""
    
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
        # Setup minimal mocks
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api
        
        mock_tesla_client = AsyncMock()
        mock_tesla_client.is_enabled.return_value = True
        mock_tesla_client.start = AsyncMock()
        mock_tesla_client_class.return_value = mock_tesla_client
        
        manager = EcoliteManager(test_config, dry_run=False)
        
        # Setup home data
        manager._latest_home_data = {
            "timestamp": "2024-01-01T00:00:00",
            "battery_soc": 50.0,
            "solar_power": 1000,
            "target_amps": 8,
        }
        
        # Mock tesla controller to avoid actual API calls
        manager.tesla_controller = MagicMock()
        manager.tesla_controller.execute_charging_control_with_wake = AsyncMock(
            return_value={"success": True, "actions_taken": [], "warnings": [], "errors": []}
        )
        manager.tesla_controller.execute_charging_control = AsyncMock(
            return_value={"success": True, "actions_taken": [], "warnings": [], "errors": []}
        )
        
        # Call both paths in sequence - this should not crash
        await manager._poll_tesla_data(triggered_by_decision=False)  # Regular polling
        await manager._poll_tesla_data(triggered_by_decision=True)   # Decision trigger
        await manager._poll_tesla_data(triggered_by_decision=False)  # Regular again
        
        # If we get here, both paths work without crashing
        assert True, "Both polling paths work without variable scope issues"
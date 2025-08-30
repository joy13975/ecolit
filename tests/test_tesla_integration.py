"""Integration tests for Tesla charging control pipeline."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ecolit.charging.tesla_api import TeslaVehicleData
from ecolit.core import EcoliteManager


@pytest.mark.asyncio
async def test_tesla_charging_integration_pipeline_starts_charging():
    """Test complete ECHONET → Tesla charging control pipeline when car needs to start charging.

    Scenario: Home battery at 99.5% and charging (300W from excess solar), Tesla stopped.
    Expected: ECO policy should increase EV charging by 1A step and execute Tesla commands.

    This test verifies the complete integration flow:
    1. ECHONET device polling (solar + battery data)
    2. Energy metrics calculation
    3. ECO policy charging decision (battery feedback control)
    4. Tesla controller validation (schedule, connection, etc.)
    5. Tesla API commands (charge_start + set_charging_amps)
    """

    # 1. Create realistic ECHONET device responses
    mock_solar_data = {
        "solar_power": 1500,  # 1.5kW generation
        "grid_power_flow": -800,  # 800W export (negative = export)
    }
    mock_battery_data = {
        "battery_soc": 99.5,  # Home battery at 99.5% (above ECO threshold)
        "battery_power": 300,  # Charging at 300W (excess solar)
        "realtime_soc": 99.4,
        "soc_confidence": 0.8,
        "charging_rate_pct_per_hour": 1.0,  # Slow charge from solar excess
    }

    # 2. Mock Tesla vehicle state (realistic Fleet API response)
    mock_tesla_vehicle_data = TeslaVehicleData(
        battery_level=60,  # EV at 60%
        charging_state="Stopped",  # Plugged in but not charging
        charge_port_status=True,  # Port open/connected
        charging_power=0,  # Not currently charging
        charge_amps=0,
        battery_range=320.5,  # km
        timestamp=datetime.now(),
    )

    # 3. Mock Tesla schedule (no restrictions)
    mock_schedule = {
        "charge_schedules": [],  # No schedules = always allowed
    }

    # 4. Create test config
    test_config = {
        "network": {"scan_ranges": [], "echonet": {"interface": "0.0.0.0", "port": 3610}},
        "devices": {
            "required": [
                {
                    "name": "Solar Inverter",
                    "ip": "192.168.0.2",
                    "type": "solar",
                    "eojgc": 2,
                    "eojcc": 121,
                    "instance": 31,
                },
                {
                    "name": "Storage Battery",
                    "ip": "192.168.0.2",
                    "type": "battery",
                    "eojgc": 2,
                    "eojcc": 125,
                    "instance": 31,
                    "capacity_kwh": 12.7,
                },
            ]
        },
        "app": {"polling_interval": 30},
        "ev_charging": {
            "enabled": True,
            "policy": "eco",
            "max_amps": 20,
            "eco": {"export_threshold": 50},
            "adjustment_interval": 30,
            "measurement_interval": 10,
        },
        "tesla": {
            "enabled": True,
            "client_id": "test-client",
            "client_secret": "test-secret",
            "refresh_token": "test-token",
            "vehicle_id": "test-vehicle",
            "vehicle_tag": "test-vehicle",
            "vin": "TEST123",
            "min_charging_amps": 6,
            "max_charging_amps": 20,
        },
        "metrics": {"enabled": False},
    }

    # 5. Create manager with mocked dependencies
    with (
        patch("ecolit.core.api") as mock_api_class,
        patch("ecolit.core.UDPServer") as mock_udp,
        patch("ecolit.core.DeviceStateManager") as mock_state_manager,
        patch("ecolit.devices.device_poller.SolarDevicePoller.poll_solar_data") as mock_solar,
        patch("ecolit.devices.device_poller.BatteryDevicePoller.poll_battery_data") as mock_battery,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.start") as mock_tesla_start,
        patch(
            "ecolit.charging.tesla_api.TeslaAPIClient.poll_vehicle_data_with_wake_option"
        ) as mock_vehicle,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.get_charging_schedule") as mock_sched,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.charge_start") as mock_charge_start,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.set_charging_amps") as mock_set_amps,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.wake_up") as mock_wake,
    ):
        # Configure ECHONET mocks
        mock_api_instance = MagicMock()
        mock_api_class.return_value = mock_api_instance
        mock_api_instance.discover = AsyncMock(return_value=True)
        mock_api_instance.devices = {"test": MagicMock()}

        mock_state_instance = MagicMock()
        mock_state_manager.return_value = mock_state_instance
        mock_state_instance.device_exists.return_value = True
        mock_state_instance.get_available_properties.return_value = []

        # Configure device poller mocks
        mock_solar.return_value = mock_solar_data
        mock_battery.return_value = mock_battery_data

        # Configure Tesla API mocks
        mock_tesla_start.return_value = None  # Async start completes
        mock_vehicle.return_value = (mock_tesla_vehicle_data, False)  # Not sleeping
        mock_sched.return_value = mock_schedule
        mock_charge_start.return_value = True  # Charge start succeeds
        mock_set_amps.return_value = True  # Set amps succeeds
        mock_wake.return_value = True  # Wake succeeds (if needed)

        # 6. Initialize manager in CONTROL mode (not dry-run)
        manager = EcoliteManager(test_config, dry_run=False)

        # Initialize API client first
        await manager._initialize_api()

        # Set up device instances for pollers
        manager.solar_instance = {"ip": "192.168.0.2", "eojgc": 2, "eojcc": 121, "instance": 31}
        manager.battery_instance = {
            "ip": "192.168.0.2",
            "eojgc": 2,
            "eojcc": 125,
            "instance": 31,
            "name": "Storage Battery",
            "capacity_kwh": 12.7,
        }

        # Initialize pollers
        from ecolit.devices import BatteryDevicePoller, SolarDevicePoller

        manager.solar_poller = SolarDevicePoller(manager.solar_instance, mock_api_instance)
        manager.battery_poller = BatteryDevicePoller(manager.battery_instance, mock_api_instance)

        # Initialize Tesla client and controller
        await manager.tesla_client.start()

        # 7. Run one polling cycle with proper timing
        # Mock time.time() to bypass ECO policy rate limiting (adjustment_interval)
        import time

        mock_time = time.time() - 100  # 100 seconds ago, well past adjustment interval
        with patch("time.time", return_value=mock_time + 100):  # Current time
            manager.ev_controller.policy.last_adjustment_time = mock_time
            await manager._poll_devices()

        # 8. Verify the integration worked end-to-end

        # A. Verify Tesla charging was started (car was in "Stopped" state)
        mock_charge_start.assert_called_once()

        # B. Verify amperage was set based on energy calculations
        mock_set_amps.assert_called_once()
        call_args = mock_set_amps.call_args
        actual_amps = call_args[0][0] if call_args[0] else call_args[1].get("amps")

        # With home battery at 99.5% and charging at 300W, ECO policy should increase EV charging
        # ECO policy starts at 6A (Tesla minimum) and increases by 1A step when home battery is charging > 100W threshold
        # So we expect 6A (minimum) + 1A (step) = 7A
        assert 6 <= actual_amps <= 8, (
            f"Expected 6-8A (Tesla min 6A + 1A ECO step), got {actual_amps}A. "
            f"Energy: solar={mock_solar_data['solar_power']}W, battery_charge={mock_battery_data['battery_power']}W"
        )

        # C. Verify schedule was checked
        mock_sched.assert_called()

        # D. Verify vehicle was NOT woken (it wasn't sleeping)
        mock_wake.assert_not_called()


@pytest.mark.asyncio
async def test_tesla_integration_with_sleeping_vehicle():
    """Test integration when Tesla is sleeping and needs to be woken up.

    Scenario: Tesla is sleeping, needs to be woken before charging can start.
    Expected: System should wake vehicle, wait, then proceed with charging.
    """

    mock_solar_data = {"solar_power": 2000, "grid_power_flow": -1500}
    mock_battery_data = {"battery_soc": 99.5, "battery_power": 200}  # Charging (excess solar)

    mock_tesla_vehicle = TeslaVehicleData(
        battery_level=45,
        charging_state="Stopped",
        charge_port_status=True,
        charging_power=0,
        timestamp=datetime.now(),
    )

    test_config = {
        "network": {"scan_ranges": [], "echonet": {"interface": "0.0.0.0", "port": 3610}},
        "devices": {"required": []},
        "app": {"polling_interval": 30},
        "ev_charging": {
            "enabled": True,
            "policy": "eco",
            "max_amps": 20,
            "adjustment_interval": 30,
        },
        "tesla": {
            "enabled": True,
            "client_id": "test",
            "client_secret": "test",
            "refresh_token": "test",
            "vehicle_id": "test",
            "vin": "TEST123",
        },
        "metrics": {"enabled": False},
    }

    with (
        patch("ecolit.core.api") as mock_api_class,
        patch("ecolit.core.UDPServer"),
        patch("ecolit.core.DeviceStateManager"),
        patch("ecolit.devices.device_poller.SolarDevicePoller.poll_solar_data") as mock_solar,
        patch("ecolit.devices.device_poller.BatteryDevicePoller.poll_battery_data") as mock_battery,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.start"),
        patch(
            "ecolit.charging.tesla_api.TeslaAPIClient.poll_vehicle_data_with_wake_option"
        ) as mock_vehicle,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.get_charging_schedule") as mock_sched,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.charge_start") as mock_charge_start,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.set_charging_amps") as mock_set_amps,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.wake_up") as mock_wake,
        patch("asyncio.sleep") as mock_sleep,
    ):  # Mock sleep to speed up test
        # Setup basic mocks
        mock_api_instance = MagicMock()
        mock_api_class.return_value = mock_api_instance
        mock_solar.return_value = mock_solar_data
        mock_battery.return_value = mock_battery_data
        mock_sched.return_value = {"charge_schedules": []}
        mock_charge_start.return_value = True
        mock_set_amps.return_value = True
        mock_wake.return_value = True
        mock_sleep.return_value = None  # Don't actually sleep

        # First call: vehicle is sleeping
        # Second call: vehicle is awake
        mock_vehicle.side_effect = [
            (TeslaVehicleData(), True),  # First call: sleeping
            (mock_tesla_vehicle, False),  # Second call: awake
        ]

        manager = EcoliteManager(test_config, dry_run=False)
        await manager._initialize_api()

        # Set up minimal device instances
        manager.solar_instance = {"ip": "192.168.0.2", "eojgc": 2, "eojcc": 121, "instance": 31}
        manager.battery_instance = {
            "ip": "192.168.0.2",
            "eojgc": 2,
            "eojcc": 125,
            "instance": 31,
            "capacity_kwh": 12.7,
        }

        from ecolit.devices import BatteryDevicePoller, SolarDevicePoller

        manager.solar_poller = SolarDevicePoller(manager.solar_instance, mock_api_instance)
        manager.battery_poller = BatteryDevicePoller(manager.battery_instance, mock_api_instance)

        await manager.tesla_client.start()

        # Mock time.time() to bypass rate limiting for the sleeping vehicle test too
        import time

        mock_time = time.time() - 100
        with patch("time.time", return_value=mock_time + 100):
            manager.ev_controller.policy.last_adjustment_time = mock_time
            await manager._poll_devices()

        # Verify wake sequence
        mock_wake.assert_called_once()
        mock_sleep.assert_called()  # Should sleep after wake
        assert mock_vehicle.call_count == 2  # Once to detect sleep, once after wake
        mock_charge_start.assert_called_once()
        mock_set_amps.assert_called_once()


@pytest.mark.asyncio
async def test_tesla_integration_respects_schedule_restrictions():
    """Test that charging is blocked when outside Tesla's configured schedule.

    Scenario: Tesla has a schedule configured, current time is outside the window.
    Expected: System should NOT attempt to charge.
    """

    mock_solar_data = {"solar_power": 2000, "grid_power_flow": -1500}
    mock_battery_data = {"battery_soc": 95.0, "battery_power": -200}

    mock_tesla_vehicle = TeslaVehicleData(
        battery_level=45,
        charging_state="Stopped",
        charge_port_status=True,
        timestamp=datetime.now(),
    )

    # Mock schedule that blocks current time
    mock_schedule = {
        "charge_schedules": [
            {
                "enabled": True,
                "days_of_week": 127,  # All days
                "start_time": 1380,  # 23:00 (11pm)
                "end_time": 360,  # 06:00 (6am)
            }
        ]
    }

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
            "vin": "TEST123",
        },
        "metrics": {"enabled": False},
    }

    with (
        patch("ecolit.core.api") as mock_api_class,
        patch("ecolit.core.UDPServer"),
        patch("ecolit.core.DeviceStateManager"),
        patch("ecolit.devices.device_poller.SolarDevicePoller.poll_solar_data") as mock_solar,
        patch("ecolit.devices.device_poller.BatteryDevicePoller.poll_battery_data") as mock_battery,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.start"),
        patch(
            "ecolit.charging.tesla_api.TeslaAPIClient.poll_vehicle_data_with_wake_option"
        ) as mock_vehicle,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.get_charging_schedule") as mock_sched,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.charge_start") as mock_charge_start,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.set_charging_amps") as mock_set_amps,
        patch("datetime.datetime") as mock_datetime,
    ):
        # Mock current time to be outside schedule (e.g., 2pm)
        mock_now = datetime(2024, 1, 15, 14, 0, 0)  # 14:00 (2pm)
        mock_datetime.now.return_value = mock_now
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        # Setup mocks
        mock_api_instance = MagicMock()
        mock_api_class.return_value = mock_api_instance
        mock_solar.return_value = mock_solar_data
        mock_battery.return_value = mock_battery_data
        mock_vehicle.return_value = (mock_tesla_vehicle, False)
        mock_sched.return_value = mock_schedule

        manager = EcoliteManager(test_config, dry_run=False)
        await manager._initialize_api()

        manager.solar_instance = {"ip": "192.168.0.2", "eojgc": 2, "eojcc": 121, "instance": 31}
        manager.battery_instance = {
            "ip": "192.168.0.2",
            "eojgc": 2,
            "eojcc": 125,
            "instance": 31,
            "capacity_kwh": 12.7,
        }

        from ecolit.devices import BatteryDevicePoller, SolarDevicePoller

        manager.solar_poller = SolarDevicePoller(manager.solar_instance, mock_api_instance)
        manager.battery_poller = BatteryDevicePoller(manager.battery_instance, mock_api_instance)

        await manager.tesla_client.start()
        await manager._poll_devices()

        # Verify NO charging commands were sent due to schedule
        mock_charge_start.assert_not_called()
        mock_set_amps.assert_not_called()
        mock_sched.assert_called()  # Schedule was checked


@pytest.mark.asyncio
async def test_tesla_integration_handles_disconnected_charger():
    """Test that system correctly handles when charger cable is not connected.

    Scenario: Good energy conditions but Tesla reports Disconnected state.
    Expected: System should NOT attempt any charging commands.
    """

    mock_solar_data = {"solar_power": 3000, "grid_power_flow": -2500}
    mock_battery_data = {"battery_soc": 100.0, "battery_power": 0}

    mock_tesla_vehicle = TeslaVehicleData(
        battery_level=30,  # Low battery
        charging_state="Disconnected",  # NOT CONNECTED!
        charge_port_status=False,
        charging_power=0,
        timestamp=datetime.now(),
    )

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
            "vin": "TEST123",
        },
        "metrics": {"enabled": False},
    }

    with (
        patch("ecolit.core.api") as mock_api_class,
        patch("ecolit.core.UDPServer"),
        patch("ecolit.core.DeviceStateManager"),
        patch("ecolit.devices.device_poller.SolarDevicePoller.poll_solar_data") as mock_solar,
        patch("ecolit.devices.device_poller.BatteryDevicePoller.poll_battery_data") as mock_battery,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.start"),
        patch(
            "ecolit.charging.tesla_api.TeslaAPIClient.poll_vehicle_data_with_wake_option"
        ) as mock_vehicle,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.get_charging_schedule") as mock_sched,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.charge_start") as mock_charge_start,
        patch("ecolit.charging.tesla_api.TeslaAPIClient.set_charging_amps") as mock_set_amps,
    ):
        mock_api_instance = MagicMock()
        mock_api_class.return_value = mock_api_instance
        mock_solar.return_value = mock_solar_data
        mock_battery.return_value = mock_battery_data
        mock_vehicle.return_value = (mock_tesla_vehicle, False)
        mock_sched.return_value = {"charge_schedules": []}

        manager = EcoliteManager(test_config, dry_run=False)
        await manager._initialize_api()

        manager.solar_instance = {"ip": "192.168.0.2", "eojgc": 2, "eojcc": 121, "instance": 31}
        manager.battery_instance = {
            "ip": "192.168.0.2",
            "eojgc": 2,
            "eojcc": 125,
            "instance": 31,
            "capacity_kwh": 12.7,
        }

        from ecolit.devices import BatteryDevicePoller, SolarDevicePoller

        manager.solar_poller = SolarDevicePoller(manager.solar_instance, mock_api_instance)
        manager.battery_poller = BatteryDevicePoller(manager.battery_instance, mock_api_instance)

        await manager.tesla_client.start()
        await manager._poll_devices()

        # Verify NO charging commands due to disconnected cable
        mock_charge_start.assert_not_called()
        mock_set_amps.assert_not_called()
        # Schedule might not even be checked if cable disconnected is detected first

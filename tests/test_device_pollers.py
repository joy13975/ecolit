"""Critical integration tests for device polling - the stuff that actually matters."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ecolit.devices import BatteryDevicePoller


class TestBatterySOCParsing:
    """Test the critical SOC parsing logic that broke in production."""

    @pytest.mark.asyncio
    async def test_soc_parsing_real_echonet_scenario(self):
        """Test the EXACT scenario that caused the bug: 0xC9 returns '0/5000' but 0xE2 returns 5358."""
        # Setup device instance and mock API client
        device_instance = {"ip": "192.168.0.2", "eojgc": 0x02, "eojcc": 0x7D, "instance": 0x1F}

        api_client = MagicMock()
        api_client._state = {}

        # Create the poller
        poller = BatteryDevicePoller(device_instance, api_client)

        # Mock the battery device wrapper
        battery_device = MagicMock()

        # Mock the EXACT responses we saw in production
        async def mock_update(epc_code):
            responses = {
                0xBF: None,  # USER_DISPLAY_SOC returns None
                0xC9: "0/5000",  # DISPLAY_SOC_ALT returns misleading string
                0xE2: 5358,  # REMAINING_STORED_ELECTRICITY returns scaled value (53.58%)
            }
            return responses.get(epc_code)

        battery_device.update = AsyncMock(side_effect=mock_update)

        # Test the SOC reading
        soc = await poller._read_battery_soc(battery_device)

        # The critical assertion: should return 53.58% not 0%!
        assert soc == pytest.approx(53.58, 0.01), f"Expected 53.58%, got {soc}%"

    @pytest.mark.asyncio
    async def test_battery_polling_handles_timeout(self):
        """Test that polling continues despite timeouts and falls back to working EPC."""
        device_instance = {"ip": "192.168.0.2", "eojgc": 0x02, "eojcc": 0x7D, "instance": 0x1F}
        api_client = MagicMock()
        poller = BatteryDevicePoller(device_instance, api_client)

        battery_device = MagicMock()
        # Simulate timeout on first EPC, success on fallback
        call_count = 0

        async def mock_update(epc_code):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:  # First two EPCs timeout
                raise TimeoutError()
            return 5358  # Third EPC succeeds

        battery_device.update = AsyncMock(side_effect=mock_update)

        soc = await poller._read_battery_soc(battery_device)
        assert soc == pytest.approx(53.58, 0.01)

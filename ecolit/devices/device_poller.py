"""Device polling classes for ECHONET Lite devices."""

import asyncio
import logging
from typing import Any

from pychonet import HomeSolarPower, StorageBattery

from ..constants import BatteryEPC, CommonEPC, SolarEPC
from ..device_state_manager import DeviceStateManager
from ..realtime_soc import RealtimeSoCEstimator

logger = logging.getLogger(__name__)


class DevicePollerBase:
    """Base class for device polling with common error handling."""

    def __init__(self, device_instance: dict[str, Any], api_client: Any):
        """Initialize base poller.

        Args:
            device_instance: Device instance info with ip, eojgc, eojcc, instance
            api_client: ECHONET API client
        """
        self.device_instance = device_instance
        self.api_client = api_client
        self.ip = device_instance["ip"]
        self.eojgc = device_instance["eojgc"]
        self.eojcc = device_instance["eojcc"]
        self.instance = device_instance["instance"]
        self.device_state_manager = DeviceStateManager(api_client)

    async def _safe_property_read(
        self, device, epc_code: int, timeout: float = 3.0, description: str = ""
    ) -> Any | None:
        """Safely read a device property with timeout and error handling.

        Args:
            device: ECHONET device instance
            epc_code: EPC property code to read
            timeout: Timeout in seconds
            description: Human-readable description for logging

        Returns:
            Property value or None if failed
        """
        try:
            value = await asyncio.wait_for(device.update(epc_code), timeout=timeout)
            if value is not None:
                logger.debug(f"{description} (0x{epc_code:02X}): {value}")
            return value
        except TimeoutError:
            logger.debug(f"Timeout reading {description} (0x{epc_code:02X})")
            return None
        except Exception as e:
            logger.debug(f"Failed to read {description} (0x{epc_code:02X}): {e}")
            return None


class SolarDevicePoller(DevicePollerBase):
    """Handles polling of solar power generation devices."""

    async def poll_solar_data(self) -> dict[str, Any | None]:
        """Poll solar device for power generation data.

        Returns:
            Dictionary with solar_power and grid_power_flow values
        """
        logger.debug("Polling solar device...")
        result = {
            "solar_power": None,
            "grid_power_flow": None,
        }

        try:
            # Create solar device wrapper
            solar_device = HomeSolarPower(
                host=self.ip, api_connector=self.api_client, instance=self.instance
            )

            # Read property maps to populate available properties
            await asyncio.wait_for(solar_device.getAllPropertyMaps(), timeout=5.0)

            # Check available properties for debugging
            available_props = self.device_state_manager.get_available_properties(
                self.ip, self.eojgc, self.eojcc, self.instance
            )
            if available_props:
                logger.debug(
                    f"☀️ Solar properties available: {[f'0x{p:02X}' for p in available_props]}"
                )

                # Read the GET property map if available
                if self.device_state_manager.has_property(
                    self.ip, self.eojgc, self.eojcc, self.instance, CommonEPC.GET_PROPERTY_MAP
                ):
                    get_map = await self._safe_property_read(
                        solar_device,
                        CommonEPC.GET_PROPERTY_MAP,
                        description="Solar GET property map",
                    )
                    if isinstance(get_map, list | tuple):
                        logger.debug(
                            f"☀️ Solar supported properties: {[f'0x{p:02X}' for p in get_map if isinstance(p, int)]}"
                        )

            # Read instantaneous power generation
            power_val = await self._safe_property_read(
                solar_device,
                SolarEPC.INSTANTANEOUS_POWER_GENERATION,
                description="Solar instantaneous power",
            )
            if power_val is not None:
                result["solar_power"] = power_val
                logger.debug(f"Solar power reading successful: {power_val}W")
            else:
                # Try alternative EPC code
                power_val = await self._safe_property_read(
                    solar_device,
                    CommonEPC.INSTANTANEOUS_POWER,
                    description="Solar power (alternative)",
                )
                if power_val is not None:
                    result["solar_power"] = power_val

            # Read real-time grid power flow (+ import, - export)
            grid_flow_val = await self._safe_property_read(
                solar_device, SolarEPC.GRID_POWER_FLOW, description="Grid power flow"
            )
            if grid_flow_val is not None:
                result["grid_power_flow"] = grid_flow_val
                logger.debug(f"Grid power flow reading successful: {grid_flow_val}W")

            # Read cumulative generation for reference
            import_total = await self._safe_property_read(
                solar_device,
                SolarEPC.CUMULATIVE_POWER_GENERATION,
                description="Cumulative power generation",
            )
            if import_total is not None:
                # Convert large values to kWh for readability
                if import_total > 10000:
                    logger.debug(f"📊 Total grid import: {import_total / 1000:.1f}kWh")
                else:
                    logger.debug(f"📊 Total grid import: {import_total}Wh")

        except Exception as wrapper_error:
            logger.error(f"Solar device wrapper failed: {wrapper_error}, trying raw API...")

            # Fallback to raw API
            try:
                status_resp = await asyncio.wait_for(
                    self.api_client.echonetMessage(
                        self.ip,
                        self.eojgc,
                        self.eojcc,
                        self.instance,
                        0x62,
                        [{"EPC": CommonEPC.OPERATION_STATUS}],
                    ),
                    timeout=2.0,
                )
                if status_resp and CommonEPC.OPERATION_STATUS in status_resp:
                    logger.info("☀️ Solar: Status property available via raw API")
                else:
                    logger.info("☀️ Solar: No response to raw API status query")
            except Exception as e:
                logger.debug(f"Raw API fallback also failed: {e}")

        return result


class BatteryDevicePoller(DevicePollerBase):
    """Handles polling of storage battery devices."""

    def __init__(self, device_instance: dict[str, Any], api_client: Any):
        super().__init__(device_instance, api_client)
        self._technical_soc_warning_shown = False

        # Initialize real-time SoC estimator - MUST have capacity_kwh in config
        battery_capacity_kwh = device_instance.get("capacity_kwh")
        if battery_capacity_kwh is None:
            raise ValueError(
                f"Battery device '{device_instance.get('name', 'Unknown')}' missing required 'capacity_kwh' in config"
            )

        self.realtime_soc_estimator = RealtimeSoCEstimator(battery_capacity_kwh)

    async def poll_battery_data(self) -> dict[str, Any | None]:
        """Poll battery device for state and power data.

        Returns:
            Dictionary with battery_soc, battery_power, and battery_mode values
        """
        logger.debug("Polling battery device...")
        result = {
            "battery_soc": None,
            "battery_power": None,
            "battery_mode": None,
        }

        try:
            # Create battery device wrapper
            battery_device = StorageBattery(
                host=self.ip, api_connector=self.api_client, instance=self.instance
            )

            # Read property maps to populate available properties
            await asyncio.wait_for(battery_device.getAllPropertyMaps(), timeout=5.0)

            # Check available properties for debugging
            available_props = self.device_state_manager.get_available_properties(
                self.ip, self.eojgc, self.eojcc, self.instance
            )
            if available_props:
                logger.debug(
                    f"🔋 Battery properties available: {[f'0x{p:02X}' for p in available_props]}"
                )

                # Read the GET property map if available
                if self.device_state_manager.has_property(
                    self.ip, self.eojgc, self.eojcc, self.instance, CommonEPC.GET_PROPERTY_MAP
                ):
                    get_map = await self._safe_property_read(
                        battery_device,
                        CommonEPC.GET_PROPERTY_MAP,
                        description="Battery GET property map",
                    )
                    if isinstance(get_map, list | tuple):
                        logger.debug(
                            f"🔋 Battery supported properties: {[f'0x{p:02X}' for p in get_map if isinstance(p, int)]}"
                        )

            # Read SOC - prioritize display SOC, fall back to technical
            official_soc = await self._read_battery_soc(battery_device)
            result["battery_soc"] = official_soc

            # Read operation mode
            result["battery_mode"] = await self._read_battery_mode(battery_device)

            # Read battery power flow
            battery_power = await self._read_battery_power(battery_device)
            result["battery_power"] = battery_power

            # Update real-time SoC estimator
            if official_soc is not None:
                self.realtime_soc_estimator.update_official_soc(official_soc)

            if battery_power is not None:
                self.realtime_soc_estimator.update_power(battery_power)

            # Get real-time SoC estimate
            soc_estimate = self.realtime_soc_estimator.get_estimated_soc()
            result["realtime_soc"] = soc_estimate.estimated_soc
            result["soc_confidence"] = soc_estimate.confidence
            result["soc_source"] = soc_estimate.source

            # Add charging info
            charging_info = self.realtime_soc_estimator.get_charging_info()
            result["charging_rate_pct_per_hour"] = charging_info["charging_rate_percent_per_hour"]
            result["time_to_full_hours"] = charging_info["time_to_full_hours"]

            logger.debug(
                f"Battery data collected: SOC={result['battery_soc']}, Mode={result['battery_mode']}, Power={result['battery_power']}W"
            )

        except Exception as wrapper_error:
            logger.debug(f"Battery device wrapper failed: {wrapper_error}")

        return result

    async def _read_battery_soc(self, battery_device) -> float | None:
        """Read battery state of charge with preference for display SOC."""
        technical_soc = None
        display_soc = None

        # ORIGINAL working SoC candidates
        soc_candidates = [
            (BatteryEPC.USER_DISPLAY_SOC, "display"),  # User display SOC (preferred)
            (BatteryEPC.DISPLAY_SOC_ALT, "display"),  # Alternative user display SOC
            (
                BatteryEPC.REMAINING_STORED_ELECTRICITY,
                "technical",
            ),  # Technical SOC - known working
        ]

        for epc, soc_type in soc_candidates:
            soc_val = await self._safe_property_read(
                battery_device, epc, description=f"Battery {soc_type} SOC"
            )
            if soc_val is not None:
                # Handle different SOC value formats
                if isinstance(soc_val, str):
                    # Handle string formats like "0/5000" but skip if it results in 0
                    if "/" in soc_val:
                        try:
                            numerator, denominator = map(int, soc_val.split("/"))
                            soc_percentage = (
                                (numerator / denominator) * 100 if denominator > 0 else 0
                            )
                            # Skip "0/5000" type values - they're not useful
                            if soc_percentage == 0:
                                continue
                        except (ValueError, ZeroDivisionError):
                            continue
                    else:
                        try:
                            soc_percentage = float(soc_val)
                        except ValueError:
                            continue
                elif (
                    isinstance(soc_val, int | float)
                    and epc == BatteryEPC.REMAINING_STORED_ELECTRICITY
                ):
                    # REMAINING_STORED_ELECTRICITY is in Wh units
                    # Correct denominator = average of AC charging and discharging capacities
                    try:
                        a0_val = await self._safe_property_read(
                            battery_device, 0xA0, description="AC_CHARGING_CAPACITY"
                        )
                        a1_val = await self._safe_property_read(
                            battery_device, 0xA1, description="AC_DISCHARGING_CAPACITY"
                        )

                        if (
                            a0_val
                            and isinstance(a0_val, (int, float))
                            and a1_val
                            and isinstance(a1_val, (int, float))
                        ):
                            # Use average of charging and discharging capacities as denominator
                            effective_capacity_wh = (a0_val + a1_val) / 2
                            soc_percentage = (soc_val / effective_capacity_wh) * 100

                            # Clamp to reasonable range for safety
                            soc_percentage = max(0, min(100, soc_percentage))
                            logger.debug(
                                f"Battery SOC: {soc_val}Wh ÷ {effective_capacity_wh:.1f}Wh = {soc_percentage:.1f}%"
                            )
                        else:
                            # Fallback to configured capacity if EPC reads fail
                            if hasattr(self, "device_instance") and self.device_instance.get(
                                "capacity_kwh"
                            ):
                                fallback_capacity_wh = self.device_instance["capacity_kwh"] * 1000
                                soc_percentage = (soc_val / fallback_capacity_wh) * 100
                                soc_percentage = max(0, min(100, soc_percentage))
                                logger.debug(
                                    f"Battery SOC (fallback): {soc_val}Wh ÷ {fallback_capacity_wh}Wh = {soc_percentage:.1f}%"
                                )
                            else:
                                continue
                    except Exception as e:
                        logger.warning(f"Failed to read capacity EPCs for SOC calculation: {e}")
                        continue
                elif isinstance(soc_val, int | float) and soc_val > 100:
                    # Convert from technical units (0.01% increments) for other EPCs
                    soc_percentage = soc_val / 100
                else:
                    # Direct assignment for values 0-100 (display EPCs)
                    soc_percentage = soc_val

                logger.debug(f"Battery {soc_type} SOC (0x{epc:02X}): {soc_percentage:.1f}%")

                if soc_type == "display":
                    display_soc = soc_percentage
                    break  # Prefer display SOC, stop searching
                elif soc_type == "technical" and display_soc is None:
                    technical_soc = soc_percentage

        # Return the best SOC value available
        if display_soc is not None:
            return display_soc
        elif technical_soc is not None:
            # Only show this warning once
            if not self._technical_soc_warning_shown:
                logger.warning("⚠️  Using technical SOC - display SOC unavailable via ECHONET")
                self._technical_soc_warning_shown = True
            return technical_soc

        return None

    async def _read_battery_mode(self, battery_device) -> str | None:
        """Read battery operation mode."""
        mode_val = await self._safe_property_read(
            battery_device, BatteryEPC.OPERATION_MODE, description="Battery operation mode"
        )
        if mode_val is not None:
            # Handle both numeric and string mode values
            if isinstance(mode_val, str):
                return mode_val
            else:
                return StorageBattery.DICT_OPERATION_MODE.get(mode_val, f"Code({mode_val})")
        return None

    async def _read_battery_power(self, battery_device) -> int | None:
        """Read battery power flow (+ charging, - discharging)."""
        # Primary power reading: charging/discharging amount
        power_val = await self._safe_property_read(
            battery_device,
            BatteryEPC.CHARGING_DISCHARGING_AMOUNT,
            description="Battery charging/discharging amount",
        )
        if power_val is not None:
            logger.debug(f"Battery primary power flow (0xD3): {power_val}W")
            return power_val

        # Alternative: separate charging/discharging power readings
        charge_val = await self._safe_property_read(
            battery_device, BatteryEPC.CHARGING_POWER, description="Battery charging power"
        )
        discharge_val = await self._safe_property_read(
            battery_device, BatteryEPC.DISCHARGING_POWER, description="Battery discharging power"
        )

        if charge_val and charge_val > 0:
            return charge_val
        elif discharge_val and discharge_val > 0:
            return -discharge_val  # Make discharge negative
        else:
            return 0

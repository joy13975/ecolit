"""Tesla charging controller with intelligent wake-up and solar-surplus-based charging."""

import logging
import time
from typing import Any

from .tesla_api import TeslaAPIClient

logger = logging.getLogger(__name__)


class TeslaChargingController:
    """Intelligent Tesla charging controller with solar surplus detection and wake-up."""

    def __init__(self, tesla_client: TeslaAPIClient, config: dict[str, Any]):
        """Initialize Tesla charging controller."""
        self.tesla_client = tesla_client
        self.config = config.get("tesla", {})

        # Rate limiting for commands
        self.last_wake_attempt = 0
        self.last_charge_command = 0
        self.last_amps_command = 0

        # Minimum intervals between commands (seconds)
        self.wake_interval = 30  # Don't wake more than once per 30s
        self.charge_command_interval = 300  # Don't start/stop more than once per 5 minutes (conservative)
        self.amps_command_interval = 30  # Don't change amps more than once per 30s (keep responsive)

        # Success flag for current solar surplus event
        self._current_surplus_charging_started = False

        # Vehicle data cache to minimize API calls
        self._vehicle_data_cache = None
        self._vehicle_data_cache_time = 0
        self._vehicle_data_cache_ttl = 30  # 30 seconds - only refresh when needed

        # Local Tesla state tracking (synced every 10 minutes to reduce API calls)
        self._local_tesla_state = {
            "soc": None,
            "charging_state": None,
            "current_amps": None,
            "last_sync": 0,
        }
        self._tesla_sync_interval = 600  # 10 minutes between Tesla API syncs

        logger.info("Tesla charging controller initialized")

    async def execute_charging_control_with_wake(self, target_amps: int, battery_soc: float = None, solar_power: float = None, policy_name: str = None) -> dict[str, Any]:
        """Execute charging control WITH wake-up option for starting charging."""
        if not self.tesla_client.is_enabled():
            return {"success": False, "error": "Tesla API client not enabled"}

        result = {
            "success": False,
            "target_amps": target_amps,
            "actions_taken": [],
            "warnings": [],
            "errors": [],
        }

        try:
            # For starting charging, try to get data and wake if needed
            if target_amps > 0:
                logger.debug("Attempting to start charging - will wake vehicle if needed")
                vehicle_data, was_sleeping = await self.tesla_client.poll_vehicle_data_with_wake_option()

                if was_sleeping:
                    result["actions_taken"].append("Vehicle woken up")
                    # Wait a moment for vehicle to fully wake
                    await self._sleep(3)
                    # Get fresh data after wake-up
                    vehicle_data, _ = await self.tesla_client.poll_vehicle_data_with_wake_option()
            else:
                # For stopping, don't wake - just use cached data
                logger.debug("Stopping charging - no wake needed")
                vehicle_data = await self.tesla_client.get_vehicle_data()
                was_sleeping = False

            # Basic connection validation (always required)
            basic_validation = await self._validate_basic_charging_conditions(vehicle_data)
            if not basic_validation["can_charge"]:
                result["errors"].extend(basic_validation["errors"])
                if basic_validation["warnings"]:
                    result["warnings"].extend(basic_validation["warnings"])
                return result

            # Handle charging start
            charge_result = await self._ensure_charging_started(vehicle_data)
            if charge_result["action_taken"]:
                result["actions_taken"].append(charge_result["action_taken"])
            if charge_result["warnings"]:
                result["warnings"].extend(charge_result["warnings"])

            # Set target amperage
            current_amps = getattr(vehicle_data, "charge_amps", None)
            needs_change = True
            if current_amps is not None:
                amps_diff = abs(current_amps - target_amps)
                needs_change = amps_diff > 0.5

            if needs_change:
                amps_result = await self._set_charging_amps(target_amps)
                if amps_result["success"]:
                    result["actions_taken"].append(f"Set charging to {target_amps}A")
                    result["success"] = True
                    self._local_tesla_state["current_amps"] = target_amps
                else:
                    result["errors"].append(amps_result["error"])
            else:
                result["success"] = True
                result["actions_taken"].append(f"Already at {target_amps}A (no change needed)")

            return result

        except Exception as e:
            logger.error(f"Error in Tesla charging control with wake: {e}")
            result["errors"].append(f"Unexpected error: {e}")
            return result

    async def execute_charging_control(self, target_amps: int, battery_soc: float = None, solar_power: float = None, policy_name: str = None) -> dict[str, Any]:
        """Execute charging control WITHOUT wake-up (for stopping charging)."""
        if not self.tesla_client.is_enabled():
            return {"success": False, "error": "Tesla API client not enabled"}

        result = {
            "success": False,
            "target_amps": target_amps,
            "actions_taken": [],
            "warnings": [],
            "errors": [],
        }

        try:
            # For stopping charging, use local state or non-wake data only
            if target_amps == 0:
                # Use local state for charging state check to avoid wake-up
                current_charging_state = self._local_tesla_state.get("charging_state")

                if current_charging_state in ["Charging", "Starting"]:
                    stop_result = await self._stop_charging()
                    if stop_result["success"]:
                        result["actions_taken"].append("Stopped charging")
                        result["success"] = True
                        # Update local state immediately after successful stop command
                        self._local_tesla_state["charging_state"] = "Stopped"
                    else:
                        result["errors"].append(stop_result["error"])
                else:
                    result["success"] = True
                    result["actions_taken"].append("Already not charging")
            else:
                result["errors"].append("Use execute_charging_control_with_wake for starting charging")

        except Exception as e:
            logger.error(f"Error in Tesla charging control: {e}")
            result["errors"].append(f"Unexpected error: {e}")

        return result

    async def _get_cached_vehicle_data(self, force_refresh: bool = False, battery_soc: float = None, solar_power: float = None, policy_name: str = None) -> tuple[Any, bool]:
        """Get vehicle data with intelligent caching and 10-minute sync to minimize API calls.

        Args:
            force_refresh: Force a fresh API call even if cache is valid
            battery_soc: Home battery SOC percentage
            solar_power: Solar power generation in watts
            policy_name: EV charging policy name

        Returns:
            Tuple of (vehicle_data, was_sleeping)
        """
        current_time = time.time()

        # Check if we need to sync with Tesla (every 10 minutes or if no local state)
        should_sync = force_refresh or self._should_sync_tesla(battery_soc, solar_power, policy_name)

        # Use cache if valid and not forcing refresh and don't need sync
        # BUT always sync if cache is None (no valid data)
        if (
            not should_sync
            and self._vehicle_data_cache is not None
            and hasattr(self._vehicle_data_cache, 'charging_state')
            and self._vehicle_data_cache.charging_state is not None
            and (current_time - self._vehicle_data_cache_time) < self._vehicle_data_cache_ttl
        ):
            logger.debug("Using cached vehicle data to avoid API call")
            return self._vehicle_data_cache, False

        # Always make API call if we reach here (either should_sync or no valid cache)
        # Use non-wake method - only check if already awake
        logger.debug("Syncing vehicle data from Tesla API (no wake-up)")
        try:
            vehicle_data = await self.tesla_client.get_vehicle_data()
            was_sleeping = False
        except Exception as e:
            logger.debug(f"Tesla not accessible (likely sleeping): {e}")
            vehicle_data = None
            was_sleeping = True

        # Update local state with fresh data
        if vehicle_data and hasattr(vehicle_data, 'charging_state'):
            await self._sync_local_tesla_state(vehicle_data)

        # Cache the result
        self._vehicle_data_cache = vehicle_data
        self._vehicle_data_cache_time = current_time

        return vehicle_data, was_sleeping

    async def _handle_wake_up(self) -> dict[str, Any]:
        """Handle vehicle wake-up with rate limiting."""
        current_time = time.time()

        if current_time - self.last_wake_attempt < self.wake_interval:
            return {
                "success": False,
                "error": f"Rate limited: last wake attempt {current_time - self.last_wake_attempt:.0f}s ago",
            }

        self.last_wake_attempt = current_time

        try:
            success = await self.tesla_client.wake_up()
            if success:
                return {"success": True}
            else:
                return {"success": False, "error": "Wake-up command failed"}
        except Exception as e:
            return {"success": False, "error": f"Wake-up failed: {e}"}

    def _get_detailed_error_explanation(self, error: str) -> str:
        """Get detailed explanation for common charging errors."""
        error_explanations = {
            "🔌 Charger not connected": (
                "The vehicle reports 'Disconnected' state. If the cable is actually connected, "
                "try waking the vehicle in the Tesla app or wait a moment for the connection to be detected."
            ),
            "Wake-up command failed": (
                "Unable to wake up the Tesla vehicle. This might be due to poor cellular connectivity "
                "or the vehicle being in deep sleep mode. Try again in a few minutes."
            ),
            "Failed to start charging": (
                "Unable to start charging. Common causes include: charge limit reached, "
                "charger not connected, or vehicle in a state that prevents charging."
            ),
            "Failed to set charging amps": (
                "Unable to adjust charging amperage. This could be due to vehicle state, "
                "hardware limitations, or temporary API issues."
            ),
        }

        for key, explanation in error_explanations.items():
            if key in error:
                return explanation

        return "An unexpected error occurred during charging control."

    async def _validate_basic_charging_conditions(self, vehicle_data) -> dict[str, Any]:
        """Validate basic charging conditions (connection, etc.) - always required."""
        result = {"can_charge": False, "errors": [], "warnings": []}

        try:
            # Check if we have vehicle data
            if not vehicle_data or not hasattr(vehicle_data, 'charging_state'):
                result["errors"].append("🚗 Tesla vehicle data unavailable")
                return result

            # Check if charger is connected based on charging_state
            # "Disconnected" means no cable connected
            # "Stopped" means plugged in but not charging
            # "Charging" means actively charging
            if vehicle_data.charging_state in ["Disconnected", None]:
                result["errors"].append("🔌 Charger not connected")
                return result

            result["can_charge"] = True

        except Exception as e:
            logger.error(f"Error validating basic charging conditions: {e}")
            result["errors"].append("🚗 Tesla vehicle not responding")

        return result

    def reset_surplus_event(self):
        """Reset the success flag when solar surplus disappears."""
        self._current_surplus_charging_started = False
        logger.debug("Reset surplus event - ready for next solar surplus")

    def has_started_charging_this_surplus(self) -> bool:
        """Check if we've successfully started charging during current surplus event."""
        return self._current_surplus_charging_started

    def mark_surplus_charging_started(self):
        """Mark that we've successfully started charging during current surplus event."""
        self._current_surplus_charging_started = True
        logger.debug("Marked surplus charging as started")

    async def _ensure_charging_started(self, vehicle_data) -> dict[str, Any]:
        """Ensure charging is started if not already charging."""
        result = {"action_taken": None, "warnings": []}

        charging_states_that_need_start = ["Stopped", "Complete", "Disconnected"]

        # Use fresh data from vehicle_data since we should have just synced if needed
        current_charging_state = vehicle_data.charging_state if vehicle_data else self._local_tesla_state.get("charging_state")

        if current_charging_state in charging_states_that_need_start:
            current_time = time.time()

            if current_time - self.last_charge_command < self.charge_command_interval:
                result["warnings"].append(
                    f"Rate limited: last charge command {current_time - self.last_charge_command:.0f}s ago"
                )
                return result

            try:
                self.last_charge_command = current_time
                success = await self.tesla_client.charge_start()
                if success:
                    result["action_taken"] = "Started charging"
                    # Update local state immediately after successful start command
                    self._local_tesla_state["charging_state"] = "Charging"
                    # Mark that we've successfully started charging during this surplus event
                    self.mark_surplus_charging_started()
                else:
                    result["warnings"].append("Failed to start charging")
            except Exception as e:
                result["warnings"].append(f"Error starting charging: {e}")

        return result

    async def _set_charging_amps(self, target_amps: int) -> dict[str, Any]:
        """Set charging amperage with rate limiting."""
        current_time = time.time()

        if current_time - self.last_amps_command < self.amps_command_interval:
            return {
                "success": False,
                "error": f"Rate limited: last amps command {current_time - self.last_amps_command:.0f}s ago",
            }

        try:
            self.last_amps_command = current_time
            success = await self.tesla_client.set_charging_amps(target_amps)
            if success:
                return {"success": True}
            else:
                return {"success": False, "error": "Failed to set charging amps"}
        except Exception as e:
            return {"success": False, "error": f"Error setting amps: {e}"}

    async def _stop_charging(self) -> dict[str, Any]:
        """Stop charging with rate limiting."""
        current_time = time.time()

        if current_time - self.last_charge_command < self.charge_command_interval:
            return {
                "success": False,
                "error": f"Rate limited: last charge command {current_time - self.last_charge_command:.0f}s ago",
            }

        try:
            self.last_charge_command = current_time
            success = await self.tesla_client.charge_stop()
            if success:
                return {"success": True}
            else:
                return {"success": False, "error": "Failed to stop charging"}
        except Exception as e:
            return {"success": False, "error": f"Error stopping charging: {e}"}

    async def _sleep(self, seconds: float):
        """Sleep with asyncio."""
        import asyncio

        await asyncio.sleep(seconds)

    def _is_charging_window_open(self, battery_soc: float, solar_power: float, policy_name: str) -> bool:
        """Determine if charging window is open based on policy and energy conditions."""
        if policy_name == "ECO":
            return battery_soc is not None and battery_soc > 95.0
        elif policy_name == "SOLAR":
            return solar_power is not None and solar_power > 1000
        elif policy_name == "FORCE":
            return True
        else:
            return False

    def _should_sync_tesla(self, battery_soc: float = None, solar_power: float = None, policy_name: str = None) -> bool:
        """Check if we should sync with Tesla API (every 10 minutes + charging window check)."""
        import time

        current_time = time.time()
        time_since_sync = current_time - self._local_tesla_state["last_sync"]

        # Always sync if it's been more than 10 minutes or we have no local state
        if (time_since_sync >= self._tesla_sync_interval or
                self._local_tesla_state["current_amps"] is None):
            return True

        # Only sync if charging window is open to minimize unnecessary API calls
        if policy_name:
            return self._is_charging_window_open(battery_soc, solar_power, policy_name)
        else:
            # If no policy provided, fallback to always sync (conservative)
            return True

    async def _sync_local_tesla_state(self, vehicle_data) -> None:
        """Update local Tesla state to reduce API calls.

        Args:
            vehicle_data: Fresh Tesla vehicle data from API
        """
        import time

        if vehicle_data:
            self._local_tesla_state.update({
                "soc": vehicle_data.battery_level,
                "charging_state": vehicle_data.charging_state,
                "current_amps": getattr(vehicle_data, "charge_amps", None),
                "last_sync": time.time(),
            })
            logger.debug(f"Local Tesla state synced: SOC={vehicle_data.battery_level}%, "
                        f"State={vehicle_data.charging_state}, Amps={self._local_tesla_state['current_amps']}")

    def _get_local_current_amps(self) -> float | None:
        """Get current amps from local state (avoiding API call)."""
        return self._local_tesla_state.get("current_amps")

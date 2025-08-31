"""Tesla charging controller with intelligent wake-up and schedule management."""

import logging
import time
from typing import Any

from .tesla_api import TeslaAPIClient

logger = logging.getLogger(__name__)


class TeslaChargingController:
    """Intelligent Tesla charging controller with schedule validation and wake-up."""

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

        # Tesla schedule cache - schedule data changes rarely
        self._schedule_cache = {}
        self._schedule_cache_time = 0
        self._schedule_cache_ttl = 900  # 15 minutes (was 5 minutes)

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

    async def execute_charging_control(self, target_amps: int, battery_soc: float = None, solar_power: float = None, policy_name: str = None) -> dict[str, Any]:
        """Execute charging control with intelligent wake-up and validation.

        Args:
            target_amps: Target charging amperage (0 to stop)
            battery_soc: Home battery SOC percentage
            solar_power: Solar power generation in watts
            policy_name: EV charging policy name

        Returns:
            Dict with status information and any error messages
        """
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
            # Step 1: Get vehicle data with caching to minimize API calls
            vehicle_data, was_sleeping = await self._get_cached_vehicle_data(battery_soc=battery_soc, solar_power=solar_power, policy_name=policy_name)

            if was_sleeping:
                wake_result = await self._handle_wake_up()
                if not wake_result["success"]:
                    result["errors"].append(wake_result["error"])
                    return result
                result["actions_taken"].append("Vehicle woken up")

                # Wait a moment for vehicle to fully wake
                await self._sleep(3)

                # Get fresh data after wake-up (force refresh)
                vehicle_data, _ = await self._get_cached_vehicle_data(force_refresh=True, battery_soc=battery_soc, solar_power=solar_power, policy_name=policy_name)

            # Step 2: Basic connection validation (always required)
            basic_validation = await self._validate_basic_charging_conditions(vehicle_data)
            if not basic_validation["can_charge"]:
                result["errors"].extend(basic_validation["errors"])
                if basic_validation["warnings"]:
                    result["warnings"].extend(basic_validation["warnings"])
                return result

            # Step 3: Handle charging state
            if target_amps > 0:
                # Only validate schedule when trying to start/increase charging
                schedule_validation = await self._validate_schedule_conditions(vehicle_data)
                if not schedule_validation["can_charge"]:
                    result["errors"].extend(schedule_validation["errors"])
                    if schedule_validation["warnings"]:
                        result["warnings"].extend(schedule_validation["warnings"])
                    return result
                # We want to charge
                charge_result = await self._ensure_charging_started(vehicle_data)
                if charge_result["action_taken"]:
                    result["actions_taken"].append(charge_result["action_taken"])
                if charge_result["warnings"]:
                    result["warnings"].extend(charge_result["warnings"])

                # Set target amperage (only if different from current)
                # Use local state for quick checks, fresh data for actual commands
                if self._should_sync_tesla(battery_soc, solar_power, policy_name):
                    # We just synced, use fresh data from vehicle_data
                    current_amps = getattr(vehicle_data, "charge_amps", None)
                else:
                    # Use local state to avoid API call for "already at X" checks
                    current_amps = self._get_local_current_amps()

                # Compare with tolerance since current_amps may be float, target_amps is int
                needs_change = True
                if current_amps is not None:
                    # Allow 0.5A tolerance (Tesla reports actual measured amps, not requested)
                    amps_diff = abs(current_amps - target_amps)
                    needs_change = amps_diff > 0.5

                if needs_change:
                    amps_result = await self._set_charging_amps(target_amps)
                    if amps_result["success"]:
                        result["actions_taken"].append(f"Set charging to {target_amps}A")
                        result["success"] = True
                        # Update local state immediately after successful command
                        self._local_tesla_state["current_amps"] = target_amps
                    else:
                        result["errors"].append(amps_result["error"])
                else:
                    result["success"] = True
                    result["actions_taken"].append(f"Already at {target_amps}A (no change needed)")
            else:
                # Target amps is 0, we want to stop
                # Use local state for charging state to avoid unnecessary API calls
                if self._should_sync_tesla(battery_soc, solar_power, policy_name):
                    # We just synced, use fresh data from vehicle_data
                    current_charging_state = vehicle_data.charging_state
                else:
                    # Use local state for charging state check
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
        if (
            not should_sync
            and self._vehicle_data_cache is not None
            and (current_time - self._vehicle_data_cache_time) < self._vehicle_data_cache_ttl
        ):
            logger.debug("Using cached vehicle data to avoid API call")
            return self._vehicle_data_cache, False

        # Only make API call if we need to sync (every 10 minutes) or cache expired
        if should_sync:
            logger.debug("Syncing vehicle data from Tesla API (10-minute interval)")
            vehicle_data, was_sleeping = await self.tesla_client.poll_vehicle_data_with_wake_option()

            # Update local state with fresh data
            await self._sync_local_tesla_state(vehicle_data)
        else:
            logger.debug("Using cached vehicle data within 10-minute window")
            vehicle_data = self._vehicle_data_cache
            was_sleeping = False

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
            "🕒 Outside Tesla charging schedule": (
                "The vehicle's scheduled charging window is not currently active. "
                "Check your Tesla app's charging schedule settings or charge immediately if needed."
            ),
            "🏠 Wall Connector schedule restriction": (
                "The Wall Connector has schedule restrictions that prevent charging at this time. "
                "Check your Wall Connector's schedule settings in the Tesla app."
            ),
            "Wake-up command failed": (
                "Unable to wake up the Tesla vehicle. This might be due to poor cellular connectivity "
                "or the vehicle being in deep sleep mode. Try again in a few minutes."
            ),
            "Failed to start charging": (
                "Unable to start charging. Common causes include: charge limit reached, "
                "charging schedule restrictions, or vehicle in a state that prevents charging."
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
            result["errors"].append(f"Basic validation error: {e}")

        return result

    async def _validate_schedule_conditions(self, vehicle_data) -> dict[str, Any]:
        """Validate schedule conditions - only for starting/increasing charging."""
        result = {"can_charge": False, "errors": [], "warnings": []}

        try:
            # Get Tesla charging schedule
            schedule_data = await self._get_cached_schedule()
            schedule_result = self._check_tesla_schedule(schedule_data)

            if not schedule_result["in_schedule"]:
                result["errors"].append(
                    f"🕒 Outside Tesla charging schedule: {schedule_result['reason']}"
                )
                return result

            # Check wall connector schedule
            wall_connector_result = await self._check_wall_connector_schedule()
            if not wall_connector_result["allowed"]:
                result["errors"].append(
                    f"🏠 Wall Connector schedule restriction: {wall_connector_result['reason']}"
                )
                return result

            result["can_charge"] = True

        except Exception as e:
            logger.error(f"Error validating schedule conditions: {e}")
            result["errors"].append(f"Schedule validation error: {e}")

        return result

    async def _get_cached_schedule(self) -> dict[str, Any]:
        """Get Tesla charging schedule with caching."""
        current_time = time.time()

        if (current_time - self._schedule_cache_time) < self._schedule_cache_ttl:
            return self._schedule_cache

        try:
            schedule_data = await self.tesla_client.get_charging_schedule()
            self._schedule_cache = schedule_data
            self._schedule_cache_time = current_time
            return schedule_data
        except Exception as e:
            logger.warning(f"Failed to get charging schedule: {e}")
            return {}

    def _check_tesla_schedule(self, schedule_data: dict[str, Any]) -> dict[str, Any]:
        """Check if current time is within Tesla charging schedule."""
        result = {"in_schedule": True, "reason": "No schedule restrictions"}

        if not schedule_data:
            return result

        # Handle sleeping vehicle
        if schedule_data.get("status") == "vehicle_sleeping":
            result["reason"] = "Schedule check requires awake vehicle"
            return result

        # Check for active schedules
        schedules = schedule_data.get("charge_schedules", [])
        active_schedules = [s for s in schedules if s.get("enabled", False)]

        if not active_schedules:
            # No schedules configured, charging is always allowed
            return result

        # Get current time info
        import datetime

        now = datetime.datetime.now()
        current_weekday = now.weekday()  # Monday=0, Sunday=6
        current_minutes = now.hour * 60 + now.minute

        # Check if current time matches any active schedule
        for schedule in active_schedules:
            # Check days of week (Tesla uses different encoding: Sunday=0, Monday=1, etc.)
            days_of_week = schedule.get("days_of_week", 0)

            # Convert Python weekday to Tesla weekday
            tesla_weekday = (current_weekday + 1) % 7  # Convert Mon=0 to Sun=0, Mon=1, etc.

            # Check if today is enabled in this schedule
            if not (days_of_week & (1 << tesla_weekday)):
                continue

            # Check time window
            start_time = schedule.get("start_time", 0)  # Minutes since midnight
            end_time = schedule.get("end_time", 0)  # Minutes since midnight

            # Handle schedules that cross midnight
            if start_time <= end_time:
                # Normal case: start_time < end_time (e.g., 9:00-17:00)
                if start_time <= current_minutes <= end_time:
                    result["reason"] = (
                        f"Within schedule: {start_time // 60:02d}:{start_time % 60:02d}-{end_time // 60:02d}:{end_time % 60:02d}"
                    )
                    return result
            else:
                # Crosses midnight: start_time > end_time (e.g., 23:00-06:00)
                if current_minutes >= start_time or current_minutes <= end_time:
                    result["reason"] = (
                        f"Within overnight schedule: {start_time // 60:02d}:{start_time % 60:02d}-{end_time // 60:02d}:{end_time % 60:02d}"
                    )
                    return result

        # If we get here, current time is not within any active schedule
        result["in_schedule"] = False
        result["reason"] = f"Outside all {len(active_schedules)} configured schedule(s)"

        return result

    async def _check_wall_connector_schedule(self) -> dict[str, Any]:
        """Check if wall connector allows charging based on its schedule."""
        result = {"allowed": True, "reason": "No wall connector schedule restrictions"}

        # For now, we'll assume wall connector allows charging unless we have specific data
        # In the future, this could check wall connector specific schedules
        # or integration with home energy management schedules

        # Basic implementation: Check if wall connector is available
        # This is a placeholder for more sophisticated schedule checking

        return result

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
